"""촬영본 분석: 기본 정보, 무음 구간, 음량, 대표 장면, 받아쓰기."""
import hashlib
import json
import re
from pathlib import Path

from . import config, ff, media, transcribe

SILENCE_DB = -35
SILENCE_MIN = 0.4
ANALYSIS_VERSION = 5


def _key(path: Path) -> str:
    st = path.stat()
    raw = f"{path.resolve()}|{st.st_size}|{int(st.st_mtime)}|{ANALYSIS_VERSION}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def silences(path: Path, duration: float) -> list:
    proc = ff.run(["-i", path, "-vn", "-af", f"silencedetect=noise={SILENCE_DB}dB:d={SILENCE_MIN}", "-f", "null", "-"],
                  check=False)
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", proc.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", proc.stderr)]
    out = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else duration
        out.append([round(max(0.0, s), 2), round(min(duration, e), 2)])
    return out


def loudness(path: Path) -> float:
    """통합 음량(LUFS). 소리가 없으면 -70."""
    proc = ff.run(["-i", path, "-vn", "-af", "ebur128", "-f", "null", "-"], check=False)
    tail = proc.stderr[proc.stderr.rfind("Summary:"):]
    m = re.search(r"I:\s+(-?[\d.]+) LUFS", tail)
    return float(m.group(1)) if m else -70.0


def thumbnails(path: Path, duration: float, out_dir: Path, key: str) -> list:
    out_dir.mkdir(parents=True, exist_ok=True)
    count = int(min(12, max(4, duration // 2)))
    thumbs = []
    for i in range(count):
        t = round(duration * (i + 0.5) / count, 2)
        name = f"{key}_{i:02d}.jpg"
        if not (out_dir / name).is_file():
            ff.run(["-ss", t, "-i", path, "-frames:v", "1", "-vf", "scale=-2:320", "-q:v", "5", out_dir / name],
                   check=False)
        if (out_dir / name).is_file():
            thumbs.append({"t": t, "file": name})
    return thumbs


def analyze_clip(path: Path, do_transcribe: bool = True, progress=None) -> dict:
    path = Path(path)
    cache_dir = config.WORK_DIR / "analysis"
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = _key(path)
    cache = cache_dir / f"{key}.json"
    if cache.is_file():
        data = json.loads(cache.read_text(encoding="utf-8"))
        if data.get("transcribed") or not do_transcribe:
            return data
    say = progress or (lambda m: None)
    say(f"{path.name}: 기본 정보")
    info = media.info(path)
    if info["is_image"]:
        thumb_dir = config.WORK_DIR / "thumbs"
        thumb_dir.mkdir(parents=True, exist_ok=True)
        name = f"{key}_00.jpg"
        if not (thumb_dir / name).is_file():
            ff.run(["-i", media.prepare_image(path), "-frames:v", "1", "-vf", "scale=-2:320", "-q:v", "5", thumb_dir / name], check=False)
        data = {"file": path.name, "key": key, **info, "silences": [], "lufs": -70.0, "speech": [], "transcribed": True,
                "transcribe_error": "", "silence_ratio": 0.0, "speech_ratio": 0.0,
                "thumbs": [{"t": 0, "file": name}] if (thumb_dir / name).is_file() else []}
        cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data
    data = {"file": path.name, "key": key, **info, "silences": [], "lufs": -70.0, "speech": [],
            "transcribed": False, "transcribe_error": ""}
    if info["has_audio"]:
        say(f"{path.name}: 무음·음량 측정")
        data["silences"] = silences(path, info["duration"])
        data["lufs"] = loudness(path)
    say(f"{path.name}: 대표 장면 추출")
    data["thumbs"] = thumbnails(path, info["duration"], config.WORK_DIR / "thumbs", key) if info["has_video"] else []
    if do_transcribe and info["has_audio"]:
        if transcribe.installed():
            try:
                size = config.load().get("whisper_model", "small")
                data["speech"] = transcribe.transcribe(path, size, lambda m: say(f"{path.name}: {m}"))
                data["transcribed"] = True
            except Exception as exc:  # 모델 내려받기 실패 등: 분석은 계속한다
                data["transcribe_error"] = f"받아쓰기 실패: {exc}"
        else:
            data["transcribe_error"] = "받아쓰기 엔진(sherpa-onnx)이 설치되지 않았습니다."
    silent = sum(e - s for s, e in data["silences"])
    data["silence_ratio"] = round(silent / info["duration"], 3) if info["duration"] else 0.0
    spoken = sum(s["end"] - s["start"] for s in data["speech"])
    data["speech_ratio"] = round(spoken / info["duration"], 3) if info["duration"] else 0.0
    cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data
