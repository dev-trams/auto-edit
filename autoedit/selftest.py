"""설치가 제대로 됐는지 스스로 확인한다: FFmpeg, 자막 입히기, 받아쓰기 엔진 불러오기."""
import os
import sys
import tempfile
from pathlib import Path


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="autoedit_selftest_"))
    os.environ["AUTOEDIT_HOME"] = str(tmp / "home")
    from . import config, edl, ff, render, transcribe

    print("ffmpeg:", ff.ffmpeg())
    print("ffprobe:", ff.ffprobe())
    for name in ("subtitles", "silencedetect", "ebur128", "alimiter"):
        if not ff.has_filter(name):
            print(f"FAIL: FFmpeg에 {name} 필터가 없습니다.")
            return 1
    print("zscale(HDR 변환):", ff.has_filter("zscale"))
    config.save({"workspace": str(tmp / "ws")})
    d = config.dirs()
    src = d["src"] / "test.mp4"
    ff.run(["-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=4",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", src])
    plan = edl.blank("selftest")
    plan["clips"] = [{"src": "test.mp4", "in": 0.5, "out": 2.0}, {"src": "test.mp4", "in": 2.5, "out": 3.5, "mute": True}]
    plan["captions"] = [{"start": 0.2, "end": 1.4, "text": "한글 자막 시험"},
                        {"start": 1.6, "end": 2.4, "text": "강조 자막", "emphasis": True}]
    clean, warnings = edl.normalize(plan, d["src"])
    clean["version"] = 1
    out = d["out"] / "selftest_v1.mp4"
    info = render.render(clean, d["src"], out, "final")
    print("render:", info["duration"], "초", f"{info['width']}x{info['height']}", warnings, info["warnings"])
    if abs(info["duration"] - 2.5) > 0.2 or not out.with_suffix(".srt").is_file():
        print("FAIL: 렌더 결과가 예상과 다릅니다.")
        return 1
    print("받아쓰기 엔진:", "있음" if transcribe.installed() else "없음")
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
