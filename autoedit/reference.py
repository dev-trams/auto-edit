"""레퍼런스 영상으로 스타일 메모 만들기: 내려받기 -> 수치·장면 분석 -> 메모 작성.

레퍼런스는 스타일을 배우는 데만 쓴다. 편집본에는 레퍼런스의 화면·소리·문구를 넣지 않는다
(편집은 '원본 영상' 폴더만 읽는다).
"""
import base64
import datetime
import hashlib
import json
import re
import statistics
from pathlib import Path

from . import config, ff

MAX_SECONDS = 600  # 영상당 앞 10분만 분석
FRAMES = 12


def list_files() -> list:
    ref = config.dirs()["ref"]
    return sorted(p for p in ref.iterdir() if p.is_file() and p.suffix.lower() in config.VIDEO_EXT)


def download(urls: list, progress=None) -> dict:
    """링크의 영상을 '레퍼런스' 폴더에 내려받는다. 이미 받은 영상은 건너뛴다."""
    say = progress or (lambda m, p=None: None)
    try:
        import yt_dlp
    except ImportError:
        raise ValueError("yt-dlp가 설치되지 않았습니다. 영상 파일을 '레퍼런스' 폴더에 직접 넣어 주세요.")
    ref = config.dirs()["ref"]
    saved, failed = [], []
    for i, url in enumerate(urls, 1):
        say(f"레퍼런스 내려받는 중 {i}/{len(urls)}", 0.3 * (i - 1) / len(urls))
        opts = {
            "format": "bv*[height<=720]+ba/b[height<=720]/b",
            "merge_output_format": "mp4",
            "outtmpl": str(ref / "%(title).80s [%(id)s].%(ext)s"),
            "noplaylist": True, "quiet": True, "no_warnings": True, "noprogress": True,
            "ffmpeg_location": str(Path(ff.ffmpeg()).parent),
            "windowsfilenames": True,
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                saved.append(Path(ydl.prepare_filename(info)).with_suffix(".mp4").name)
        except Exception as exc:  # 한 링크가 실패해도 나머지는 계속한다
            msg = re.sub(r"\x1b\[[0-9;]*m", "", str(exc)).replace("ERROR: ", "")
            failed.append({"url": url, "error": msg[:200]})
    return {"saved": saved, "failed": failed}


def _cuts(path: Path, limit: float) -> list:
    proc = ff.run(["-t", limit, "-i", path, "-an", "-vf",
                   "fps=10,scale=320:-2,select='gt(scene,0.3)',metadata=print:file=-", "-f", "null", "-"], check=False)
    times = [float(x) for x in re.findall(r"pts_time:([\d.]+)", proc.stdout + proc.stderr)]
    merged = []
    for t in times:
        if not merged or t - merged[-1] > 0.5:
            merged.append(t)
    return merged


def analyze_one(path: Path, progress=None) -> dict:
    say = progress or (lambda m: None)
    st = path.stat()
    key = hashlib.sha1(f"{path.resolve()}|{st.st_size}|{int(st.st_mtime)}|r2".encode("utf-8")).hexdigest()[:16]
    cache = config.WORK_DIR / "reference" / f"{key}.json"
    if cache.is_file():
        return json.loads(cache.read_text(encoding="utf-8"))
    cache.parent.mkdir(parents=True, exist_ok=True)
    info = ff.probe(path)
    span = min(info["duration"], MAX_SECONDS)
    data = {"file": path.name, "key": key, "duration": round(info["duration"], 1), "analyzed_seconds": round(span, 1),
            "width": info["width"], "height": info["height"], "fps": info["fps"]}
    say(f"{path.name}: 컷 길이 측정")
    cuts = _cuts(path, span) if info["has_video"] else []
    bounds = [0.0] + cuts + [span]
    lengths = sorted(b - a for a, b in zip(bounds, bounds[1:]) if b - a > 0)
    n = len(lengths)
    data["cuts"] = {
        "count": len(cuts), "per_minute": round(len(cuts) / span * 60, 1) if span else 0,
        "median_sec": round(statistics.median(lengths), 1) if lengths else 0,
        "under_2s_pct": round(100 * sum(1 for x in lengths if x < 2) / n) if n else 0,
        "from_2_to_5s_pct": round(100 * sum(1 for x in lengths if 2 <= x < 5) / n) if n else 0,
        "over_5s_pct": round(100 * sum(1 for x in lengths if x >= 5) / n) if n else 0,
    }
    data["audio"] = {}
    if info["has_audio"]:
        say(f"{path.name}: 소리 측정")
        proc = ff.run(["-t", span, "-i", path, "-vn", "-af", "ebur128", "-f", "null", "-"], check=False)
        tail = proc.stderr[proc.stderr.rfind("Summary:"):]
        m1, m2 = re.search(r"I:\s+(-?[\d.]+) LUFS", tail), re.search(r"LRA:\s+(-?[\d.]+) LU", tail)
        proc = ff.run(["-t", span, "-i", path, "-vn", "-af", "silencedetect=noise=-35dB:d=0.3", "-f", "null", "-"], check=False)
        silent = sum(float(x) for x in re.findall(r"silence_duration: ([\d.]+)", proc.stderr))
        data["audio"] = {"lufs": float(m1.group(1)) if m1 else None, "range_lu": float(m2.group(1)) if m2 else None,
                         "silence_pct": round(100 * silent / span, 1) if span else 0}
    say(f"{path.name}: 장면 추출")
    frames = []
    out_dir = config.WORK_DIR / "thumbs"
    out_dir.mkdir(parents=True, exist_ok=True)
    for i in range(FRAMES if info["has_video"] else 0):
        t = round(span * (i + 0.5) / FRAMES, 1)
        name = f"ref_{key}_{i:02d}.jpg"
        if not (out_dir / name).is_file():
            ff.run(["-ss", t, "-i", path, "-frames:v", "1", "-vf", "scale=-2:360", "-q:v", "4", out_dir / name], check=False)
        if (out_dir / name).is_file():
            frames.append({"t": t, "file": name})
    data["frames"] = frames
    cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def _stats_table(items: list) -> str:
    rows = ["| 영상 | 길이 | 화면 | 분당 컷 | 컷 길이 중앙값 | 2초 미만 / 2~5초 / 5초 이상 | 전체 음량 | 음량 변화폭 | 무음 비율 |",
            "|---|---|---|---|---|---|---|---|---|"]
    for a in items:
        c, au = a["cuts"], a.get("audio") or {}
        mins = f"{int(a['duration'] // 60)}분 {int(a['duration'] % 60)}초"
        rows.append(f"| {a['file'][:40]} | {mins} | {a['width']}x{a['height']} | {c['per_minute']}회 | {c['median_sec']}초 | "
                    f"{c['under_2s_pct']}% / {c['from_2_to_5s_pct']}% / {c['over_5s_pct']}% | "
                    f"{au.get('lufs', '-')} LUFS | {au.get('range_lu', '-')} | {au.get('silence_pct', '-')}% |")
    return "\n".join(rows)


LIMITS = ("- 컷은 장면 전환 검출(임계값 0.3)로 셌다. 같은 구도 안의 점프컷은 잡히지 않아 실제 컷은 더 많을 수 있다.\n"
          "- 소리는 말소리와 배경음을 분리하지 않고 전체를 잰 값이다. 배경음의 분위기는 판단하지 않았다.\n"
          f"- 영상마다 앞 {MAX_SECONDS // 60}분까지만 분석했다.\n"
          "- 레퍼런스는 스타일 참고용이다. 화면·음악·문구를 편집본에 넣지 않는다.")

MEMO_PROMPT = """아래는 사용자가 고른 레퍼런스 영상들의 측정값과 대표 장면입니다. 이 영상들의 편집 스타일을 정리한
'스타일 메모'를 한국어 마크다운으로 써 주세요. 이 메모는 이후 자동 편집의 기준으로 쓰입니다.

지킬 것:
- 측정값(컷, 음량)은 주어진 숫자를 그대로 쓰고 지어내지 마세요. 표는 이미 만들어져 있으니 다시 만들지 않아도 됩니다.
- 장면 이미지에서 보이는 것만 적으세요: 자막 위치·크기(화면 높이 대비 대략 %)·색·글꼴 느낌·외곽선/박스, 강조 방식,
  화면 전환, 그래픽 사용, 첫 장면 구성. 확인할 수 없는 것은 "확인 못 함"이라고 쓰세요.
- 배경음 분위기는 들을 수 없으므로 판단하지 마세요.
- 레퍼런스의 문구나 화면을 베끼라는 내용은 넣지 마세요. 스타일만 설명합니다.
- 자막은 아래 세 유형 중 가장 가까운 것을 고르고, 메모 안에 정확히 이 형식의 줄을 한 번 넣으세요:
  `기본 자막 유형: A형` (또는 B형, C형)
  A형 = 얇은 명조, 박스 없이 옅은 그림자 / B형 = 굵은 고딕 + 검정 외곽선, 강조는 빨강 / C형 = 굵은 고딕 + 반투명 회색 박스
- 구성: "## 레퍼런스별 특징", "## 공통점", "## 편집 기본값" (컷, 자막, 소리 순서). 제목(#)과 측정 표, 한계는 쓰지 마세요."""


def _claude_memo(items: list) -> str:
    from . import planner
    content = []
    for a in items:
        for fr in a["frames"]:
            path = config.WORK_DIR / "thumbs" / fr["file"]
            if path.is_file():
                content.append({"type": "text", "text": f"{a['file']} · {fr['t']}초"})
                content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                            "data": base64.standard_b64encode(path.read_bytes()).decode("ascii")}})
    stats = [{k: a[k] for k in ("file", "duration", "width", "height", "cuts", "audio")} for a in items]
    content.append({"type": "text", "text": MEMO_PROMPT + "\n\n## 측정값\n" + json.dumps(stats, ensure_ascii=False)})
    resp = planner._client().messages.create(model=config.load()["model_plan"], max_tokens=8000,
                                             messages=[{"role": "user", "content": content}])
    text = "\n".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    if not text:
        raise RuntimeError("Claude가 스타일 메모를 돌려주지 않았습니다.")
    return text


