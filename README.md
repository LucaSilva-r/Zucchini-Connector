# tjarepo

Small API service for browsing an ESE/TJA repository and converting requested songs into Taiko PS3 custom-song assets.

The service keeps the ESE checkout read-only and writes generated packages into `storage/ESE-convert`.

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
- conversion cache at `./storage/ESE-convert`
- local TLS certificates at `./storage/certificates`
- Sony `ps3_at3tool.exe` from `./storage/ps3_at3tool.exe`

The `tja2fumen` converter is vendored in `app/tja2fumen`.

Set `TJAREPO_API_TOKEN` to require `Authorization: Bearer <token>`.

The FastAPI app serves HTTPS directly on `TJAREPO_HTTPS_PORT` (`8443` by
default), so it does not conflict with TaikOnline's local `443`. The container
generates a self-signed certificate on first start and stores it under
`storage/certificates/local`.

Set `TJAREPO_TLS_ENABLED=0` to run the same container as plain HTTP instead.
