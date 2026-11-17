"""자막 스타일 (스타일 메모의 A/B/C형)과 ASS/SRT 생성."""
import sys

# 글자 높이는 화면 높이 대비 비율. 세로 영상은 화면이 길어 같은 비율이면 너무 커지므로 따로 둔다.
PRESETS = {
    "A": {"label": "A형 · 얇은 명조, 그림자", "family": "serif", "bold": False, "size_land": 0.050, "size_port": 0.040,
          "margin_bottom_pct": 11, "border": "shadow", "emphasis": "#FFE14A"},
    "B": {"label": "B형 · 굵은 고딕, 검정 외곽선", "family": "sans", "bold": True, "size_land": 0.065, "size_port": 0.052,
          "margin_bottom_pct": 15, "border": "outline", "emphasis": "#FF4A3D"},
    "C": {"label": "C형 · 굵은 고딕, 반투명 박스", "family": "sans", "bold": True, "size_land": 0.047, "size_port": 0.0375,
          "margin_bottom_pct": 11, "border": "box", "emphasis": "#FFD24A"},
}

FONTS = {
    "win32": {"sans": "Malgun Gothic", "serif": "Batang"},
    "darwin": {"sans": "Apple SD Gothic Neo", "serif": "AppleMyungjo"},
    "linux": {"sans": "Noto Sans CJK KR", "serif": "Noto Serif CJK KR"},
}


def font_name(family: str, override: str = "") -> str:
    if override:
        return override
    table = FONTS.get(sys.platform if sys.platform in FONTS else "linux")
    return table[family]


def resolve(style: dict, width: int, height: int) -> dict:
    """EDL의 style 블록을 실제 픽셀 값으로 바꾼다."""
    preset = PRESETS.get(str(style.get("preset", "C")).upper(), PRESETS["C"])
    ratio = preset["size_port"] if height > width else preset["size_land"]
    scale = float(style.get("font_scale", 1.0) or 1.0)
    margin_pct = style.get("margin_bottom_pct")
    if margin_pct is None:
        margin_pct = preset["margin_bottom_pct"]
    return {
        "font": font_name(preset["family"], style.get("font", "")),
        "bold": preset["bold"],
        "font_px": max(12, round(height * ratio * scale)),
        "margin_v": round(height * float(margin_pct) / 100),
        "border": preset["border"],
        "emphasis": style.get("emphasis_color") or preset["emphasis"],
    }


def _ass_color(hex_rgb: str, alpha: int = 0) -> str:
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def make_ass(captions: list, style: dict, width: int, height: int) -> str:
    s = resolve(style, width, height)
    px = s["font_px"]
    if s["border"] == "box":
        border, outline, shadow = 3, max(4, round(px * 0.11)), 0
        outline_col, back_col = _ass_color("#262626", 0x59), _ass_color("#000000", 0xFF)
    elif s["border"] == "outline":
        border, outline, shadow = 1, max(2, round(px * 0.08)), 0
        outline_col, back_col = _ass_color("#000000"), _ass_color("#000000", 0xFF)
    else:
        border, outline, shadow = 1, 1, max(1, round(px * 0.04))
        outline_col, back_col = _ass_color("#000000", 0x80), _ass_color("#000000", 0x80)
    head = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}",
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Cap,{s['font']},{px},{_ass_color('#FFFFFF')},{_ass_color('#FFFFFF')},{outline_col},{back_col},"
        f"{-1 if s['bold'] else 0},0,0,0,100,100,0,0,{border},{outline},{shadow},2,40,40,{s['margin_v']},1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    emph = _ass_color(s["emphasis"])[4:]  # BBGGRR
    for c in captions:
        text = str(c.get("text", "")).replace("\n", "\\N").replace("{", "(").replace("}", ")")
        if not text.strip():
            continue
        if c.get("emphasis"):
            text = "{\\c&H" + emph + "&}" + text
        head.append(f"Dialogue: 0,{_ass_time(c['start'])},{_ass_time(c['end'])},Cap,,0,0,0,,{text}")
    return "\n".join(head) + "\n"


def make_srt(captions: list) -> str:
    out = []
    n = 0
    for c in captions:
        if not str(c.get("text", "")).strip():
            continue
        n += 1
        out.append(f"{n}\n{_srt_time(c['start'])} --> {_srt_time(c['end'])}\n{c['text']}\n")
    return "\n".join(out)
