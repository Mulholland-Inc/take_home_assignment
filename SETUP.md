# Setup

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Docker (Docker Desktop on Mac/Windows; Docker Engine on Linux)

## 1. Install dependencies

From the repo root:

```bash
uv sync
```

This resolves dependencies and installs everything in editable mode, including generating a `uv.lock` on first run.

## 2. Start Postgres

```
docker compose up -d
```

This starts a Postgres 18 container on port 5432 with user `atlas`, password `atlas`, database `atlas`.

**Port conflict**: if you already have Postgres running locally on 5432, either stop it first or change the port mapping in `docker-compose.yml` to e.g. `"5433:5432"` and set `ATLAS_DSN` accordingly (see step 3).

**WSL users**: make sure Docker Desktop has WSL integration enabled (Settings → Resources → WSL Integration). The DSN in step 3 uses TCP `localhost`, which works correctly from inside WSL2.

## 3. Set the connection string

```bash
export ATLAS_DSN=postgresql://atlas:atlas@localhost:5432/atlas
```

Add this to your shell profile or a `.env` file you source before running anything. Atlas reads this environment variable on every startup — if it is unset, it falls back to a default that may not match your container.

## 4. Verify

```bash
python - <<'EOF'
import atlas
conn = atlas.connect()
atlas.bootstrap(conn)
print("atlas is ready")
conn.close()
EOF
```

You should see `atlas is ready`. If you get a connection error, check that the container is running (`docker compose ps`) and that `ATLAS_DSN` is exported in your current shell.

## 5. Your client

Your client scaffold is at `clients/reznar/`. The only hard convention atlas enforces: `clients/reznar/ontology.py` must export a `REGISTRY` dict mapping type name strings to Pydantic `BaseModel` subclasses, and a `register_all(atlas)` function.

See `clients/stormland/ontology.py` for a complete worked example.

## 6. Export a snapshot

Once your data is loaded:

```bash
python -m atlas export reznar --out reznar_snap.bin
```

Submit `reznar_snap.bin` alongside your code.