def _rule_memo(items: list) -> str:
    med = statistics.median(a["cuts"]["median_sec"] for a in items)
    lufs = [a["audio"]["lufs"] for a in items if (a.get("audio") or {}).get("lufs") is not None]
    sil = [a["audio"]["silence_pct"] for a in items if a.get("audio")]
    lines = ["## 공통점 (측정값 기준)",
             f"- 컷 길이 중앙값은 영상별로 {min(a['cuts']['median_sec'] for a in items)}~{max(a['cuts']['median_sec'] for a in items)}초 (가운데 값 {med}초).",
             f"- 0.3초 이상 무음은 전체의 {min(sil)}~{max(sil)}%." if sil else "- 소리 정보 없음.",
             f"- 전체 음량은 {min(lufs)} ~ {max(lufs)} LUFS." if lufs else "",
             "", "## 편집 기본값",
             "- 컷: 무음(-35dB, 0.4초 이상)을 자르고 말 앞 0.10초·뒤 0.15초를 남긴다. 전환 효과 없이 하드 컷.",
             f"- 소리: 말소리 {max(-20, min(-14, round(statistics.mean(lufs)))) if lufs else -16} LUFS 안팎, 배경음은 그보다 18dB 작게.",
             "- 자막: 장면을 직접 보지 못해 유형을 고르지 못했다. 아래 줄의 A/B/C를 직접 고쳐 주세요.",
             "", "기본 자막 유형: C형", "",
             "> Claude API 키가 없어 숫자만으로 만든 메모입니다. 자막 위치·색·글꼴, 그래픽 사용은 분석하지 못했습니다."]
    return "\n".join(x for x in lines if x is not None)


