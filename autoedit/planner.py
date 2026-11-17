"""편집 지시서를 만드는 두 가지 방법: 규칙 기반(키 없이 동작)과 Claude API."""
import base64
import copy
import json
import re

from . import config, edl as edl_mod

PAD_BEFORE, PAD_AFTER = 0.10, 0.15
CAPTION_MAX_CHARS = 16


class NeedsAI(Exception):
    """규칙으로는 처리할 수 없는 요청."""


# ---------- 공통 ----------

def _output_for(analyses: list) -> dict:
    port = sum(a["duration"] for a in analyses if a["height"] > a["width"])
    land = sum(a["duration"] for a in analyses if a["height"] <= a["width"])
    return {"width": 1080, "height": 1920, "fps": 30} if port > land else {"width": 1920, "height": 1080, "fps": 30}


def _split_caption(text: str, start: float, end: float) -> list:
    """긴 문장을 한 호흡 단위로 나누고 글자 수에 비례해 시간을 나눈다."""
    words = text.split()
    chunks, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > CAPTION_MAX_CHARS:
            chunks.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        chunks.append(cur)
    total = sum(len(c) for c in chunks) or 1
    out, t = [], start
    for c in chunks:
        d = (end - start) * len(c) / total
        out.append({"start": round(t, 2), "end": round(t + d, 2), "text": c, "emphasis": False})
        t += d
    return out


def _keep_ranges(a: dict) -> list:
    dur = a["duration"]
    if a.get("speech"):
        raw = [(max(0.0, s["start"] - PAD_BEFORE), min(dur, s["end"] + PAD_AFTER)) for s in a["speech"]]
    else:
        raw, t = [], 0.0
        for s, e in a.get("silences", []):
            if s - t > 0.05:
                raw.append((max(0.0, t - PAD_BEFORE), min(dur, s + PAD_AFTER)))
            t = e
        if dur - t > 0.05:
            raw.append((max(0.0, t - PAD_BEFORE), dur))
        if not a.get("silences"):
            raw = [(0.0, dur)]
    merged = []
    for s, e in sorted(raw):
        if merged and s - merged[-1][1] <= 0.35:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return [(round(s, 2), round(e, 2)) for s, e in merged if e - s >= 0.3]


# ---------- 규칙 기반 ----------

def rule_plan(analyses: list, mode: str = "auto", caption_lines=None, clip_len: float = 3.0, name: str = "편집본") -> dict:
    caption_lines = [ln.strip() for ln in (caption_lines or []) if ln.strip()]
    total = sum(a["duration"] for a in analyses) or 1
    speech_ratio = sum(a["duration"] * a.get("speech_ratio", 0) for a in analyses) / total
    if mode == "auto":
        mode = "talk" if speech_ratio >= 0.3 and not caption_lines else "template"
    plan = edl_mod.blank(name)
    plan.update(planner="rule", output=_output_for(analyses))
    plan["style"]["preset"] = config.default_preset()
    t = 0.0
    if mode == "talk":
        for a in analyses:
            for s, e in _keep_ranges(a):
                plan["clips"].append({"src": a["file"], "in": s, "out": e, "mute": False, "reason": "말소리 구간"})
                for sp in a.get("speech", []):
                    if sp["end"] <= s or sp["start"] >= e:
                        continue
                    cs, ce = max(sp["start"], s), min(sp["end"], e)
                    plan["captions"] += _split_caption(sp["text"], t + cs - s, t + ce - s)
                t += e - s
        plan["audio"]["target_lufs"] = -16
        plan["note"] = "말소리 구간만 남기고 무음을 잘랐습니다 (규칙 기반)."
    else:
        for i, a in enumerate(analyses):
            head = min(0.5, a["duration"] * 0.1)
            length = max(0.3, min(clip_len, a["duration"] - 2 * head))
            plan["clips"].append({"src": a["file"], "in": round(head, 2), "out": round(head + length, 2),
                                  "mute": False, "reason": "가운데 구간"})
            if i < len(caption_lines):
                line = caption_lines[i]
                plan["captions"].append({"start": round(t + 0.15, 2), "end": round(t + length - 0.1, 2),
                                         "text": line.lstrip("*").strip(), "emphasis": line.startswith("*")})
            t += length
        plan["audio"]["target_lufs"] = -16 if speech_ratio >= 0.3 else -23
        plan["note"] = "클립마다 가운데 구간을 쓰고, 입력한 자막을 순서대로 붙였습니다 (규칙 기반)."
    return plan


