"""PC 안에서만 도는 웹 서버. 브라우저 화면과 편집 엔진을 잇는다."""
import json
import threading
import traceback
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse

from . import __version__, analyze, config, edl as edl_mod, ff, planner, reference, render, style, transcribe

app = FastAPI(title="auto-edit")
WEB = Path(__file__).parent / "web"
JOBS = {}
HEAVY = threading.Lock()  # 무거운 작업은 한 번에 하나만


def _project() -> str:
    return config.load().get("project") or "편집본"


def start_job(label: str, fn) -> dict:
    job = {"id": uuid.uuid4().hex[:12], "label": label, "status": "queued", "message": "대기 중", "progress": 0.0,
           "result": None, "error": ""}
    JOBS[job["id"]] = job

    def update(message, progress=None):
        job["message"] = message
        if progress is not None:
            job["progress"] = round(float(progress), 3)

    def work():
        with HEAVY:
            job["status"] = "running"
            try:
                job["result"] = fn(update)
                job.update(status="done", progress=1.0)
            except (planner.NeedsAI, ff.FFError, FileExistsError, ValueError) as exc:
                job.update(status="error", error=str(exc))
            except Exception as exc:  # 예상 못 한 오류도 화면에 보여 준다
                traceback.print_exc()
                job.update(status="error", error=f"{type(exc).__name__}: {exc}")

    threading.Thread(target=work, daemon=True).start()
    return job


def _sources(d: dict) -> list:
    out = []
    for p in sorted(d["src"].iterdir(), key=lambda x: x.name.lower()):
        if p.is_file() and p.suffix.lower() in config.VIDEO_EXT:
            cache = config.WORK_DIR / "analysis" / f"{analyze._key(p)}.json"
            data = json.loads(cache.read_text(encoding="utf-8")) if cache.is_file() else None
            out.append({"file": p.name, "size": p.stat().st_size, "analysis": data})
    return out


def _analyses(files: list, d: dict, update, do_transcribe: bool = True) -> list:
    out = []
    for i, name in enumerate(files):
        path = d["src"] / Path(name).name
        if not path.is_file():
            raise ValueError(f"원본 영상 폴더에 없는 파일입니다: {name}")
        out.append(analyze.analyze_clip(path, do_transcribe, lambda m, i=i: update(m, i / max(1, len(files)))))
    return out


def _save(plan: dict, d: dict) -> dict:
    clean, warnings = edl_mod.normalize(plan, d["src"])
    clean["name"] = _project()
    saved = edl_mod.save_new(clean, d["out"])
    return {"edl": saved, "warnings": warnings}


@app.get("/", response_class=HTMLResponse)
def index():
    return (WEB / "index.html").read_text(encoding="utf-8")


@app.get("/api/state")
def state():
    d = config.dirs()
    s = config.load()
    size = s.get("whisper_model", "small")
    return {
        "version": __version__,
        "settings": {"workspace": str(d["root"]), "project": _project(), "has_key": bool(config.api_key()),
                     "whisper_model": size, "model_plan": s["model_plan"], "model_revise": s["model_revise"]},
        "env": {"ffmpeg": ff.available(), "hdr_convert": ff.available() and ff.has_filter("zscale"),
                "transcribe_installed": transcribe.installed(), "models_ready": transcribe.models_ready(size),
                "style_memo": bool(config.style_memo())},
        "sources": _sources(d) if ff.available() else [],
        "versions": edl_mod.versions(d["out"], _project()),
        "presets": {k: v["label"] for k, v in style.PRESETS.items()},
        "references": [p.name for p in reference.list_files()],
        "memo": config.style_memo(),
        "default_preset": config.default_preset(),
    }


@app.post("/api/settings")
async def settings(request: Request):
    body = await request.json()
    update = {k: body[k] for k in ("workspace", "project", "whisper_model", "model_plan", "model_revise") if k in body}
    if body.get("api_key"):
        update["api_key"] = body["api_key"].strip()
    if "workspace" in update and update["workspace"]:
        if not Path(update["workspace"]).expanduser().is_dir():
            raise HTTPException(400, "작업 폴더를 찾을 수 없습니다.")
    config.save(update)
    return state()


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...)):
    d = config.dirs()
    saved = []
    for f in files:
        name = Path(f.filename or "upload").name
        if Path(name).suffix.lower() not in config.VIDEO_EXT:
            continue
        dest = d["src"] / name
        n = 1
        while dest.exists():  # 원본은 덮어쓰지 않는다
            dest = d["src"] / f"{Path(name).stem}_{n}{Path(name).suffix}"
            n += 1
        with open(dest, "wb") as out:
            while chunk := await f.read(1 << 20):
                out.write(chunk)
        saved.append(dest.name)
    return {"saved": saved}


