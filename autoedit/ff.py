"""FFmpeg/ffprobe 호출 도우미."""
import functools
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


class FFError(RuntimeError):
    pass


def _find(name: str) -> str:
    exe = name + (".exe" if os.name == "nt" else "")
    # 설치형으로 묶을 때: 실행 파일 옆 bin/ 폴더를 먼저 본다
    bases = [Path(sys.argv[0]).resolve().parent, Path(__file__).resolve().parent.parent]
    if getattr(sys, "_MEIPASS", ""):
        bases.insert(0, Path(sys._MEIPASS))
    for base in bases:
        # 절대 경로로 바꿔 둔다: FFmpeg는 작업 폴더를 옮겨서 실행하므로 상대 경로면 못 찾는다
        cand = (base / "bin" / exe).resolve()
        if cand.is_file():
            if os.name != "nt" and not os.access(cand, os.X_OK):
                try:
                    cand.chmod(0o755)
                except OSError:
                    pass
            return str(cand)
    found = shutil.which(name)
    if not found and os.name != "nt":
        # 맥에서 앱으로 실행하면 PATH에 Homebrew 경로가 빠져 있다
        for d in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"):
            if (Path(d) / exe).is_file():
                found = str(Path(d) / exe)
                break
    if not found:
        raise FFError(f"{name}를 찾을 수 없습니다. FFmpeg를 설치해 주세요.")
    return found


@functools.lru_cache(maxsize=None)
def ffmpeg() -> str:
    return _find("ffmpeg")


@functools.lru_cache(maxsize=None)
def ffprobe() -> str:
    return _find("ffprobe")


def available() -> bool:
    try:
        ffmpeg(), ffprobe()
        return True
    except FFError:
        return False


_ACTIVE = set()  # 지금 돌고 있는 FFmpeg 프로세스


def run(args, cwd=None, check=True) -> subprocess.CompletedProcess:
    cmd = [ffmpeg(), "-nostdin", "-hide_banner", "-y"] + [str(a) for a in args]
    child = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding="utf-8", errors="replace")
    _ACTIVE.add(child)
    try:
        out, err = child.communicate()
    finally:
        _ACTIVE.discard(child)
    proc = subprocess.CompletedProcess(cmd, child.returncode, out, err)
    if check and proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-8:])
        raise FFError(f"FFmpeg 실패:\n{tail}")
    return proc


def kill_children():
    """프로그램을 끌 때 돌고 있던 FFmpeg도 함께 끝낸다."""
    for child in list(_ACTIVE):
        try:
            child.kill()
        except OSError:
            pass


@functools.lru_cache(maxsize=None)
def has_filter(name: str) -> bool:
    proc = subprocess.run([ffmpeg(), "-hide_banner", "-filters"], capture_output=True, text=True, errors="replace")
    return any(line.split()[1:2] == [name] for line in proc.stdout.splitlines() if len(line.split()) > 2)


def _rate(text: str) -> float:
    try:
        num, den = text.split("/")
        return float(num) / float(den) if float(den) else 0.0
    except (ValueError, AttributeError):
        return 0.0


def probe(path) -> dict:
    """영상 기본 정보. 회전 정보를 반영한 실제 가로·세로를 돌려준다."""
    cmd = [ffprobe(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise FFError(f"파일을 읽을 수 없습니다: {Path(path).name}")
    data = json.loads(proc.stdout or "{}")
    info = {"duration": float(data.get("format", {}).get("duration", 0) or 0), "width": 0, "height": 0,
            "fps": 0.0, "has_video": False, "has_audio": False, "hdr": False}
    for st in data.get("streams", []):
        if st.get("codec_type") == "video" and not info["has_video"]:
            if st.get("disposition", {}).get("attached_pic"):
                continue
            w, h = int(st.get("width", 0)), int(st.get("height", 0))
            rot = 0
            for sd in st.get("side_data_list", []) or []:
                if "rotation" in sd:
                    rot = int(float(sd["rotation"]))
            rot = rot or int(float(st.get("tags", {}).get("rotate", 0) or 0))
            if abs(rot) % 180 == 90:
                w, h = h, w
            fps = _rate(st.get("avg_frame_rate", "")) or _rate(st.get("r_frame_rate", ""))
            info.update(width=w, height=h, fps=round(fps, 3), has_video=True,
                        hdr=st.get("color_transfer") in ("arib-std-b67", "smpte2084"))
        elif st.get("codec_type") == "audio":
            info["has_audio"] = True
    return info
