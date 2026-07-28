# Zucchini Connector

Management server for TaikoZucchini arcade cabinets, grown out of the tjarepo
conversion service. Two jobs:

1. **Song catalog + conversion** — browse TJA and osu! beatmap
   repositories and convert requested songs into Taiko PS3 custom-song assets.
2. **Remote cabinet management** — cabinets running zucchini.sprx hold a
   WebSocket open to the connector; operators use the web UI at `/ui` to
   rename cabinets, pick each
   cabinet's song selection, and queue config changes (including chassisinfo
   operator flags) without opening the cab or attaching a controller.

The service keeps its TJA and OSZ sources read-only and writes generated
packages into `storage/SONGS/CONVERTED`. Durable scan, conversion-job, package,
and cabinet-package metadata lives in `storage/connector.db`; the existing
`storage/cabinets/<cabinet_id>.json` files remain the rolling-compatible
cabinet control state.

## Run

Quick start on any machine (creates a venv, installs deps, self-signed TLS):

```sh
./run.sh        # Linux / macOS
.\run.ps1       # Windows (PowerShell)
```

For a permanent install, Docker:

```sh
docker compose up --build
```

Everything lives under `./storage`: song sources in `storage/SONGS/TJA` and
`storage/SONGS/OSU`, generated packages in `storage/SONGS/CONVERTED`, cabinet
state, TLS certificates, and `storage/ps3_at3tool.exe` (needed for song
conversion, plus `ffmpeg`, and `wine` on non-Windows hosts).

Web UI: `https://localhost:8443/ui` (enter the API token once; it is kept in
localStorage).

The local run scripts check the host firewall before starting. On Windows they
can add a Private/Domain Windows Defender Firewall rule; on Linux they can add
the port to an active UFW or firewalld configuration. Both ask before changing
firewall rules. Set `CONNECTOR_FIREWALL=0` to skip the check (for example, in
an unattended launch environment).

The UI is a Svelte 5/Vite application built from `frontend/` with
shadcn-svelte components checked into `frontend/src/lib/components/ui/`.
`docker compose build` builds it automatically. For local frontend work:

```sh
cd frontend
npm install
npm run dev
```

Vite proxies `/api` to `https://localhost:8443` and production builds are
written to `app/static/` for FastAPI to serve at `/ui/`.

API base:

```text
https://localhost:8443/api/connector
```

Configuration comes from `CONNECTOR_*` environment variables.

The compose file mounts:

- TJA source library from `./storage/SONGS/TJA`
- osu! beatmap archives from `./storage/SONGS/OSU`
- conversion cache at `./storage/SONGS/CONVERTED`
- cabinet management state at `./storage/cabinets`
- uploaded Zucchini version history at `./storage/updates`
- local TLS certificates at `./storage/certificates`
- Sony `ps3_at3tool.exe` from `./storage/ps3_at3tool.exe`

The `tja2fumen` converter is vendored in `app/tja2fumen`.

## Cabinet management (cabinet-initiated socket)

The cabinet is a pure client — no listening socket on the PS3, so it works
behind any NAT/firewall. It opens one WebSocket to
`/api/connector/cabinet/control` and keeps it open, reconnecting with capped
backoff if it drops. Two frame types go up it:

- `H\n` — the full snapshot: identity (`cabinet_id` auto-generated on first
  boot, operator-set `cabinet_name`, game variant, dongle serial), the
  cached-song list, and the raw `taiko_config.cfg`. Event-driven, never
  periodic: sent on connect, after a song job changes the library, after a
  config is applied, and on request. It is ~100 KiB and the cabinet builds and
  sends it on the same thread that services remote input, so sending it on a
  timer visibly stalls gameplay input.
- `T\n` — compact operation telemetry, sent on change and at least every 10 s.

The connector pushes `M\n` command snapshots down the same socket whenever the
operator's queued state changes, and `R\n` to request a fresh `H\n` when it
needs one (an operator opening a dashboard against state this process never
saw). `M\n` carries:

- **Config changes** (`section.key = value`, e.g. `chassis.force_freeplay`)
  are applied through the same validation the config file parser uses, then
  saved. Network keys apply live; features/patches at next boot.
