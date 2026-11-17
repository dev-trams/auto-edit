"""받아쓰기: Whisper 모델을 sherpa-onnx로 CPU에서 실행한다.

말소리 구간을 먼저 찾고(VAD) 그 구간만 받아 적는다. 소리 없는 구간에서
Whisper가 문장을 지어내는 문제를 줄이기 위해서다.
"""
import re
import subprocess
import tarfile
import urllib.request
from pathlib import Path

from . import config, ff

BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"
VAD_URL = f"{BASE}/silero_vad.onnx"
SAMPLE_RATE = 16000


def installed() -> bool:
    try:
        import numpy  # noqa: F401
        import sherpa_onnx  # noqa: F401
        return True
    except ImportError:
        return False


def _model_paths(size: str) -> dict:
    d = config.MODEL_DIR / f"sherpa-onnx-whisper-{size}"
    return {"dir": d, "encoder": d / f"{size}-encoder.int8.onnx", "decoder": d / f"{size}-decoder.int8.onnx",
            "tokens": d / f"{size}-tokens.txt", "vad": config.MODEL_DIR / "silero_vad.onnx"}


def models_ready(size: str = "small") -> bool:
    p = _model_paths(size)
    return all(p[k].is_file() for k in ("encoder", "decoder", "tokens", "vad"))


def _download(url: str, dest: Path, progress=None, label=""):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as resp, open(tmp, "wb") as f:
        total = int(resp.headers.get("Content-Length", 0) or 0)
        done = 0
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(f"{label} 내려받는 중 {done * 100 // total}%")
    tmp.replace(dest)


def ensure_models(size: str = "small", progress=None):
    """모델이 없으면 내려받는다 (small 약 610MB). 처음 한 번만 걸린다."""
    p = _model_paths(size)
    if not p["vad"].is_file():
        _download(VAD_URL, p["vad"], progress, "말소리 검출 모델")
    if not all(p[k].is_file() for k in ("encoder", "decoder", "tokens")):
        archive = config.MODEL_DIR / f"whisper-{size}.tar.bz2"
        _download(f"{BASE}/sherpa-onnx-whisper-{size}.tar.bz2", archive, progress, f"Whisper {size} 모델")
        if progress:
            progress("모델 압축 푸는 중")
        with tarfile.open(archive, "r:bz2") as tar:
            try:
                tar.extractall(config.MODEL_DIR, filter="data")
            except TypeError:  # 오래된 Python
                tar.extractall(config.MODEL_DIR)
        archive.unlink(missing_ok=True)


def load_audio(path):
    import numpy as np
    cmd = [ff.ffmpeg(), "-nostdin", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
           "-f", "f32le", "-"]
    proc = subprocess.run(cmd, capture_output=True)
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()


_BRACKETED = re.compile(r"^[\[\(（【].*[\]\)）】]$")


def clean_text(text: str) -> str:
    """Whisper가 흔히 지어내는 형태(괄호 설명, 같은 말 반복)를 걸러낸다."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text or _BRACKETED.match(text):
        return ""
    text = re.sub(r"[\[\(（【][^\]\)）】]*[\]\)）】]", "", text).strip()
    words = text.split()
    if len(words) >= 6 and len(set(words)) <= max(2, len(words) // 4):
        return ""
    return text


_cache = {}


def _recognizer(size: str):
    import sherpa_onnx
    if size not in _cache:
        p = _model_paths(size)
        _cache[size] = sherpa_onnx.OfflineRecognizer.from_whisper(
            encoder=str(p["encoder"]), decoder=str(p["decoder"]), tokens=str(p["tokens"]),
            language="ko", task="transcribe", num_threads=4)
    return _cache[size]


def speech_segments(samples, size: str = "small") -> list:
    """말소리 구간 [(start, end, samples)]."""
    import sherpa_onnx
    cfg = sherpa_onnx.VadModelConfig()
    cfg.silero_vad.model = str(_model_paths(size)["vad"])
    cfg.silero_vad.threshold = 0.5
    cfg.silero_vad.min_silence_duration = 0.25
    cfg.silero_vad.min_speech_duration = 0.25
    cfg.silero_vad.max_speech_duration = 6.0
    cfg.sample_rate = SAMPLE_RATE
    vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=120)
    window = cfg.silero_vad.window_size
    out = []

    def drain():
        while not vad.empty():
            seg = vad.front
            start = seg.start / SAMPLE_RATE
            out.append((start, start + len(seg.samples) / SAMPLE_RATE, seg.samples))
            vad.pop()

    for i in range(0, len(samples) - window + 1, window):
        vad.accept_waveform(samples[i:i + window])
        drain()
    vad.flush()
    drain()
    return out


def transcribe(path, size: str = "small", progress=None) -> list:
    """[{start, end, text}] — 말소리가 없으면 빈 목록."""
    ensure_models(size, progress)
    samples = load_audio(path)
    if len(samples) < SAMPLE_RATE // 2:
        return []
    rec = _recognizer(size)
    result = []
    segs = speech_segments(samples, size)
    for n, (start, end, seg) in enumerate(segs, 1):
        if progress:
            progress(f"받아쓰기 {n}/{len(segs)}")
        stream = rec.create_stream()
        stream.accept_waveform(SAMPLE_RATE, seg)
        rec.decode_stream(stream)
        text = clean_text(stream.result.text)
        if text:
            result.append({"start": round(start, 2), "end": round(end, 2), "text": text})
    return result
