from __future__ import annotations

from pathlib import Path
from threading import Thread

from fastapi import APIRouter, Body, Depends, FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile, WebSocket, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import auth, cabinets, catalog, control, converter, database, library_admin, updates
from .config import settings

app = FastAPI(title="zucchini-connector")
cabinet_api = APIRouter()
ui_api = APIRouter()


@app.on_event("startup")
def startup() -> None:
    database.initialize()
    catalog.ensure_category_dirs()
    count = catalog.warm_song_index()
    converter.refresh_broken_index()
    print(
        f"[connector] indexed {count} songs; "
        f"conversion workers={settings.conversion_workers}",
        flush=True,
    )
    # Build the library off the request path: the first build reads every
    # source file once and can take tens of seconds. Afterwards the watch
    # rebuilds it in the background on file changes, so /library and
    # /library/hash always answer instantly from memory.
    Thread(target=catalog.refresh_library, daemon=True, name="connector-warm").start()
    catalog.start_library_watch()
    converter.resume_jobs()


@app.on_event("shutdown")
def shutdown() -> None:
    converter.shutdown()


def require_token(authorization: str | None = Header(default=None)) -> None:
    if not settings.api_token:
        return
    if authorization != f"Bearer {settings.api_token}":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


@app.get("/health")
def health() -> dict[str, object]:
    return {"status": "ok", "scan": database.scan_health()}


@ui_api.get("/auth/status")
def management_status(request: Request) -> dict[str, bool]:
    return {
        "configured": auth.configured(),
        "unlocked": auth.cookie_valid(request.cookies.get(auth.COOKIE_NAME)),
    }


@ui_api.post("/auth/pin")
def management_login(
    request: Request, response: Response, pin: str = Body(embed=True)
) -> dict[str, bool]:
    if not auth.configured():
        raise HTTPException(status_code=503, detail="Management PIN is not configured")
    client = request.client.host if request.client else "unknown"
    auth.check_login_rate(client)
    matched = auth.pin_matches(pin)
    auth.record_login(client, matched)
    if not matched:
        raise HTTPException(status_code=401, detail="Incorrect management PIN")
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    response.set_cookie(
        auth.COOKIE_NAME,
        auth.issue_cookie(),
        max_age=settings.management_session_seconds,
        httponly=True,
        secure=request.url.scheme == "https" or forwarded_proto == "https",
        samesite="strict",
        path="/",
    )
    return {"configured": True, "unlocked": True}


@ui_api.post("/auth/logout")
def management_logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(auth.COOKIE_NAME, path="/", samesite="strict")
    return {"configured": auth.configured(), "unlocked": False}


@cabinet_api.get("/songs/categories", dependencies=[Depends(require_token)])
def categories() -> dict[str, object]:
    return {"categories": library_admin.available_library()["categories"]}


@ui_api.get("/library")
@cabinet_api.get("/library", dependencies=[Depends(require_token)])
def library() -> dict[str, object]:
    return library_admin.available_library()


@cabinet_api.get("/library/hash", dependencies=[Depends(require_token)])
def library_hash() -> dict[str, str]:
    return {"hash": str(library_admin.available_library()["hash"])}


@ui_api.get("/library/manage")
def manage_library() -> dict[str, object]:
    return library_admin.management_library()


@ui_api.post("/library/upload/osz", dependencies=[Depends(auth.require_management)])
async def library_upload_osz(
    file: UploadFile = File(...), category: str = Form(...)
) -> dict[str, object]:
    try:
        return await library_admin.upload_osz(file, category)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@ui_api.post("/library/upload/tja", dependencies=[Depends(auth.require_management)])
async def library_upload_tja(
    files: list[UploadFile] = File(...), category: str = Form(...)
) -> dict[str, object]:
    try:
        return await library_admin.upload_tja(files, category)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@ui_api.post("/library/songs/delete-batch", dependencies=[Depends(auth.require_management)])