- **Song selection**: once an operator saves a selection the cabinet is
  *managed*. A separate worker converts and downloads into an isolated staging
  area during any game state; gameplay no longer has to sit in attract for
  network work to progress. Every asset is checked against the package
  manifest's size and SHA-1. Only the short directory swap/reload waits for
  attract and service mode. The previous working package remains playable if
  conversion, transfer, verification, or activation fails. Interrupted
  transfers resume, interrupted swaps recover from their rollback directory,
  and telemetry frames continue reporting phase and song-count progress.
  Operator edits made while a job runs are retained as the next desired
  sequence, separately from the sequence that is actually active.
- **Zucchini updates**: an operator can upload any signed `zucchini.sprx` with
  a version and change note, or select an earlier stored build to roll back a
  cabinet. The Connector rejects a HEN/GEX signing-flavor mismatch and stores
  artifacts by SHA-1. The cabinet streams the selected file in resumable
  chunks, verifies its SHA-1, and atomically swaps the runtime plugin, keeping
  the previous build until the swap succeeds. The running plugin is already
  mapped in memory, so the swap is safe in any game state and the new build
  takes effect at the cabinet's next launch (which also re-patches its EBOOT).
  The acknowledgement is the SHA-1 of the plugin actually on disk, so a build
  installed by hand reports itself correctly too. A cabinet never restarts
  itself: the **Close game** button on the Control tab does it on request,
  behind a confirmation, once an operator is watching. The game exits to XMB,
  where the drum still works as a controller, so the same remote controls
  relaunch it — and the relaunch is what applies the new build. On a console
  running webMAN MOD, **Restart game** does that round trip unattended (below).

### Console commands (webMAN agent)

Commands that must outlive the game — reboot, exit to XMB, relaunch the title —
are webMAN MOD web commands, delivered by a standalone ~9 KB VSH plugin
([zucchini-webman-agent]) — not a webMAN fork, so webMAN stays stock and
updatable. It lives in VSH, so it is up whenever the console is; the Zucchini
plugin cannot do this job, because it dies with the game and a PS3 game process
has no route to its own console.

The agent long-polls `/api/agent/poll` and runs each returned path through
webMAN's own HTTP server on the console's loopback. The browser only picks an
action name; paths are a fixed table in `app/main.py` (`restart_game`,
`exit_game`, `reboot`). There is no shutdown action on purpose: nothing here
can power a console back on.
`restart_game` is the unattended plugin-update round trip:
`/xmb.ps3$exit;/wait.ps3?xmb;/wait.ps3?5;/pad.ps3?cross`.

- **Transport**: outbound only, so cabinets behind NAT need no forwarding.
  webMAN has no TLS, so this route gets its **own plain-HTTP listener** on
  `AGENT_PORT` (default 8080) serving that single endpoint — the UI, its
  cookies, and the catalog stay on HTTPS. Keep the agent port on the arcade LAN.
- **Credential**: a dedicated `AGENT_TOKEN`, generated once into
  `storage/agent_token`. Deliberately *not* the catalog/API token, which also
  mints TaikOnline cards: the agent token crosses the LAN in clear and is
  stored on every cabinet's disk. The connector provisions it automatically —
  when a cabinet reports a `network.agent_token` that differs, the value is
  queued through the normal config channel, the plugin saves it, and the agent
  picks it up on its next config reload. No operator step.
- **Presence**: `agent_online` / `agent_state` on the cabinet record, available
  even while the cabinet's own control socket is down. The connector logs one
  line when an agent comes online.
- **Screenshots**: one button, route chosen server-side. The plugin captures a
  running game (webMAN refuses to while a game plays); the agent captures the
  XMB (the plugin is dead then). Agent capture needs a webMAN built with
  `XMB_SCREENSHOT` — `[Full]` has it, `[Rebug-PS3MAPI]` and `[lite]` do not.
  The button itself is not part of the console-control panel: it is read-only,
  and on a cabinet with no agent the plugin still serves it.

  **Under RPCS3 captures are black.** The plugin reads the RSX local memory the
  game scans out, which on real hardware is the displayed frame; RPCS3 renders
  on the host GPU and only writes it back to guest memory when
  *Configuration → GPU → Write Color Buffers* is enabled. Not a fault on
  hardware, which is the case that matters.

[zucchini-webman-agent]: https://github.com/LucaSilva-r/zucchini-webman-agent

