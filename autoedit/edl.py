"""편집 지시서(EDL): 구조, 검증, 버전 파일."""
import copy
import json
import re
from pathlib import Path

from . import ff

EDL_SCHEMA = {
    "type": "object",
    "properties": {
        "note": {"type": "string", "description": "이 버전에서 무엇을 했는지 한두 문장 (한국어)"},
        "output": {"type": "object", "properties": {
            "width": {"type": "integer"}, "height": {"type": "integer"}, "fps": {"type": "number"}},
            "required": ["width", "height", "fps"]},
        "clips": {"type": "array", "description": "완성본에 이어 붙일 순서대로", "items": {
            "type": "object", "properties": {
                "src": {"type": "string", "description": "원본 파일 이름"},
                "in": {"type": "number", "description": "사용 시작 (초)"},
                "out": {"type": "number", "description": "사용 끝 (초)"},
                "mute": {"type": "boolean"},
                "reason": {"type": "string", "description": "이 구간을 고른 이유 (짧게)"}},
            "required": ["src", "in", "out"]}},
        "captions": {"type": "array", "description": "완성본 타임라인 기준 시간", "items": {
            "type": "object", "properties": {
                "start": {"type": "number"}, "end": {"type": "number"}, "text": {"type": "string"},
                "emphasis": {"type": "boolean", "description": "강조색으로 표시"}},
            "required": ["start", "end", "text"]}},
        "style": {"type": "object", "properties": {
            "preset": {"type": "string", "enum": ["A", "B", "C"]},
            "font_scale": {"type": "number", "description": "1.0이 기본"},
            "margin_bottom_pct": {"type": "number", "description": "자막의 아래 여백 (화면 높이 %)"}}},
        "audio": {"type": "object", "properties": {
            "target_lufs": {"type": "number"},
            "bgm": {"type": ["string", "null"], "description": "배경음 파일 경로. 사용자가 준 경로만 사용"},
            "bgm_offset_db": {"type": "number", "description": "말소리 대비 배경음 크기 (음수)"}}},
    },
    "required": ["note", "output", "clips", "captions", "style", "audio"],
}


def blank(name: str = "편집본") -> dict:
    return {"name": name, "version": 0, "based_on": None, "planner": "manual", "note": "",
            "output": {"width": 1920, "height": 1080, "fps": 30},
            "clips": [], "captions": [],
            "style": {"preset": "C", "font_scale": 1.0, "margin_bottom_pct": None},
            "audio": {"target_lufs": -16, "bgm": None, "bgm_offset_db": -18}}


def duration(edl: dict) -> float:
    return round(sum(max(0.0, c["out"] - c["in"]) for c in edl.get("clips", [])), 3)


def normalize(edl: dict, src_dir: Path) -> tuple:
    """값을 정리하고 경고 목록을 돌려준다. 원본에 없는 파일이나 범위를 벗어난 구간을 걸러낸다."""
    base = blank(edl.get("name", "편집본"))
    out = copy.deepcopy(base)
    warnings = []
    for key in ("name", "version", "based_on", "planner", "note"):
        if key in edl:
            out[key] = edl[key]
    o = edl.get("output") or {}
    w, h = int(o.get("width", 1920)), int(o.get("height", 1080))
    out["output"] = {"width": max(16, w - w % 2), "height": max(16, h - h % 2), "fps": float(o.get("fps", 30) or 30)}
    for c in edl.get("clips") or []:
        name = Path(str(c.get("src", ""))).name
        path = src_dir / name
        if not path.is_file():
            warnings.append(f"원본에 없는 파일이라 뺐습니다: {name}")
            continue
        dur = ff.probe(path)["duration"]
        cin = max(0.0, float(c.get("in", 0)))
        cout = min(dur, float(c.get("out", dur)))
        if cout - cin < 0.2:
            warnings.append(f"너무 짧아 뺐습니다: {name} {cin:.2f}~{cout:.2f}초")
            continue
        clip = {"src": name, "in": round(cin, 3), "out": round(cout, 3), "mute": bool(c.get("mute", False)),
                "reason": str(c.get("reason", ""))}
        crop = c.get("crop")
        if isinstance(crop, dict) and all(k in crop for k in ("x", "y", "w", "h")):
            clip["crop"] = {k: min(1.0, max(0.0, float(crop[k]))) for k in ("x", "y", "w", "h")}
        out["clips"].append(clip)
    total = duration(out)
    for cap in edl.get("captions") or []:
        text = str(cap.get("text", "")).strip()
        start, end = float(cap.get("start", 0)), float(cap.get("end", 0))
        end = min(end, total)
        if not text or end - start < 0.1:
            continue
        out["captions"].append({"start": round(max(0.0, start), 3), "end": round(end, 3), "text": text,
                                "emphasis": bool(cap.get("emphasis", False))})
    out["captions"].sort(key=lambda c: c["start"])
    st = edl.get("style") or {}
    out["style"] = {"preset": str(st.get("preset", "C")).upper() if str(st.get("preset", "C")).upper() in "ABC" else "C",
                    "font_scale": min(3.0, max(0.4, float(st.get("font_scale", 1.0) or 1.0))),
                    "margin_bottom_pct": st.get("margin_bottom_pct")}
    if st.get("font"):
        out["style"]["font"] = str(st["font"])
    au = edl.get("audio") or {}
    bgm = au.get("bgm") or None
    if bgm and not Path(bgm).expanduser().is_file():
        warnings.append(f"배경음 파일을 찾을 수 없어 뺐습니다: {bgm}")
        bgm = None
    out["audio"] = {"target_lufs": min(-8.0, max(-40.0, float(au.get("target_lufs", -16)))), "bgm": bgm,
                    "bgm_offset_db": min(0.0, max(-40.0, float(au.get("bgm_offset_db", -18))))}
    if not out["clips"]:
        warnings.append("사용할 구간이 하나도 없습니다.")
    return out, warnings


_VER = re.compile(r"_v(\d+)(?:\.edl\.json|\.mp4|\.srt)$")


def versions(out_dir: Path, name: str) -> list:
    found = {}
    for p in out_dir.glob(f"{name}_v*"):
        m = _VER.search(p.name)
        if not m or p.name[: -len(m.group(0))] != name:
            continue
        v = found.setdefault(int(m.group(1)), {"version": int(m.group(1)), "edl": False, "video": False})
        if p.name.endswith(".edl.json"):
            v["edl"] = True
        elif p.suffix == ".mp4":
            v["video"] = True
    return [found[k] for k in sorted(found)]


def next_version(out_dir: Path, name: str) -> int:
    vs = versions(out_dir, name)
    return (vs[-1]["version"] + 1) if vs else 1


def path_for(out_dir: Path, name: str, version: int, kind: str = "edl") -> Path:
    ext = {"edl": ".edl.json", "video": ".mp4", "srt": ".srt"}[kind]
    return out_dir / f"{name}_v{version}{ext}"


def save_new(edl: dict, out_dir: Path) -> dict:
    """항상 새 버전 번호로 저장한다. 기존 파일은 덮어쓰지 않는다."""
    edl = copy.deepcopy(edl)
    edl["version"] = next_version(out_dir, edl["name"])
    path_for(out_dir, edl["name"], edl["version"]).write_text(
        json.dumps(edl, ensure_ascii=False, indent=2), encoding="utf-8")
    return edl


def load(out_dir: Path, name: str, version: int) -> dict:
    return json.loads(path_for(out_dir, name, version).read_text(encoding="utf-8"))
