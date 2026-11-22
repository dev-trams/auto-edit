"""촬영본 한 개(영상 또는 사진)의 기본 정보. 사진은 편집에 쓰기 좋게 미리 변환해 둔다."""
import hashlib
from pathlib import Path

from . import config, ff

STILL_SECONDS = 3.0   # 사진 한 장을 보여 주는 기본 길이
STILL_MAX = 120.0
MAX_SIDE = 4096


def is_image(path) -> bool:
    return Path(path).suffix.lower() in config.IMAGE_EXT


def prepare_image(path: Path) -> Path:
    """사진을 똑바로 세운 JPEG로 바꿔 작업 폴더에 둔다 (원본은 건드리지 않는다).

    휴대폰 사진은 회전 정보가 따로 저장돼 있고, 아이폰 사진(HEIC)은 FFmpeg가 못 읽는 경우가 있어서다.
    """
    path = Path(path)
    st = path.stat()
    key = hashlib.sha1(f"{path.resolve()}|{st.st_size}|{int(st.st_mtime)}|i1".encode("utf-8")).hexdigest()[:16]
    out = config.WORK_DIR / "images" / f"{key}.jpg"
    if out.is_file() and out.stat().st_size > 0:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image, ImageOps
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            pass
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            im = im.convert("RGB")
            im.thumbnail((MAX_SIDE, MAX_SIDE))
            im.save(out, "JPEG", quality=95)
        return out
    except ImportError:
        pass  # Pillow가 없으면 FFmpeg로 시도한다 (회전 정보는 반영되지 않을 수 있다)
    except Exception:
        pass
    proc = ff.run(["-i", path, "-frames:v", "1", "-q:v", "2", out], check=False)
    if proc.returncode != 0 or not out.is_file():
        out.unlink(missing_ok=True)
        raise ff.FFError(f"사진을 읽을 수 없습니다: {path.name}")
    return out


def info(path) -> dict:
    """영상이면 ff.probe 결과, 사진이면 같은 모양의 정보(길이는 기본 표시 시간)."""
    path = Path(path)
    if not is_image(path):
        data = ff.probe(path)
        data["is_image"] = False
        return data
    probe = ff.probe(prepare_image(path))
    return {"duration": STILL_SECONDS, "width": probe["width"], "height": probe["height"], "fps": 0.0,
            "has_video": True, "has_audio": False, "hdr": False, "is_image": True}