### Reported build and custom-song support

The cabinet reports its build code (`game=ST71`), the full build id the game
prints on its own boot-check screen (`build=ST7100-1-…`), and `song_inject`,
which is the plugin's own answer to "did the patcher resolve this build's
song-select injection sites". Custom songs are still downloaded and installed
on a build without it, they just never appear in song select, so the dashboard
and the song picker say so rather than letting an operator find out later.
Build names are keyed on the series part of the code (`ST7` = White) because
one game ships under several variant digits.

Changes queued while a cabinet is offline persist and are delivered when it
reconnects. Cabinet liveness is socket presence — there is no HTTP polling and
no poll endpoint.

At boot, chassisinfo.xml synthesis blocks until the first command snapshot
arrives on the socket, so operator flags queued overnight apply as the cabinet
powers on. That wait is bounded (8 s); when the connector is unreachable or the
network comes up after the deadline, the flags apply on the following boot
instead.

**Trust model:** one shared bearer token gates everything (catalog and
management). Run the connector on the arcade's private LAN; there is no
per-cabinet auth.

## osu!taiko archives

Place each `.osz` file directly in a category folder such as
`storage/SONGS/OSU/Anime`. TJA packages use the matching folder under
`storage/SONGS/TJA`; both source types are merged into the same plain-name
category. The eight standard category folders are created automatically.

Only native osu!taiko charts (`Mode: 1`) are indexed. Each OSZ remains one song
with at most five selected courses. Easy, Normal, Hard, and Oni are always
present; the closest available chart is reused for missing required courses.
Ura is optional. Charts are matched using their calculated offline osu!taiko
difficulty and common difficulty names. The displayed Taiko level is
`round(osu!taiko stars * 1.5)`, clamped to 1–10.

Set `CONNECTOR_API_TOKEN` to require `Authorization: Bearer <token>` from
cabinet clients. The browser-facing song library and selection tools are
available to anyone who can reach the connector. Privileged actions (remote
control, cabinet configuration, Zucchini updates, and forgetting a cabinet)
require `CONNECTOR_MANAGEMENT_PIN`. The unlock is stored in an HttpOnly,
SameSite cookie for eight hours; change the lifetime with
`CONNECTOR_MANAGEMENT_SESSION_SECONDS`. For compatibility, the API token is
also accepted as the management PIN when no dedicated PIN is configured.
Repeated wrong PIN attempts are rate-limited per client.

Cabinets keep one authenticated WebSocket open for remote input and management
traffic. Song selections and configuration snapshots are pushed over that
channel, while conversion manifests and asset bytes continue to use HTTP.
Compact operation telemetry includes the active song, asset byte progress, and
measured transfer speed. Dashboard pages subscribe to a separate read-only
events socket, so progress updates appear immediately. There is no recurring
HTTP poll; inventory and configuration snapshots ride the same socket as `H\n`
heartbeats.

Batch conversion uses a bounded worker pool so several queued songs can be
prepared in parallel while clients download assets sequentially. Set
`CONNECTOR_CONVERSION_WORKERS` to control concurrency (default: `4` in Docker,
or up to `4` based on detected CPUs outside Docker).

Package identity is content-addressed: the source-content revision is combined
with `CONNECTOR_PACKAGE_RECIPE_VERSION`, manifest schema, chart endianness, and
audio settings. Bump `CONNECTOR_PACKAGE_RECIPE_VERSION` whenever converter
behavior can change generated bytes. Existing packages whose asset hashes still
match a new manifest are adopted without downloading them again.

Conversion attempts and retry deadlines survive connector restarts in SQLite.
Set `CONNECTOR_DATABASE_PATH` to move the database,
`CONNECTOR_CONVERSION_TIMEOUT_SECONDS` to bound a converter subprocess
(default 900), and `CONNECTOR_LIBRARY_FULL_RESCAN_SECONDS` to control the
watchdog safety scan (default 300).

The FastAPI app serves HTTPS directly on `CONNECTOR_HTTPS_PORT` (`8443` by
default), so it does not conflict with TaikOnline's local `443`. The container
generates a self-signed certificate on first start and stores it under
`storage/certificates/local`.

Set `CONNECTOR_TLS_ENABLED=0` to run the same container as plain HTTP instead.
