from __future__ import annotations

from pathlib import Path
from threading import Thread

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import FileResponse

from . import catalog, converter
from .config import settings
from .titlegen.cache import title_argb, title_image

app = FastAPI(title="tjarepo")


@app.on_event("startup")
def startup() -> None:
    count = catalog.warm_song_index()
    print(
        f"[tjarepo] indexed {count} songs; "
        f"conversion workers={settings.conversion_workers}",
        flush=True,
    )
    # Build the library off the request path: the first build reads every
    # source file once and can take tens of seconds. Afterwards the watch
    # rebuilds it in the background on file changes, so /library and
    # /library/hash always answer instantly from memory.
    Thread(target=catalog.refresh_library, daemon=True, name="tjarepo-warm").start()
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


@app.get("/api/tjarepo/songs/categories", dependencies=[Depends(require_token)])
def categories() -> dict[str, object]:
    return {"categories": catalog.categories()}


@app.get("/api/tjarepo/library", dependencies=[Depends(require_token)])
def library() -> dict[str, object]:
    return catalog.library()


@app.get("/api/tjarepo/library/hash", dependencies=[Depends(require_token)])
def library_hash() -> dict[str, str]:
    return {"hash": catalog.library_hash()}


@app.get("/api/tjarepo/songs", dependencies=[Depends(require_token)])
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


@app.get("/api/tjarepo/songs/{song_id}", dependencies=[Depends(require_token)])
def show_song(song_id: str) -> dict[str, object]:
    entry = catalog.song(song_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Song not found")
    return catalog.public_song(entry)


@app.get("/api/tjarepo/songs/{song_id}/hash", dependencies=[Depends(require_token)])
def song_hash(song_id: str) -> dict[str, str]:
    """Cheap freshness check: the same source_hash the prepared manifest stores,
    so the PS3 can reuse its local cache when it matches and only re-download
    when the source files changed."""
    entry = catalog.song(song_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Song not found")
    return {"source_hash": catalog.source_hash(entry)}


@app.get("/api/tjarepo/songs/{song_id}/title/{variant}.png", dependencies=[Depends(require_token)])
def song_title_image(song_id: str, variant: str) -> FileResponse:
    path = title_image(song_id, variant)
    if path is None:
        raise HTTPException(status_code=404, detail="Title image not found")
    return FileResponse(path, media_type="image/png")


@app.get("/api/tjarepo/songs/{song_id}/title/{variant}.argb", dependencies=[Depends(require_token)])
def song_title_argb(song_id: str, variant: str) -> FileResponse:
    item = title_argb(song_id, variant)
    if item is None:
        raise HTTPException(status_code=404, detail="Title image not found")
    path, width, height = item
    return FileResponse(
        path,
        media_type="application/octet-stream",
        headers={
            "X-Title-Width": str(width),
            "X-Title-Height": str(height),
            "X-Title-Format": "A8R8G8B8",
        },
    )


@app.post("/api/tjarepo/songs/prepare-batch", dependencies=[Depends(require_token)])
def prepare_batch(song_ids: list[str] = Body(embed=True)) -> Response:
    if len(song_ids) > 4096:
        raise HTTPException(status_code=413, detail="Batch exceeds 4096 songs")
    valid_ids = [song_id for song_id in song_ids if song_id]
    data = converter.enqueue_many(valid_ids)
    return _json(data, 202)


@app.post("/api/tjarepo/songs/{song_id}/prepare", dependencies=[Depends(require_token)])
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


@app.get("/api/tjarepo/conversions/{song_id}", dependencies=[Depends(require_token)])
def conversion_status(song_id: str) -> Response:
    data = converter.status_for(song_id)
    return _json(data, 404 if data.get("status") == "not_found" else 200)


@app.get("/api/tjarepo/conversions/{song_id}/assets/{asset_path:path}", dependencies=[Depends(require_token)])
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
