"""설정과 폴더 위치."""
import json
import os
from pathlib import Path

APP_DIR = Path(os.environ.get("AUTOEDIT_HOME", str(Path.home() / ".autoedit")))
WORK_DIR = APP_DIR / "work"
MODEL_DIR = APP_DIR / "models"
SETTINGS_FILE = APP_DIR / "settings.json"

SUBDIRS = {"src": "원본 영상", "ref": "레퍼런스", "out": "완성본"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}

DEFAULTS = {
    "workspace": "",
    "api_key": "",
    "model_plan": "claude-sonnet-5-5",
    "model_revise": "claude-haiku-5-5",
    "whisper_model": "small",
    "project": "편집본",
}


def load() -> dict:
    data = dict(DEFAULTS)
    if SETTINGS_FILE.exists():
        try:
            data.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return data


def save(update: dict) -> dict:
    data = load()
    data.update({k: v for k, v in update.items() if k in DEFAULTS})
    APP_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def api_key() -> str:
    return os.environ.get("ANTHROPIC_API_KEY") or load().get("api_key", "")


def _guess_workspace() -> Path:
    home = Path.home()
    for base in ("Movies", "Videos", "동영상"):
        cand = home / base / "AI 영상 편집"
        if cand.exists():
            return cand
    base = home / ("Videos" if os.name == "nt" else "Movies")
    return base / "AI 영상 편집"


def workspace() -> Path:
    ws = load().get("workspace") or ""
    path = Path(ws).expanduser() if ws else _guess_workspace()
    return path


def dirs(create: bool = True) -> dict:
    ws = workspace()
    out = {k: ws / v for k, v in SUBDIRS.items()}
    out["root"] = ws
    if create:
        for p in out.values():
            p.mkdir(parents=True, exist_ok=True)
        WORK_DIR.mkdir(parents=True, exist_ok=True)
    return out


def style_memo() -> str:
    for name in ("스타일 메모.md", "레퍼런스/스타일.md"):
        p = workspace() / name
        if p.exists():
            try:
                return p.read_text(encoding="utf-8")
            except OSError:
                pass
    return ""