def library_delete_songs(song_ids: list[str] = Body(embed=True)) -> dict[str, object]:
    if len(song_ids) > 4096:
        raise HTTPException(status_code=413, detail="Batch exceeds 4096 songs")
    try:
        return library_admin.delete_songs(song_ids)
    except (ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@ui_api.delete("/library/songs/{song_id}", dependencies=[Depends(auth.require_management)])
def library_delete_song(song_id: str) -> dict[str, str]:
    try:
        return library_admin.delete_song(song_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@ui_api.post("/library/convert-all", dependencies=[Depends(auth.require_management)])
def library_convert_all(include_failed: bool = False) -> Response:
    return _json(converter.convert_all(include_failed=include_failed), 202)


@ui_api.post("/library/songs/reconvert-batch", dependencies=[Depends(auth.require_management)])
def library_reconvert_songs(song_ids: list[str] = Body(embed=True)) -> Response:
    if len(song_ids) > 4096:
        raise HTTPException(status_code=413, detail="Batch exceeds 4096 songs")
    return _json(converter.reconvert_many([s for s in song_ids if s]), 202)


@ui_api.post("/library/songs/{song_id}/retry", dependencies=[Depends(auth.require_management)])
def library_retry_song(song_id: str) -> Response:
    data = converter.retry(song_id)
    return _json(data, 404 if data.get("status") == "not_found" else 202)


@cabinet_api.get("/songs", dependencies=[Depends(require_token)])
def songs(category: str | None = None, offset: int = 0, limit: int = 48) -> dict[str, object]:
    limit = max(1, min(200, limit))
    offset = max(0, offset)
    entries = catalog.songs(category)
    page = entries[offset:offset + limit]
    return {
        "songs": [catalog.public_song(s) for s in page],
        "total": len(entries),
        "offset": offset,
        "limit": limit,
    }


@cabinet_api.get("/songs/{song_id}", dependencies=[Depends(require_token)])
def show_song(song_id: str) -> dict[str, object]:
    entry = catalog.song(song_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Song not found")
    return catalog.public_song(entry)


@cabinet_api.get("/songs/{song_id}/hash", dependencies=[Depends(require_token)])
def song_hash(song_id: str) -> dict[str, str]:
    """Cheap freshness check: the same source_hash the prepared manifest stores,
    so the PS3 can reuse its local cache when it matches and only re-download
    when the source files changed."""
    entry = catalog.song(song_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Song not found")
    return {"source_hash": catalog.source_hash(entry)}


@cabinet_api.post("/songs/prepare-batch", dependencies=[Depends(require_token)])
def prepare_batch(song_ids: list[str] = Body(embed=True)) -> Response:
    if len(song_ids) > 4096:
        raise HTTPException(status_code=413, detail="Batch exceeds 4096 songs")
    valid_ids = [song_id for song_id in song_ids if song_id]
    data = converter.enqueue_many(valid_ids)
    return _json(data, 202)


@cabinet_api.post("/songs/{song_id}/prepare", dependencies=[Depends(require_token)])
def prepare(song_id: str) -> Response:
    data = converter.enqueue(song_id)
    code = {
        "ready": 200,
        "queued": 202,
        "processing": 202,
        "not_found": 404,
        "failed": 500,
    }.get(str(data.get("status")), 500)
    return _json(data, code)


@cabinet_api.get("/conversions/{song_id}", dependencies=[Depends(require_token)])
def conversion_status(song_id: str) -> Response:
    data = converter.status_for(song_id)
    return _json(data, 404 if data.get("status") == "not_found" else 200)


@cabinet_api.get("/conversions/{song_id}/assets/{asset_path:path}", dependencies=[Depends(require_token)])
def asset(song_id: str, asset_path: str, request: Request) -> Response:
    item = converter.asset(song_id, asset_path)
    if item is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    path = Path(item["path"])
    size = path.stat().st_size
    max_length = max(1, settings.asset_chunk_bytes)
    offset = max(0, int(request.query_params.get("offset", "0")))
    length = min(max_length, max(1, int(request.query_params.get("length", str(max_length)))))
    if offset >= size:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}", "X-Asset-Size": str(size)})
    length = min(length, size - offset)
    with path.open("rb") as fh:
        fh.seek(offset)
        body = fh.read(length)
    return Response(
        body,
        status_code=200 if offset == 0 and len(body) == size else 206,
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(len(body)),
            "Content-Range": f"bytes {offset}-{offset + len(body) - 1}/{size}",
            "X-Asset-Name": asset_path,
            "X-Asset-Size": str(size),
            "X-Asset-Sha1": str(item["sha1"]),
            "X-Chunk-Offset": str(offset),
        },
    )


@cabinet_api.get("/updates/{update_id}", dependencies=[Depends(require_token)])
def update_asset(update_id: str, request: Request) -> Response:
    item = updates.artifact(update_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Update not found")
    path = Path(item["path"])
    size = int(item["size"])
    max_length = max(1, settings.asset_chunk_bytes)
    offset = max(0, int(request.query_params.get("offset", "0")))
    length = min(max_length, max(1, int(request.query_params.get("length", str(max_length)))))
    if offset >= size:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}", "X-Asset-Size": str(size)})
    length = min(length, size - offset)
    with path.open("rb") as fh:
        fh.seek(offset)
        body = fh.read(length)
    return Response(
        body,
        status_code=200 if offset == 0 and len(body) == size else 206,
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(len(body)),
            "Content-Range": f"bytes {offset}-{offset + len(body) - 1}/{size}",
            "X-Asset-Name": "zucchini.sprx",
            "X-Asset-Size": str(size),
            "X-Asset-Sha1": update_id,
            "X-Chunk-Offset": str(offset),
        },
    )


def _json(data: dict[str, object], code: int) -> Response:
    import json

    return Response(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        status_code=code,
        media_type="application/json",
    )


@cabinet_api.websocket("/cabinet/control")
async def cabinet_control(websocket: WebSocket, id: str = "") -> None:
    await control.hub.cabinet(websocket, id)


@ui_api.get("/cabinets")
def cabinet_list() -> dict[str, object]:
    return {"cabinets": [control.hub.decorate(cab) for cab in cabinets.list_all()]}


@ui_api.get("/updates", dependencies=[Depends(auth.require_management)])
def update_list() -> dict[str, object]:
    return {"updates": updates.list_artifacts()}


@ui_api.get("/cabinets/{cabinet_id}")
def cabinet_show(cabinet_id: str) -> dict[str, object]:
    cab = cabinets.load(cabinet_id)
    if cab is None:
        raise HTTPException(status_code=404, detail="Cabinet not found")
    return control.hub.decorate(cab)


@ui_api.websocket("/cabinets/{cabinet_id}/control")
async def cabinet_operator_control(
    websocket: WebSocket, cabinet_id: str
) -> None:
    await control.hub.operator(websocket, cabinet_id)


@ui_api.websocket("/cabinets/{cabinet_id}/events")
async def cabinet_events(websocket: WebSocket, cabinet_id: str) -> None:
    await control.hub.viewer(websocket, cabinet_id)


@ui_api.delete("/cabinets/{cabinet_id}", dependencies=[Depends(auth.require_management)])
def cabinet_delete(cabinet_id: str) -> dict[str, str]:
    if not cabinets.delete(cabinet_id):
        raise HTTPException(status_code=404, detail="Cabinet not found")
    return {"status": "deleted"}


@ui_api.post("/cabinets/{cabinet_id}/resync", dependencies=[Depends(auth.require_management)])
def cabinet_resync(cabinet_id: str) -> dict[str, object]:
    cab = cabinets.force_resync(cabinet_id)
    if cab is None:
        raise HTTPException(status_code=404, detail="Cabinet not found")
    if not cab["managed"]:
        raise HTTPException(status_code=409, detail="Cabinet has no managed selection to resync")
    return cab


@ui_api.put("/cabinets/{cabinet_id}/selection")
def cabinet_selection(cabinet_id: str, song_ids: list[str] = Body(embed=True)) -> dict[str, object]:
    cab = cabinets.set_selection(cabinet_id, song_ids)
    if cab is None:
        raise HTTPException(status_code=404, detail="Cabinet not found")
    return cab


@ui_api.put("/cabinets/{cabinet_id}/config", dependencies=[Depends(auth.require_management)])
def cabinet_config(cabinet_id: str, config: dict[str, str] = Body(embed=True)) -> dict[str, object]:
    try:
        cab = cabinets.set_config(cabinet_id, config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if cab is None:
        raise HTTPException(status_code=404, detail="Cabinet not found")
    return cab


@ui_api.post("/cabinets/{cabinet_id}/update", dependencies=[Depends(auth.require_management)])
async def cabinet_update(
    cabinet_id: str,
    file: UploadFile = File(...),
    version: str = Form(...),
    note: str = Form(""),
) -> dict[str, object]:
    cab = cabinets.load(cabinet_id)
    if cab is None:
        raise HTTPException(status_code=404, detail="Cabinet not found")
    try:
        artifact = await updates.store_upload(
            file, version, str(cab.get("flavor", "")), note
        )
        queued = cabinets.queue_update(cabinet_id, artifact)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if queued is None:
        raise HTTPException(status_code=404, detail="Cabinet not found")
    return queued


@ui_api.post("/cabinets/{cabinet_id}/update/{update_id}", dependencies=[Depends(auth.require_management)])
def cabinet_update_from_history(cabinet_id: str, update_id: str) -> dict[str, object]:
    cab = cabinets.load(cabinet_id)
    if cab is None:
        raise HTTPException(status_code=404, detail="Cabinet not found")
    item = updates.artifact(update_id)
    if item is None or "version" not in item:
        raise HTTPException(status_code=404, detail="Stored update not found")
    cabinet_flavor = str(cab.get("flavor", ""))
    artifact_flavor = str(item.get("flavor", ""))
    if cabinet_flavor and artifact_flavor != cabinet_flavor:
        raise HTTPException(
            status_code=400,
            detail=f"That update is for {artifact_flavor.upper()}, but this cabinet runs {cabinet_flavor.upper()}",
        )
    queued_artifact = {key: value for key, value in item.items() if key != "path"}
    try:
        queued = cabinets.queue_update(cabinet_id, queued_artifact)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if queued is None:
        raise HTTPException(status_code=404, detail="Cabinet not found")
    return queued


@ui_api.delete("/cabinets/{cabinet_id}/update", dependencies=[Depends(auth.require_management)])
def cabinet_update_cancel(cabinet_id: str) -> dict[str, object]:
    try:
        cab = cabinets.cancel_update(cabinet_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if cab is None:
        raise HTTPException(status_code=404, detail="Cabinet not found")
    return cab


@app.get("/")
def root() -> Response:
    return Response(status_code=307, headers={"Location": "/ui/"})


@app.get("/ui")
def ui_redirect() -> Response:
    return Response(status_code=307, headers={"Location": "/ui/"})


# /api/tjarepo is the legacy mount for sprx builds predating the rename.
app.include_router(cabinet_api, prefix="/api/connector")
app.include_router(cabinet_api, prefix="/api/tjarepo")
app.include_router(ui_api, prefix="/api/ui")
app.mount("/ui", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="ui")
