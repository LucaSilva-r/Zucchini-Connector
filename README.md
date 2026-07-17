# Zucchini Connector

Management server for TaikoZucchini arcade cabinets, grown out of the tjarepo
conversion service. Two jobs:

1. **Song catalog + conversion** — browse ESE/TJA and osu! beatmap
   repositories and convert requested songs into Taiko PS3 custom-song assets.
2. **Remote cabinet management** — cabinets running zucchini.sprx poll the
   connector; operators use the web UI at `/ui` to rename cabinets, pick each
   cabinet's song selection, and queue config changes (including chassisinfo
   operator flags) without opening the cab or attaching a controller.

The service keeps its ESE and OSZ sources read-only and writes generated
packages into `storage/ESE-convert`; per-cabinet management state lives in
`storage/cabinets/<cabinet_id>.json`.

## Run

```sh
docker compose up --build
```

Web UI: `https://localhost:8443/ui` (enter the API token once; it is kept in
localStorage).

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

`/api/tjarepo` is still served as a legacy alias so sprx builds predating the
rename keep working. `CONNECTOR_*` environment variables are preferred;
`TJAREPO_*` names still work as fallbacks.

The compose file mounts:

- ESE source repo from `./storage/ESE`
- osu! beatmap archives from `./storage/OSU`
- conversion cache at `./storage/ESE-convert`
- cabinet management state at `./storage/cabinets`
- local TLS certificates at `./storage/certificates`
- Sony `ps3_at3tool.exe` from `./storage/ps3_at3tool.exe`

The `tja2fumen` converter is vendored in `app/tja2fumen`.

## Cabinet management (pull model)

The cabinet is a pure HTTPS client — no listening socket on the PS3, so it
works behind any NAT/firewall. Every 5 s (plus once at boot) the plugin POSTs
a plain-text heartbeat to `/api/connector/cabinet/poll` carrying its identity
(`cabinet_id` auto-generated on first boot, operator-set `cabinet_name`, game
variant, dongle serial), its cached-song list, and the raw `taiko_config.cfg`.
The response carries whatever the operator queued in the UI:

- **Config changes** (`section.key = value`, e.g. `chassis.force_freeplay`)
  are applied through the same validation the config file parser uses, then
  saved. Network keys apply live; features/patches at next boot.
- **Song selection**: once an operator saves a selection the cabinet is
  *managed*. A separate worker converts, downloads, and pre-renders the entire
  immutable selection only while the game is at attract; the 5 s heartbeat
  continues reporting its phase and song-count progress. The completed set is
  activated in one persisted index update, then deselected cache directories
  are garbage-collected. Operator edits made while a job runs are saved as the
  latest queued selection and promoted only after the active sequence is
  acknowledged, so a large sync cannot change underneath the cabinet.

Changes queued while a cabinet is offline persist and are delivered on its
next poll. At boot the plugin polls before the game reads chassisinfo.xml
(bounded by the `network.mgmt_boot_wait` config key, default 8 s), so operator
flags queued overnight apply as the cabinet powers on — when the network
comes up after that window, they apply on the following boot instead.

**Trust model:** one shared bearer token gates everything (catalog and
management). Run the connector on the arcade's private LAN; there is no
per-cabinet auth.

## osu!taiko archives

Place `.osz` files anywhere below `storage/OSU`. A first-level folder such as
`storage/OSU/Anime` is merged into the matching ESE category (`02 Anime`);
folders without a matching ESE category are exposed as their own categories.

Only native osu!taiko charts (`Mode: 1`) are indexed. Each OSZ remains one song
with at most five selected courses. Easy, Normal, Hard, and Oni are always
present; the closest available chart is reused for missing required courses.
Ura is optional. Charts are matched using their calculated offline osu!taiko
difficulty and common difficulty names. The displayed Taiko level is
`round(osu!taiko stars * 1.5)`, clamped to 1–10.

Set `CONNECTOR_API_TOKEN` to require `Authorization: Bearer <token>`.

Batch conversion uses a bounded worker pool so several queued songs can be
prepared in parallel while clients download assets sequentially. Set
`CONNECTOR_CONVERSION_WORKERS` to control concurrency (default: `4` in Docker,
or up to `4` based on detected CPUs outside Docker).

The FastAPI app serves HTTPS directly on `CONNECTOR_HTTPS_PORT` (`8443` by
default), so it does not conflict with TaikOnline's local `443`. The container
generates a self-signed certificate on first start and stores it under
`storage/certificates/local`.

Set `CONNECTOR_TLS_ENABLED=0` to run the same container as plain HTTP instead.
