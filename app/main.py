from __future__ import annotations

from pathlib import Path
from threading import Thread

from fastapi import APIRouter, Body, Depends, FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import cabinets, catalog, converter, library_admin
from .config import settings

app = FastAPI(title="zucchini-connector")
api = APIRouter()


@app.on_event("startup")
def startup() -> None:
    count = catalog.warm_song_index()
    broken = converter.refresh_broken_index()
    cabinets.remove_songs_everywhere(broken)
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


@app.on_event("shutdown")
def shutdown() -> None:
    converter.shutdown()


def require_token(authorization: str | None = Header(default=None)) -> None:
    if not settings.api_token:
        return
    if authorization != f"Bearer {settings.api_token}":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@api.get("/songs/categories", dependencies=[Depends(require_token)])
def categories() -> dict[str, object]:
    return {"categories": library_admin.available_library()["categories"]}


@api.get("/library", dependencies=[Depends(require_token)])
def library() -> dict[str, object]:
    return library_admin.available_library()


@api.get("/library/hash", dependencies=[Depends(require_token)])
def library_hash() -> dict[str, str]:
    return {"hash": str(library_admin.available_library()["hash"])}


@api.get("/library/manage", dependencies=[Depends(require_token)])
def manage_library() -> dict[str, object]:
    return library_admin.management_library()


@api.post("/library/upload/osz", dependencies=[Depends(require_token)])
async def library_upload_osz(
    file: UploadFile = File(...), category: str = Form("root")
) -> dict[str, object]:
    try:
        return await library_admin.upload_osz(file, category)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.post("/library/upload/tja", dependencies=[Depends(require_token)])
async def library_upload_tja(
    files: list[UploadFile] = File(...), category: str = Form("root")
) -> dict[str, object]:
    try:
        return await library_admin.upload_tja(files, category)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.post("/library/songs/delete-batch", dependencies=[Depends(require_token)])
def library_delete_songs(song_ids: list[str] = Body(embed=True)) -> dict[str, object]:
    if len(song_ids) > 4096:
        raise HTTPException(status_code=413, detail="Batch exceeds 4096 songs")
    try:
        return library_admin.delete_songs(song_ids)
    except (ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api.delete("/library/songs/{song_id}", dependencies=[Depends(require_token)])
def library_delete_song(song_id: str) -> dict[str, str]:
    try:
        return library_admin.delete_song(song_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api.post("/library/songs/{song_id}/retry", dependencies=[Depends(require_token)])
def library_retry_song(song_id: str) -> Response:
    data = converter.retry(song_id)
    return _json(data, 404 if data.get("status") == "not_found" else 202)


@api.get("/songs", dependencies=[Depends(require_token)])
def songs(category: str | None = None, offset: int = 0, limit: int = 48) -> dict[str, object]:
    limit = max(1, min(200, limit))
    offset = max(0, offset)
    entries = catalog.songs(category)
    broken = converter.broken_song_ids()
    entries = [entry for entry in entries if entry["id"] not in broken]
    page = entries[offset:offset + limit]
    return {
        "songs": [catalog.public_song(s) for s in page],
        "total": len(entries),
        "offset": offset,
        "limit": limit,
    }


@api.get("/songs/{song_id}", dependencies=[Depends(require_token)])
def show_song(song_id: str) -> dict[str, object]:
    entry = catalog.song(song_id)
    if entry is None or song_id in converter.broken_song_ids():
        raise HTTPException(status_code=404, detail="Song not found")
    return catalog.public_song(entry)


@api.get("/songs/{song_id}/hash", dependencies=[Depends(require_token)])
def song_hash(song_id: str) -> dict[str, str]:
    """Cheap freshness check: the same source_hash the prepared manifest stores,
    so the PS3 can reuse its local cache when it matches and only re-download
    when the source files changed."""
    entry = catalog.song(song_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Song not found")
    return {"source_hash": catalog.source_hash(entry)}


@api.post("/songs/prepare-batch", dependencies=[Depends(require_token)])
def prepare_batch(song_ids: list[str] = Body(embed=True)) -> Response:
    if len(song_ids) > 4096:
        raise HTTPException(status_code=413, detail="Batch exceeds 4096 songs")
    valid_ids = [song_id for song_id in song_ids if song_id]
    data = converter.enqueue_many(valid_ids)
    return _json(data, 202)


@api.post("/songs/{song_id}/prepare", dependencies=[Depends(require_token)])
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


@api.get("/conversions/{song_id}", dependencies=[Depends(require_token)])
def conversion_status(song_id: str) -> Response:
    data = converter.status_for(song_id)
    return _json(data, 404 if data.get("status") == "not_found" else 200)


@api.get("/conversions/{song_id}/assets/{asset_path:path}", dependencies=[Depends(require_token)])
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


def _json(data: dict[str, object], code: int) -> Response:
    import json

    return Response(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        status_code=code,
        media_type="application/json",
    )


@api.post("/cabinet/poll", dependencies=[Depends(require_token)])
async def cabinet_poll(request: Request) -> Response:
    body = (await request.body()).decode("utf-8", errors="replace")
    return Response(cabinets.handle_poll(body), media_type="text/plain")


@api.get("/cabinets", dependencies=[Depends(require_token)])
def cabinet_list() -> dict[str, object]:
    return {"cabinets": cabinets.list_all()}


@api.get("/cabinets/{cabinet_id}", dependencies=[Depends(require_token)])
def cabinet_show(cabinet_id: str) -> dict[str, object]:
    cab = cabinets.load(cabinet_id)
    if cab is None:
        raise HTTPException(status_code=404, detail="Cabinet not found")
    return cab


@api.delete("/cabinets/{cabinet_id}", dependencies=[Depends(require_token)])
def cabinet_delete(cabinet_id: str) -> dict[str, str]:
    if not cabinets.delete(cabinet_id):
        raise HTTPException(status_code=404, detail="Cabinet not found")
    return {"status": "deleted"}


@api.post("/cabinets/{cabinet_id}/resync", dependencies=[Depends(require_token)])
def cabinet_resync(cabinet_id: str) -> dict[str, object]:
    cab = cabinets.force_resync(cabinet_id)
    if cab is None:
        raise HTTPException(status_code=404, detail="Cabinet not found")
    if not cab["managed"]:
        raise HTTPException(status_code=409, detail="Cabinet has no managed selection to resync")
    return cab


@api.put("/cabinets/{cabinet_id}/selection", dependencies=[Depends(require_token)])
def cabinet_selection(cabinet_id: str, song_ids: list[str] = Body(embed=True)) -> dict[str, object]:
    broken = converter.broken_song_ids()
    cab = cabinets.set_selection(cabinet_id, [song_id for song_id in song_ids if song_id not in broken])
    if cab is None:
        raise HTTPException(status_code=404, detail="Cabinet not found")
    return cab


@api.put("/cabinets/{cabinet_id}/config", dependencies=[Depends(require_token)])
def cabinet_config(cabinet_id: str, config: dict[str, str] = Body(embed=True)) -> dict[str, object]:
    try:
        cab = cabinets.set_config(cabinet_id, config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
app.include_router(api, prefix="/api/connector")
app.include_router(api, prefix="/api/tjarepo")
app.mount("/ui", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="ui")
