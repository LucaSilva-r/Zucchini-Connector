from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from threading import Thread

from fastapi import APIRouter, Body, Depends, FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile, WebSocket, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import agents, auth, cabinets, catalog, control, converter, database, library_admin, updates
from .config import settings

app = FastAPI(title="zucchini-connector")
cabinet_api = APIRouter()
ui_api = APIRouter()
# Served on both listeners: over HTTPS for completeness, and over the plain
# agent port because webMAN has no TLS.
agent_api = APIRouter()


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
    start_agent_listener()


@app.on_event("shutdown")
def shutdown() -> None:
    converter.shutdown()


def require_token(authorization: str | None = Header(default=None)) -> None:
    if not settings.api_token:
        return
    if authorization != f"Bearer {settings.api_token}":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


def require_agent_token(
    request: Request, authorization: str | None = Header(default=None)
) -> None:
    """Auth for the agent channel only.

    Separate from require_token on purpose: this credential travels in clear
    on the LAN and is written to a file on every cabinet, so it must not be
    the TaikOnline card-issuer token that guards the catalog. An empty
    AGENT_TOKEN disables the check, matching require_token.

    Rejections are logged: a console whose token is stale polls forever and
    silently, which reads exactly like an agent that was never installed.
    """
    if not settings.agent_token:
        return
    if authorization != f"Bearer {settings.agent_token}":
        client = request.client.host if request.client else "unknown"
        sent = (authorization or "").removeprefix("Bearer ")
        print(
            f"[connector] agent poll REJECTED from {client} "
            f"id={request.query_params.get('id', '')!r} "
            f"token={sent[:8] + '…' if sent else '(none)'}",
            flush=True,
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


@agent_api.get("/poll", dependencies=[Depends(require_agent_token)])
async def agent_poll(
    id: str = "", state: str = "", cpu_temp: str = "", rsx_temp: str = "", fan: str = ""
) -> Response:
    """Long-poll for webMAN commands. Body is one command path per line.

    Plain text because the client is C inside webMAN with no JSON parser, and
    held open so an idle cabinet costs one request every POLL_HOLD_SECONDS
    rather than a busy loop.
    """
    if not id:
        raise HTTPException(status_code=400, detail="Cabinet id required")
    agents.hub.note_seen(id, state)
    agents.hub.note_sensors(id, cpu_temp, rsx_temp, fan)
    await asyncio.to_thread(cabinets.mark_agent_seen, id)
    commands = await agents.hub.wait(id)
    return Response(
        "".join(f"{command}\n" for command in commands), media_type="text/plain"
    )


@agent_api.post("/games", dependencies=[Depends(require_agent_token)])
async def agent_games(request: Request, id: str = "") -> dict[str, object]:
    """Receive the complete /dev_hdd0/game inventory from one VSH agent."""
    if not id:
        raise HTTPException(status_code=400, detail="Cabinet id required")
    try:
        report = agents.parse_games_report(await request.body())
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    saved = await asyncio.to_thread(
        cabinets.set_installed_games,
        id,
        report["installed_games"],
        report["autoboot_dir"],
        report["autoboot_delay"],
    )
    if saved is None:
        raise HTTPException(status_code=404, detail="Cabinet not found")
    return {
        "status": "stored",
        "games": len(report["installed_games"]),
    }


@agent_api.post("/health", dependencies=[Depends(require_agent_token)])
async def agent_health(request: Request, id: str = "") -> dict[str, object]:
    """Receive webMAN's console-info page, relayed verbatim by the agent.

    Held in memory only. It is a live reading, worthless once stale, and it
    arrives every two minutes per cabinet — a file write per push would buy
    nothing but disk churn.
    """
    if not id:
        raise HTTPException(status_code=400, detail="Cabinet id required")
    try:
        page = agents.parse_health(await request.body())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    agents.hub.note_health(id, page)
    return {"status": "stored"}


def _game_icon_path(cabinet_id: str, directory: str) -> Path:
    safe_id = "".join(c for c in cabinet_id if c.isalnum() or c in "-_")
    digest = hashlib.sha256(directory.encode("utf-8")).hexdigest()
    return settings.cabinets_root / "uploads" / safe_id / "games" / f"{digest}.png"


@agent_api.post("/game-icon", dependencies=[Depends(require_agent_token)])
async def agent_game_icon(
    request: Request, id: str = "", dir: str = ""
) -> dict[str, object]:
    if not id:
        raise HTTPException(status_code=400, detail="Cabinet id required")
    try:
        directory = agents.validate_game_directory(dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if cabinets.load(id) is None:
        raise HTTPException(status_code=404, detail="Cabinet not found")
    body = await request.body()
    if (
        len(body) < 8
        or len(body) > 4 * 1024 * 1024
        or not body.startswith(b"\x89PNG\r\n\x1a\n")
    ):
        raise HTTPException(status_code=400, detail="Invalid ICON0.PNG")
    path = _game_icon_path(id, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    await asyncio.to_thread(tmp.write_bytes, body)
    await asyncio.to_thread(os.replace, tmp, path)
    return {"status": "stored", "bytes": len(body)}


# Screenshots are XMB-only: webMAN's saveBMP() pauses the RSX FIFO and refuses
# to run in-game. Half-resolution 24-bit BMP, so ~430 KB; the ceiling is a
# sanity bound against a runaway agent, not a real limit.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
UPLOAD_KINDS = {"screenshot": "screenshot.bmp"}


def _upload_path(cabinet_id: str, kind: str) -> Path:
    safe = "".join(c for c in cabinet_id if c.isalnum() or c in "-_")
    return settings.cabinets_root / "uploads" / safe / UPLOAD_KINDS[kind]


@agent_api.post("/upload", dependencies=[Depends(require_agent_token)])
async def agent_upload(request: Request, id: str = "", kind: str = "") -> dict[str, object]:
    """Receive a file the agent captured on the console.

    Written to a temp file and renamed, so a half-finished upload never
    replaces the last good screenshot an operator is looking at.
    """
    if not id or kind not in UPLOAD_KINDS:
        raise HTTPException(status_code=400, detail="Unknown upload")
    body = await request.body()
    if not body or len(body) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Bad upload size")
    path = _upload_path(id, kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    await asyncio.to_thread(tmp.write_bytes, body)
    await asyncio.to_thread(os.replace, tmp, path)
    print(f"[connector] agent upload: {id} {kind} {len(body)} bytes", flush=True)
    return {"status": "stored", "bytes": len(body)}


# ----------------------------------------------------------------------
# File manager
#
# The console answers over the same held poll as every other agent verb, so
# these UI routes are synchronous from the operator's side and hold their
# request until the cabinet replies. The agent executes one command at a
# time, so a large pull blocks the next one until it finishes.
# ----------------------------------------------------------------------

FS_LIST_TIMEOUT = 45
FS_TRANSFER_TIMEOUT = 300


def _safe_id(cabinet_id: str) -> str:
    return "".join(c for c in cabinet_id if c.isalnum() or c in "-_")


def _pull_path(cabinet_id: str) -> Path:
    """Where the last file pulled off this cabinet lands. One slot: the
    operator's request is still waiting on it when it arrives."""
    return settings.cabinets_root / "uploads" / _safe_id(cabinet_id) / "pull.bin"


def _push_path(cabinet_id: str, kind: str) -> Path:
    if kind not in agents.PUSH_KINDS:
        raise HTTPException(status_code=400, detail="Unknown push target")
    return settings.cabinets_root / "uploads" / _safe_id(cabinet_id) / f"push-{kind}.bin"


async def _stream_to_file(request: Request, path: Path, limit: int) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    total = 0
    try:
        with tmp.open("wb") as fh:
            async for chunk in request.stream():
                total += len(chunk)
                if total > limit:
                    raise HTTPException(status_code=413, detail="File too large")
                fh.write(chunk)
        await asyncio.to_thread(os.replace, tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return total


@agent_api.post("/dir", dependencies=[Depends(require_agent_token)])
async def agent_dir(request: Request, id: str = "") -> dict[str, object]:
    """Receive one directory listing the agent was asked for."""
    if not id:
        raise HTTPException(status_code=400, detail="Cabinet id required")
    try:
        report = agents.parse_dir_report(await request.body())
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    agents.hub.note_result(id, "ls", report)
    return {"status": "stored", "entries": len(report["entries"])}


@agent_api.post("/file", dependencies=[Depends(require_agent_token)])
async def agent_file(
    request: Request, id: str = "", path: str = "", error: str = ""
) -> dict[str, object]:
    """Receive one file the agent read off the console.

    `error=1` with an empty body is the agent saying it could not open the
    path, so the operator gets an answer instead of a timeout. `path` is
    optional and only ever a label — the request still waiting on this file is
    the one that asked for it, and it kept the path it sent.
    """
    if not id:
        raise HTTPException(status_code=400, detail="Cabinet id required")
    if error:
        agents.hub.note_result(id, "get", {"path": path, "error": True})
        return {"status": "noted"}
    size = await _stream_to_file(request, _pull_path(id), agents.MAX_PULL_BYTES)
    agents.hub.note_result(id, "get", {"path": path, "size": size, "error": False})
    return {"status": "stored", "bytes": size}


@agent_api.get("/blob", dependencies=[Depends(require_agent_token)])
def agent_blob(id: str = "", kind: str = "") -> Response:
    """Serve the staged file for a `put`. The agent asks by kind, never by
    path — the destinations live in the agent's own table."""
    path = _push_path(id, kind)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Nothing staged for that cabinet")
    return FileResponse(path, media_type="application/octet-stream")


@agent_api.post("/blob", dependencies=[Depends(require_agent_token)])
def agent_blob_result(
    id: str = "", kind: str = "", ok: str = "", size: str = ""
) -> dict[str, object]:
    """The agent reporting whether the install actually happened."""
    agents.hub.note_result(
        id,
        "put",
        {"kind": kind, "ok": ok == "1", "size": int(size) if size.isdigit() else 0},
    )
    return {"status": "noted"}


@ui_api.post("/cabinets/{cabinet_id}/fs/list", dependencies=[Depends(auth.require_management)])
async def cabinet_fs_list(
    cabinet_id: str, path: str = Body(default="/dev_hdd0", embed=True)
) -> dict[str, object]:
    if cabinets.load(cabinet_id) is None:
        raise HTTPException(status_code=404, detail="Cabinet not found")
    try:
        command = agents.list_command(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    agents.hub.arm(cabinet_id, "ls")
    if not agents.hub.enqueue(cabinet_id, command):
        raise HTTPException(status_code=409, detail="Cabinet VSH agent is offline")
    report = await agents.hub.collect(cabinet_id, "ls", FS_LIST_TIMEOUT)
    if report is None:
        raise HTTPException(status_code=504, detail="The cabinet did not answer")
    if report["error"]:
        raise HTTPException(status_code=404, detail="No such directory on the console")
    return report


@ui_api.post("/cabinets/{cabinet_id}/fs/fetch", dependencies=[Depends(auth.require_management)])
async def cabinet_fs_fetch(cabinet_id: str, path: str = Body(embed=True)) -> Response:
    """Pull one file off the console and hand it straight to the browser."""
    if cabinets.load(cabinet_id) is None:
        raise HTTPException(status_code=404, detail="Cabinet not found")
    try:
        command = agents.fetch_command(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    agents.hub.arm(cabinet_id, "get")
    if not agents.hub.enqueue(cabinet_id, command):
        raise HTTPException(status_code=409, detail="Cabinet VSH agent is offline")
    result = await agents.hub.collect(cabinet_id, "get", FS_TRANSFER_TIMEOUT)
    if result is None:
        raise HTTPException(status_code=504, detail="The cabinet did not answer")
    if result.get("error"):
        raise HTTPException(status_code=404, detail="The console could not read that file")
    return FileResponse(
        _pull_path(cabinet_id),
        media_type="application/octet-stream",
        filename=path.rsplit("/", 1)[-1] or "download.bin",
        headers={"Cache-Control": "no-store"},
    )


async def _stage_push(upload: UploadFile, path: Path, require_self: bool) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    total = 0
    first = b""
    try:
        with tmp.open("wb") as fh:
            while chunk := await upload.read(256 * 1024):
                total += len(chunk)
                if total > agents.MAX_PUSH_BYTES:
                    raise ValueError("File exceeds the 32 MiB push limit")
                if len(first) < 4:
                    first = (first + chunk)[:4]
                fh.write(chunk)
        if not total:
            raise ValueError("File is empty")
        # The agent checks this too, on the side that would have to be
        # recovered by hand — an unsigned "SPRX" pushed over the agent itself
        # is the one failure with no way back in.
        if require_self and first != b"SCE\0":
            raise ValueError("Not a signed PS3 SELF/SPRX (missing SCE header)")
        await asyncio.to_thread(os.replace, tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return total


@ui_api.post("/cabinets/{cabinet_id}/fs/push", dependencies=[Depends(auth.require_management)])
async def cabinet_fs_push(
    cabinet_id: str, kind: str = Form(...), file: UploadFile = File(...)
) -> dict[str, object]:
    """Replace one of the three files a cabinet will accept.

    `kind` is never a path: agents.PUSH_KINDS and the agent's own table decide
    where each one lands, and the agent's config is in neither.
    """
    if cabinets.load(cabinet_id) is None:
        raise HTTPException(status_code=404, detail="Cabinet not found")
    if kind not in agents.PUSH_KINDS:
        raise HTTPException(status_code=400, detail="Unknown push target")
    staged = _push_path(cabinet_id, kind)
    try:
        size = await _stage_push(file, staged, agents.PUSH_KINDS[kind])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    agents.hub.arm(cabinet_id, "put")
    if not agents.hub.enqueue(cabinet_id, agents.push_command(kind)):
        raise HTTPException(status_code=409, detail="Cabinet VSH agent is offline")
    result = await agents.hub.collect(cabinet_id, "put", FS_TRANSFER_TIMEOUT)
    if result is None:
        raise HTTPException(status_code=504, detail="The cabinet did not answer")
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail="The console refused the file")
    print(f"[connector] pushed {kind} to {cabinet_id}: {size} bytes", flush=True)
    return {"status": "installed", "kind": kind, "bytes": size}


@cabinet_api.post("/cabinet/screenshot", dependencies=[Depends(require_token)])
async def cabinet_screenshot_upload(request: Request, id: str = "") -> dict[str, object]:
    """In-game capture, uploaded by the plugin over its own TLS socket."""
    return await agent_upload(request, id=id, kind="screenshot")


@ui_api.post("/cabinets/{cabinet_id}/screenshot", dependencies=[Depends(auth.require_management)])
async def cabinet_screenshot_request(cabinet_id: str) -> dict[str, str]:
    """Capture the cabinet's screen, from whichever half can actually do it.

    The plugin can grab a running game but dies with it; webMAN survives the
    game but refuses to capture while one runs. Between them every state is
    covered, so prefer the plugin when the game is up and fall back to the
    agent otherwise — the operator just presses one button.
    """
    if await control.hub.request_screenshot(cabinet_id):
        return {"status": "requested", "route": "plugin"}
    if agents.hub.enqueue(cabinet_id, "screenshot"):
        return {"status": "requested", "route": "agent"}
    raise HTTPException(
        status_code=409, detail="Neither the game nor a webMAN agent is reachable"
    )


@ui_api.get("/cabinets/{cabinet_id}/screenshot", dependencies=[Depends(auth.require_management)])
def cabinet_screenshot(cabinet_id: str) -> Response:
    path = _upload_path(cabinet_id, "screenshot")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No screenshot captured yet")
    return Response(
        path.read_bytes(),
        media_type="image/bmp",
        headers={"Cache-Control": "no-store", "X-Captured-At": str(int(path.stat().st_mtime))},
    )


def start_agent_listener() -> None:
    """Serve the agent route over plain HTTP on its own port.

    A separate app, not the main one: everything else here — the management UI,
    its cookies, the catalog — must not become reachable without TLS just
    because the agents cannot speak it.
    """
    if not settings.agent_port:
        return
    import uvicorn

    agent_app = FastAPI(title="zucchini-connector-agents")
    agent_app.include_router(agent_api, prefix="/api/agent")
    server = uvicorn.Server(
        uvicorn.Config(
            agent_app, host="0.0.0.0", port=settings.agent_port, log_level="warning"
        )
    )
    # uvicorn installs signal handlers only on the main thread; this one is a
    # daemon and dies with the process.
    server.install_signal_handlers = lambda: None
    Thread(target=server.run, daemon=True, name="connector-agents").start()
    print(f"[connector] webMAN agent listener on :{settings.agent_port}", flush=True)


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


def _installed_game(cab: dict, directory: str) -> dict[str, object] | None:
    return next(
        (
            game
            for game in cab.get("installed_games", [])
            if isinstance(game, dict) and game.get("directory") == directory
        ),
        None,
    )


@ui_api.get(
    "/cabinets/{cabinet_id}/games/{directory}/icon",
    dependencies=[Depends(auth.require_management)],
)
def cabinet_game_icon(cabinet_id: str, directory: str) -> Response:
    cab = cabinets.load(cabinet_id)
    if cab is None:
        raise HTTPException(status_code=404, detail="Cabinet not found")
    game = _installed_game(cab, directory)
    if game is None or not game.get("has_icon"):
        raise HTTPException(status_code=404, detail="Game icon not available")
    path = _game_icon_path(cabinet_id, directory)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Game icon not uploaded yet")
    return FileResponse(
        path,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=300"},
    )


@ui_api.post(
    "/cabinets/{cabinet_id}/games/refresh",
    dependencies=[Depends(auth.require_management)],
)
def cabinet_games_refresh(cabinet_id: str) -> dict[str, str]:
    if cabinets.load(cabinet_id) is None:
        raise HTTPException(status_code=404, detail="Cabinet not found")
    if not agents.hub.enqueue(cabinet_id, "inventory"):
        raise HTTPException(status_code=409, detail="Cabinet VSH agent is offline")
    return {"status": "requested"}


@ui_api.put(
    "/cabinets/{cabinet_id}/games/autoboot",
    dependencies=[Depends(auth.require_management)],
)
def cabinet_game_autoboot(
    cabinet_id: str,
    directory: str = Body(default="", embed=True),
    delay: int = Body(default=15, embed=True),
) -> dict[str, object]:
    cab = cabinets.load(cabinet_id)
    if cab is None:
        raise HTTPException(status_code=404, detail="Cabinet not found")
    if directory and _installed_game(cab, directory) is None:
        raise HTTPException(status_code=400, detail="Game is not installed on this cabinet")
    try:
        command = agents.autoboot_command(directory, delay)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not agents.hub.enqueue(cabinet_id, command):
        raise HTTPException(status_code=409, detail="Cabinet VSH agent is offline")
    return {
        "status": "requested",
        "directory": directory,
        "delay": max(0, min(600, int(delay))),
    }


@ui_api.post(
    "/cabinets/{cabinet_id}/games/launch",
    dependencies=[Depends(auth.require_management)],
)
def cabinet_game_launch(
    cabinet_id: str, directory: str = Body(embed=True)
) -> dict[str, str]:
    cab = cabinets.load(cabinet_id)
    if cab is None:
        raise HTTPException(status_code=404, detail="Cabinet not found")
    if _installed_game(cab, directory) is None:
        raise HTTPException(status_code=400, detail="Game is not installed on this cabinet")
    try:
        launch = agents.launch_command(directory)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # One queue batch is drained by one poll and executed line-by-line by the
    # VSH agent. webMAN does not return from the first line until XMB is ready;
    # only then can the proven game_ext_plugin launch command run.
    if not agents.hub.enqueue_many(
        cabinet_id,
        [
            "/xmb.ps3$exit;/wait.ps3?xmb;/wait.ps3?2",
            launch,
        ],
    ):
        raise HTTPException(status_code=409, detail="Cabinet VSH agent is offline")
    return {"status": "switching", "directory": directory}


@ui_api.websocket("/cabinets/{cabinet_id}/control")
async def cabinet_operator_control(
    websocket: WebSocket, cabinet_id: str
) -> None:
    await control.hub.operator(websocket, cabinet_id)


@ui_api.websocket("/cabinets/{cabinet_id}/events")
async def cabinet_events(websocket: WebSocket, cabinet_id: str) -> None:
    await control.hub.viewer(websocket, cabinet_id)


@ui_api.post("/cabinets/{cabinet_id}/exit", dependencies=[Depends(auth.require_management)])
async def cabinet_exit(cabinet_id: str) -> dict[str, str]:
    """Close the game on the cabinet. It lands on XMB, where the drum still
    works as a controller, so an operator can relaunch it remotely."""
    if not await control.hub.request_exit(cabinet_id):
        raise HTTPException(status_code=409, detail="Cabinet is not connected")
    return {"status": "closing"}


@ui_api.post(
    "/cabinets/{cabinet_id}/itaiko/{index}/read",
    dependencies=[Depends(auth.require_management)],
)
async def cabinet_itaiko_read(cabinet_id: str, index: int) -> dict[str, str]:
    if cabinets.load(cabinet_id) is None:
        raise HTTPException(status_code=404, detail="Cabinet not found")
    try:
        sent = await control.hub.request_itaiko_read(cabinet_id, index)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not sent:
        raise HTTPException(status_code=409, detail="Cabinet is not connected")
    return {"status": "requested"}


@ui_api.put(
    "/cabinets/{cabinet_id}/itaiko/{index}/settings",
    dependencies=[Depends(auth.require_management)],
)
async def cabinet_itaiko_settings(
    cabinet_id: str,
    index: int,
    itaiko_settings: dict[str, object] = Body(embed=True),
) -> dict[str, str]:
    if cabinets.load(cabinet_id) is None:
        raise HTTPException(status_code=404, detail="Cabinet not found")
    try:
        sent = await control.hub.request_itaiko_settings(
            cabinet_id, index, itaiko_settings
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not sent:
        raise HTTPException(status_code=409, detail="Cabinet is not connected")
    return {"status": "saving"}


# Fixed webMAN command chains. The browser picks an action name, never a path:
# whatever is sent here is executed verbatim by the CFW on the console.
# `restart_game` is the unattended plugin-update round trip — XMB leaves the
# cursor on the title that was just closed, so one X press relaunches it, and
# the new SPRX is read at launch.
# No shutdown action on purpose: nothing here can power a console back on, so
# a misclick means someone drives to the cabinet. Reboot covers every case
# shutdown would have.
WEBMAN_ACTIONS = {
    "restart_game": "/xmb.ps3$exit;/wait.ps3?xmb;/wait.ps3?5;/pad.ps3?cross",
    "exit_game": "/xmb.ps3$exit",
    "reboot": "/reboot.ps3?soft",
}

# Virtual pad. webMAN registers a debug (LDD) controller and inserts one 70 ms
# press per request, with the insert-into-game filter on, so these steer the
# XMB and a running title alike. Every edition but `lite` builds it.
#
# webMAN matches button names by substring (`strcasestr` in vpad.h), so each of
# these must name exactly one button and nothing else; the names below are the
# ones it looks for, spelled its way. `off` unregisters the virtual controller,
# which is the escape hatch if it ever bothers the real drum.
#
# These go through the same fixed map as the three above even though a button
# press is harmless, because the point of the map is that the browser never
# hands the CFW a path of its own making.
PAD_BUTTONS = (
    "up", "down", "left", "right",
    "cross", "circle", "square", "triangle",
    "L1", "L2", "R1", "R2", "L3", "R3",
    "select", "start", "psbtn",
    "off",
)
WEBMAN_ACTIONS |= {f"pad_{b.lower()}": f"/pad.ps3?{b}" for b in PAD_BUTTONS}


@ui_api.post("/cabinets/{cabinet_id}/webman", dependencies=[Depends(auth.require_management)])
async def cabinet_webman(cabinet_id: str, action: str = Body(embed=True)) -> dict[str, str]:
    """Run one preset webMAN command on the cabinet's console.

    Delivered by the webMAN agent, which lives in VSH and is therefore up even
    with no game running. The Zucchini plugin has no part in this: a PS3 game
    process cannot reach its own console, and the relay that tried crashed it.
    """
    path = WEBMAN_ACTIONS.get(action)
    if path is None:
        raise HTTPException(status_code=400, detail="Unknown webMAN action")
    if cabinets.load(cabinet_id) is None:
        raise HTTPException(status_code=404, detail="Cabinet not found")
    if not agents.hub.enqueue(cabinet_id, path):
        raise HTTPException(
            status_code=409,
            detail="No webMAN agent is connected for this cabinet",
        )
    return {"status": "sent", "action": action, "route": "agent"}


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


app.include_router(agent_api, prefix="/api/agent")
app.include_router(cabinet_api, prefix="/api/connector")
app.include_router(ui_api, prefix="/api/ui")
app.mount("/ui", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="ui")