_RULES = [
    (r"자막.*(크게|키워|키우)", lambda e: e["style"].__setitem__("font_scale", round(e["style"]["font_scale"] * 1.15, 3)), "자막을 한 단계 키웠습니다."),
    (r"자막.*(작게|줄여|줄이)", lambda e: e["style"].__setitem__("font_scale", round(e["style"]["font_scale"] / 1.15, 3)), "자막을 한 단계 줄였습니다."),
    (r"자막.*(위로|올려|올리)", lambda e: e["style"].__setitem__("margin_bottom_pct", (e["style"].get("margin_bottom_pct") or 11) + 4), "자막을 위로 올렸습니다."),
    (r"자막.*(아래로|내려|내리)", lambda e: e["style"].__setitem__("margin_bottom_pct", max(2, (e["style"].get("margin_bottom_pct") or 11) - 4)), "자막을 아래로 내렸습니다."),
    (r"(배경음|음악|bgm).*(작게|줄여|줄이)", lambda e: e["audio"].__setitem__("bgm_offset_db", e["audio"]["bgm_offset_db"] - 3), "배경음을 3dB 줄였습니다."),
    (r"(배경음|음악|bgm).*(크게|키워|키우)", lambda e: e["audio"].__setitem__("bgm_offset_db", min(0, e["audio"]["bgm_offset_db"] + 3)), "배경음을 3dB 키웠습니다."),
    (r"(소리|음량|볼륨).*(크게|키워|키우)", lambda e: e["audio"].__setitem__("target_lufs", e["audio"]["target_lufs"] + 2), "전체 소리를 키웠습니다."),
    (r"(소리|음량|볼륨).*(작게|줄여|줄이)", lambda e: e["audio"].__setitem__("target_lufs", e["audio"]["target_lufs"] - 2), "전체 소리를 줄였습니다."),
]


def rule_revise(edl: dict, message: str) -> dict:
    """API 키 없이 처리할 수 있는 단순 수정."""
    new = copy.deepcopy(edl)
    msg = message.lower()
    notes = []
    m = re.search(r"\b([abc])\s*형", msg)
    if m:
        new["style"].update(preset=m.group(1).upper(), margin_bottom_pct=None)
        notes.append(f"자막을 {m.group(1).upper()}형으로 바꿨습니다.")
    for pattern, fn, note in _RULES:
        if re.search(pattern, msg):
            fn(new)
            notes.append(note)
    if not notes:
        raise NeedsAI("이 요청은 Claude API 키가 있어야 처리할 수 있습니다. (키 없이 되는 것: 자막 크게/작게, "
                      "위로/아래로, A·B·C형, 배경음·소리 크게/작게)")
    new.update(planner="rule", note=" ".join(notes), based_on=edl.get("version"))
    return new


# ---------- Claude API ----------

SYSTEM = """당신은 영상 편집자입니다. 촬영본 분석 결과를 보고 편집 지시서를 submit_edl 도구로 제출합니다.
당신은 영상을 직접 볼 수 없고, 주어진 대표 장면 이미지·무음 구간·받아쓰기만으로 판단합니다.

규칙:
- clips의 in/out은 각 원본 파일 안의 초 단위 시간입니다. 파일 길이를 넘지 마세요.
- captions의 시간은 완성본 타임라인 기준입니다 (앞선 clips 길이를 누적한 시간).
- 말소리가 있으면 말이 없는 구간을 잘라내되, 말 앞 0.10초·뒤 0.15초 여유를 남기고 숨 쉬는 짧은 쉼은 남깁니다.
- 자막은 받아쓰기 내용만 씁니다. 오탈자와 띄어쓰기는 다듬되 없는 말을 지어내지 않습니다.
  사용자가 자막 문구를 직접 준 경우에는 그 문구를 그대로 씁니다.
- 자막은 한 줄, 한 호흡 단위(대략 16자 이내)로 나눕니다.
- 전환 효과는 없습니다. 하드 컷만 씁니다.
- 배경음(audio.bgm)은 사용자가 경로를 준 경우에만 넣습니다. 그렇지 않으면 null입니다.
- 스타일 메모가 있으면 그 기준을 따릅니다.
- 확신이 없는 판단은 note에 한국어로 적습니다.
- 답은 글로 쓰지 말고 반드시 submit_edl 도구 호출로 제출합니다."""


def _summary(analyses: list) -> list:
    return [{"file": a["file"], "duration": a["duration"], "width": a["width"], "height": a["height"],
             "lufs": a.get("lufs"), "silences": a.get("silences", []), "speech": a.get("speech", [])} for a in analyses]