@app.post("/api/reference/learn")
async def api_reference_learn(request: Request):
    body = await request.json()
    urls = [u.strip() for u in (body.get("urls") or "").splitlines() if u.strip()]
    for u in urls:
        if not u.lower().startswith(("http://", "https://")):
            raise HTTPException(400, f"링크 형식이 아닙니다: {u[:60]}")

    def work(update):
        got = reference.download(urls, update) if urls else {"saved": [], "failed": []}
        if got["failed"] and not reference.list_files():
            # 받은 영상이 하나도 없으면 왜 못 받았는지를 그대로 보여 준다
            reasons = "\n".join(f"- {f['url']}\n  {f['error']}" for f in got["failed"])
            raise ValueError(f"링크의 영상을 받지 못했습니다 (yt-dlp {got.get('version', '?')}).\n{reasons}\n\n"
                             "영상 파일을 '레퍼런스' 폴더에 직접 넣고 다시 눌러도 됩니다.")
        result = reference.learn(update)
        result.update(downloaded=got["saved"], failed=got["failed"])
        return result

    return start_job("스타일 분석", work)


@app.post("/api/memo")
async def api_memo(request: Request):
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "메모 내용이 비어 있습니다.")
    reference.save_memo(text + "\n")
    return {"ok": True}


@app.post("/api/analyze")
async def api_analyze(request: Request):
    body = await request.json()
    d = config.dirs()
    files = body.get("files") or []
    return start_job("분석", lambda up: {"analyses": _analyses(files, d, up, body.get("transcribe", True))})


@app.post("/api/plan")
async def api_plan(request: Request):
    body = await request.json()
    d = config.dirs()
    files = body.get("files") or []
    if not files:
        raise HTTPException(400, "촬영본을 하나 이상 고르세요.")
    captions = (body.get("captions") or "").splitlines()

    def work(update):
        analyses = _analyses(files, d, update)
        update("편집 지시서 만드는 중", 0.9)
        if body.get("planner") == "claude":
            plan = planner.claude_plan(analyses, body.get("instruction", ""), captions, _project())
        else:
            plan = planner.rule_plan(analyses, body.get("mode", "auto"), captions,
                                     float(body.get("clip_len") or 3.0), _project())
        return _save(plan, d)

    return start_job("편집 계획", work)


@app.get("/api/edl/{version}")
def api_edl(version: int):
    d = config.dirs()
    try:
        return edl_mod.load(d["out"], _project(), version)
    except FileNotFoundError:
        raise HTTPException(404, "지시서가 없습니다.")


@app.post("/api/edl")
async def api_edl_save(request: Request):
    body = await request.json()
    d = config.dirs()
    plan = body.get("edl") or {}
    plan.update(planner="manual", based_on=plan.get("version"), note=body.get("note") or "화면에서 직접 고친 버전입니다.")
    return _save(plan, d)


@app.post("/api/revise")
async def api_revise(request: Request):
    body = await request.json()
    d = config.dirs()
    current = edl_mod.load(d["out"], _project(), int(body["version"]))
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "수정할 내용을 적어 주세요.")

    def work(update):
        update("수정 내용 반영하는 중", 0.3)
        files = list(dict.fromkeys(c["src"] for c in current["clips"]))
        analyses = _analyses(files, d, lambda m, p=None: None, do_transcribe=False)
        return _save(planner.revise(current, message, analyses), d)

    return start_job("수정", work)


@app.post("/api/render")
async def api_render(request: Request):
    body = await request.json()
    d = config.dirs()
    version, quality = int(body["version"]), body.get("quality", "preview")
    current, warnings = edl_mod.normalize(edl_mod.load(d["out"], _project(), version), d["src"])
    current["version"] = version
    if quality == "final":
        out = edl_mod.path_for(d["out"], _project(), version, "video")
        url = f"/media/out/{out.name}"
    else:
        out = config.WORK_DIR / "preview" / f"{_project()}_v{version}.mp4"
        url = f"/media/preview/{out.name}"

    def work(update):
        info = render.render(current, d["src"], out, quality, update)
        info.update(url=url, quality=quality, version=version, warnings=warnings + info["warnings"])
        return info

    return start_job("최종 내보내기" if quality == "final" else "미리보기", work)


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "작업을 찾을 수 없습니다.")
    return job


def _ranged(path: Path, request: Request, media_type: str):
    """영상 탐색(구간 요청)을 지원하는 파일 응답."""
    if not path.is_file():
        raise HTTPException(404)
    size = path.stat().st_size
    header = request.headers.get("range")
    if not header:
        return FileResponse(path, media_type=media_type, headers={"Accept-Ranges": "bytes", "Cache-Control": "no-store"})
    try:
        start_s, end_s = header.replace("bytes=", "").split("-")[:2]
        start = int(start_s) if start_s else max(0, size - int(end_s))
        end = int(end_s) if (end_s and start_s) else size - 1
    except ValueError:
        return Response(status_code=416)
    end = min(end, size - 1)
    if start > end:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})

    def body():
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1 << 18, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    return StreamingResponse(body(), status_code=206, media_type=media_type, headers={
        "Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(end - start + 1),
        "Accept-Ranges": "bytes", "Cache-Control": "no-store"})


@app.get("/media/{kind}/{name}")
def media(kind: str, name: str, request: Request):
    d = config.dirs()
    base = {"out": d["out"], "preview": config.WORK_DIR / "preview", "thumb": config.WORK_DIR / "thumbs"}.get(kind)
    if base is None:
        raise HTTPException(404)
    path = base / Path(name).name
    return _ranged(path, request, "image/jpeg" if kind == "thumb" else "video/mp4")
