# tjarepo

Small API service for browsing ESE/TJA and osu! beatmap repositories and converting requested songs into Taiko PS3 custom-song assets.

The service keeps its ESE and OSZ sources read-only and writes generated packages into `storage/ESE-convert`.

## Run

```sh
docker compose up --build
```

Default API base for local debugging:

```text
https://localhost:8443/api/tjarepo
```

The compose file mounts:

- ESE source repo from `./storage/ESE`
- osu! beatmap archives from `./storage/OSU`
- conversion cache at `./storage/ESE-convert`
- local TLS certificates at `./storage/certificates`
- Sony `ps3_at3tool.exe` from `./storage/ps3_at3tool.exe`

The `tja2fumen` converter is vendored in `app/tja2fumen`.

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

Set `TJAREPO_API_TOKEN` to require `Authorization: Bearer <token>`.

Batch conversion uses a bounded worker pool so several queued songs can be
prepared in parallel while clients download assets sequentially. Set
`TJAREPO_CONVERSION_WORKERS` to control concurrency (default: `4` in Docker,
or up to `4` based on detected CPUs outside Docker).

The FastAPI app serves HTTPS directly on `TJAREPO_HTTPS_PORT` (`8443` by
default), so it does not conflict with TaikOnline's local `443`. The container
generates a self-signed certificate on first start and stores it under
`storage/certificates/local`.

Set `TJAREPO_TLS_ENABLED=0` to run the same container as plain HTTP instead.
