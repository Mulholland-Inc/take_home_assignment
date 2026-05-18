# Setup

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)

That's it. No Docker, no separate Postgres install.

## 1. Install dependencies

```bash
uv sync
```

uv resolves dependencies into `.venv/`. Prefix Python commands with `uv run`,
or `source .venv/bin/activate` once for your session.

## 2. Verify

```bash
uv run python verify.py
```

You should see `postgres is ready`.

On the first run, `pgserver` downloads a Postgres binary into your user
cache and starts a local instance. The data directory lives at
`data/.pg/` and is gitignored — delete it to reset the database.

## 3. Your work

- **`reznar/ontology.py`** — define your Pydantic entity models here.
- **`reznar/`** — also where your extraction pipeline lives. Add whatever
  scripts, modules, or notebooks you need to populate Postgres from
  `data/items_combined.pdf`.
- **`stormland/ontology.py`** — a worked example showing how to structure
  Pydantic models for a different domain (commercial real estate leases).

Use `db.connect()` to get a `psycopg` connection:

```python
import db

with db.connect() as conn, conn.cursor() as cur:
    cur.execute("create table if not exists item (id uuid primary key, data jsonb)")
    # ...
```

How you schema the database (a single `entity` table with a `type`
column and `jsonb` data, one table per entity type, …) is your design
call — we want to see your reasoning.

## 4. Submit

See `README.md` for what to commit alongside your code.
