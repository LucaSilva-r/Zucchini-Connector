# tjarepo

Small API service for browsing an ESE/TJA repository and converting requested songs into Taiko PS3 custom-song assets.

The service keeps the ESE checkout read-only and writes generated packages into `storage/ESE-convert`.

## Run

```sh
docker compose up --build
```

Default API base for local debugging:

```text
http://localhost:8090/api/tjarepo
```

For local game testing, start the development TLS proxy too:

```sh
docker compose --profile local-tls up --build
```

The local TLS API base is:

```text
https://localhost:8443/api/tjarepo
```

The compose file mounts:

- ESE source repo from `./storage/ESE`
- conversion cache at `./storage/ESE-convert`
- Sony `ps3_at3tool.exe` from `./storage/ps3_at3tool.exe`

The `tja2fumen` converter is vendored in `app/tja2fumen`.

Set `TJAREPO_API_TOKEN` to require `Authorization: Bearer <token>`.

The FastAPI app is published as plain HTTP on `TJAREPO_PORT` (`8090` by
default). The optional local nginx TLS proxy is published on
`TJAREPO_HTTPS_PORT` (`8443` by default), so it does not conflict with
TaikOnline's local `443`. The proxy generates a self-signed certificate on
first start and is intended only for local development.
