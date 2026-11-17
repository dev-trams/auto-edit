"""편집 지시서대로 FFmpeg를 돌려 영상을 만든다."""
import hashlib
import re
import shutil
import sys
from pathlib import Path

from . import config, edl as edl_mod, ff, style

TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
           "zscale=t=bt709:m=bt709:r=tv")
QUALITY = {
    "final": {"scale": 1.0, "seg": ["-preset", "veryfast", "-crf", "12"], "out": ["-preset", "medium", "-crf", "18"]},
    "preview": {"scale": 0.5, "seg": ["-preset", "ultrafast", "-crf", "20"], "out": ["-preset", "ultrafast", "-crf", "28"]},
}


def _even(n: float) -> int:
    n = int(round(n))
    return max(16, n - n % 2)


def _segment(clip: dict, src: Path, info: dict, w: int, h: int, fps: float, q: dict, seg_dir: Path, warnings: list) -> Path:
    dur = round(clip["out"] - clip["in"], 3)
    st = src.stat()
    raw = f"{src.resolve()}|{st.st_size}|{int(st.st_mtime)}|{clip['in']}|{clip['out']}|{clip.get('crop')}|{clip.get('mute')}|{w}x{h}@{fps}|{q['seg']}"
    seg = seg_dir / (hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20] + ".mov")
    if seg.is_file() and seg.stat().st_size > 0:
        return seg
    vf = [f"fps={fps:g}"]
    crop = clip.get("crop")
    if crop:
        vf.append(f"crop=iw*{crop['w']:.4f}:ih*{crop['h']:.4f}:iw*{crop['x']:.4f}:ih*{crop['y']:.4f}")
    vf += [f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos", f"crop={w}:{h}"]
    if info["hdr"]:
        if ff.has_filter("zscale") and ff.has_filter("tonemap"):
            vf.append(TONEMAP)
        else:
            warnings.append("이 FFmpeg에는 HDR 변환 기능(zscale)이 없어 색이 흐리게 나올 수 있습니다.")
    vf += ["setsar=1", "format=yuv420p"]
    args = ["-ss", clip["in"], "-t", dur, "-i", src]
    use_audio = info["has_audio"] and not clip.get("mute")
    if use_audio:
        fade_out = max(0.0, dur - 0.015)
        args += ["-af", f"aresample=48000,aformat=sample_fmts=s16:channel_layouts=stereo,"
                        f"afade=t=in:d=0.015,afade=t=out:st={fade_out:.3f}:d=0.015"]
    else:
        args += ["-f", "lavfi", "-t", dur, "-i", "anullsrc=r=48000:cl=stereo", "-map", "0:v:0", "-map", "1:a:0"]
    args += ["-vf", ",".join(vf), "-c:v", "libx264", *q["seg"], "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le",
             "-ar", "48000", "-ac", "2", "-t", dur, seg]
    ff.run(args)
    return seg


def _lufs(path: Path, cwd=None, limit: float = 0) -> float:
    args = ["-i", path]
    if limit:
        args = ["-t", limit] + args
    proc = ff.run(args + ["-vn", "-af", "ebur128", "-f", "null", "-"], cwd=cwd, check=False)
    tail = proc.stderr[proc.stderr.rfind("Summary:"):]
    m = re.search(r"I:\s+(-?[\d.]+) LUFS", tail)
    return float(m.group(1)) if m else -70.0


def render(edl: dict, src_dir: Path, out_path: Path, quality: str = "final", progress=None) -> dict:
    """edl은 normalize를 거친 것이어야 한다. out_path가 이미 있으면 덮어쓰지 않고 오류를 낸다."""
    say = progress or (lambda m, p=None: None)
    q = QUALITY[quality]
    out_path = Path(out_path)
    if out_path.exists() and quality == "final":
        raise FileExistsError(f"이미 있는 파일입니다: {out_path.name}")
    if not edl["clips"]:
        raise ValueError("사용할 구간이 없습니다.")
    w, h = _even(edl["output"]["width"] * q["scale"]), _even(edl["output"]["height"] * q["scale"])
    fps = edl["output"]["fps"]
    seg_dir = config.WORK_DIR / "segments"
    job_dir = config.WORK_DIR / "render" / f"{edl['name']}_v{edl['version']}_{quality}"
    seg_dir.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(job_dir, ignore_errors=True)
    job_dir.mkdir(parents=True)
    warnings = []

    probes = {}
    lines = []
    total = len(edl["clips"])
    for i, clip in enumerate(edl["clips"], 1):
        say(f"구간 자르는 중 {i}/{total}", 0.7 * (i - 1) / total)
        src = src_dir / clip["src"]
        info = probes.setdefault(clip["src"], ff.probe(src))
        seg = _segment(clip, src, info, w, h, fps, q, seg_dir, warnings)
        link = job_dir / f"s{i:03d}.mov"
        shutil.copyfile(seg, link)
        lines.append(f"file '{link.name}'")
    (job_dir / "list.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    say("이어 붙이는 중", 0.72)
    ff.run(["-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", "all.mov"], cwd=job_dir)
    dur = edl_mod.duration(edl)

    # 소리: 전체 음량을 목표에 맞추고, 배경음은 그보다 작게 깐다
    audio = edl["audio"]
    measured = _lufs(Path("all.mov"), cwd=job_dir)
    gain = 0.0 if measured <= -69 else max(-20.0, min(30.0, audio["target_lufs"] - measured))
    inputs = ["-i", "all.mov"]
    chains = [f"[0:a]volume={gain:.2f}dB[a0]"]
    last = "a0"
    if audio.get("bgm"):
        bgm = Path(audio["bgm"]).expanduser()
        bgm_lufs = _lufs(bgm, limit=90)
        bgm_gain = max(-40.0, min(30.0, audio["target_lufs"] + audio["bgm_offset_db"] - bgm_lufs))
        inputs += ["-stream_loop", "-1", "-i", bgm]
        fade_out = max(0.0, dur - 2.0)
        chains.append(f"[1:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,volume={bgm_gain:.2f}dB,"
                      f"atrim=0:{dur:.3f},afade=t=in:d=1,afade=t=out:st={fade_out:.3f}:d=2[bg]")
        chains.append("[a0][bg]amix=inputs=2:duration=first:dropout_transition=0,volume=2[mx]")
        last = "mx"
    chains.append(f"[{last}]alimiter=limit=0.84:level=false[aout]")

    vmap = "0:v"
    if edl["captions"]:
        (job_dir / "subs.ass").write_text(
            style.make_ass(edl["captions"], edl["style"], w, h), encoding="utf-8")
        sub = "subtitles=subs.ass"
        fonts = config.workspace() / "fonts"
        if fonts.is_dir() and any(fonts.iterdir()):
            shutil.copytree(fonts, job_dir / "fonts", dirs_exist_ok=True)
            sub += ":fontsdir=fonts"
        elif sys.platform == "darwin" and Path("/System/Library/Fonts").is_dir():
            # 함께 묶은 FFmpeg는 시스템 글꼴 설정을 못 찾을 수 있어 폴더를 직접 알려 준다
            sub += ":fontsdir=/System/Library/Fonts"
        chains.append(f"[0:v]{sub}[vout]")
        vmap = "[vout]"

    say("자막과 소리 입히는 중", 0.78)
    tmp_out = job_dir / "out.mp4"
    args = inputs + ["-filter_complex", ";".join(chains), "-map", vmap, "-map", "[aout]", "-t", f"{dur:.3f}",
                     "-c:v", "libx264", *q["out"], "-pix_fmt", "yuv420p",
                     "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                     "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", "out.mp4"]
    ff.run(args, cwd=job_dir)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(tmp_out, out_path)
    if quality == "final" and edl["captions"]:
        srt = out_path.with_suffix(".srt")
        if not srt.exists():
            srt.write_text(style.make_srt(edl["captions"]), encoding="utf-8")
    info = ff.probe(out_path)
    shutil.rmtree(job_dir, ignore_errors=True)
    say("완료", 1.0)
    return {"path": str(out_path), "duration": info["duration"], "width": info["width"], "height": info["height"],
            "gain_db": round(gain, 1), "source_lufs": measured, "warnings": sorted(set(warnings))}