def learn(progress=None) -> dict:
    """'레퍼런스' 폴더의 영상을 분석해 스타일 메모를 새로 쓴다. 기존 메모는 날짜를 붙여 보관한다."""
    say = progress or (lambda m, p=None: None)
    files = list_files()
    if not files:
        raise ValueError("'레퍼런스' 폴더에 영상이 없습니다. 링크를 넣거나 파일을 직접 넣어 주세요.")
    items = []
    for i, path in enumerate(files):
        items.append(analyze_one(path, lambda m, i=i: say(m, 0.3 + 0.5 * i / len(files))))
    say("스타일 메모 쓰는 중", 0.85)
    used_ai = bool(config.api_key())
    body = _claude_memo(items) if used_ai else _rule_memo(items)
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    memo = "\n\n".join([
        "# 스타일 메모",
        f"작성: {today} · 기준: 레퍼런스 영상 {len(items)}개 · " + ("Claude가 장면과 측정값을 보고 작성" if used_ai else "측정값만으로 작성"),
        "## 측정값\n" + _stats_table(items), body.strip(), "## 분석 한계\n" + LIMITS, "## 수정 이력\n- (아직 없음)"]) + "\n"
    path = save_memo(memo)
    return {"memo": memo, "path": str(path), "used_ai": used_ai, "files": [a["file"] for a in items]}


def save_memo(text: str) -> Path:
    path = config.workspace() / "스타일 메모.md"
    if path.is_file():
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"스타일 메모_이전_{stamp}.md")
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(text, encoding="utf-8")
    return path