def _client():
    key = config.api_key()
    if not key:
        raise NeedsAI("Claude API 키가 설정되지 않았습니다.")
    import anthropic
    return anthropic.Anthropic(api_key=key)


def _extract_json(text: str):
    """도구를 쓰지 않고 글로 답한 경우, 글 안의 JSON을 찾아 쓴다."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) and "clips" in data else None


def _call(model: str, content: list) -> dict:
    """submit_edl 도구 호출을 받아 온다.

    최신 모델은 도구를 강제로 지정하는 옵션(tool_choice: tool/any)을 받지 않으므로
    자동 선택으로 두고, 도구를 쓰지 않았으면 한 번 더 요청한다.
    """
    client = _client()
    tools = [{"name": "submit_edl", "description": "완성한 편집 지시서를 제출한다. 답은 반드시 이 도구로 낸다.",
              "input_schema": edl_mod.EDL_SCHEMA}]
    messages = [{"role": "user", "content": content}]
    for attempt in range(2):
        resp = client.messages.create(model=model, max_tokens=16000, system=SYSTEM, tools=tools,
                                      tool_choice={"type": "auto"}, messages=messages)
        texts = []
        for block in resp.content:
            kind = getattr(block, "type", "")
            if kind == "tool_use" and getattr(block, "name", "") == "submit_edl":
                return dict(block.input)
            if kind == "text":
                texts.append(block.text)
        found = _extract_json("\n".join(texts))
        if found:
            return found
        if getattr(resp, "stop_reason", "") == "max_tokens":
            raise RuntimeError("Claude의 답이 너무 길어 중간에 끊겼습니다. 촬영본을 나눠서 다시 시도해 주세요.")
        messages = messages + [{"role": "assistant", "content": resp.content},
                               {"role": "user", "content": "설명 없이 submit_edl 도구를 호출해서 편집 지시서를 제출하세요."}]
    raise RuntimeError("Claude가 편집 지시서를 돌려주지 않았습니다.")


def claude_plan(analyses: list, instruction: str = "", caption_lines=None, name: str = "편집본", max_images: int = 40) -> dict:
    content = []
    per_clip = max(2, max_images // max(1, len(analyses)))
    thumbs_dir = config.WORK_DIR / "thumbs"
    for a in analyses:
        thumbs = a.get("thumbs", [])
        step = max(1, len(thumbs) // per_clip)
        for th in thumbs[::step][:per_clip]:
            path = thumbs_dir / th["file"]
            if not path.is_file():
                continue
            content.append({"type": "text", "text": f"{a['file']} · {th['t']}초"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                        "data": base64.standard_b64encode(path.read_bytes()).decode("ascii")}})
    parts = ["## 촬영본 분석\n" + json.dumps(_summary(analyses), ensure_ascii=False),
             "## 권장 출력 크기\n" + json.dumps(_output_for(analyses))]
    memo = config.style_memo()
    if memo:
        parts.append("## 스타일 메모\n" + memo[:6000])
    lines = [ln for ln in (caption_lines or []) if ln.strip()]
    if lines:
        parts.append("## 사용자가 준 자막 문구 (순서대로, *로 시작하면 강조)\n" + "\n".join(lines))
    parts.append("## 요청\n" + (instruction.strip() or "스타일 메모 기준으로 편집해 주세요."))
    content.append({"type": "text", "text": "\n\n".join(parts)})
    plan = _call(config.load()["model_plan"], content)
    plan.update(name=name, planner="claude")
    return plan


def claude_revise(edl: dict, message: str, analyses: list) -> dict:
    core = {k: edl[k] for k in ("output", "clips", "captions", "style", "audio")}
    text = ("## 현재 편집 지시서\n" + json.dumps(core, ensure_ascii=False) +
            "\n\n## 촬영본 분석\n" + json.dumps(_summary(analyses), ensure_ascii=False) +
            "\n\n## 수정 요청\n" + message.strip() +
            "\n\n요청한 부분만 고치고 나머지는 그대로 둔 전체 지시서를 제출하세요. "
            "clips를 바꾸면 captions 시간도 그에 맞게 옮기세요. note에는 무엇을 바꿨는지 적으세요.")
    new = _call(config.load()["model_revise"], [{"type": "text", "text": text}])
    new.update(name=edl["name"], planner="claude", based_on=edl.get("version"))
    return new


def revise(edl: dict, message: str, analyses: list) -> dict:
    """단순한 요청은 규칙으로, 나머지는 Claude로."""
    try:
        return rule_revise(edl, message)
    except NeedsAI:
        if not config.api_key():
            raise
        return claude_revise(edl, message, analyses)
