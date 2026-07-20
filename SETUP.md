# Setup

## Prerequisites

- Go 1.24+
- Docker (for the local Postgres)
- [`sqlc`](https://docs.sqlc.dev/en/latest/overview/install.html) — `brew install sqlc`

## 1. Start Postgres

```bash
docker compose up -d
```

One pinned PostgreSQL 18 (its built-in `uuidv7()` backs the `object` base class),
reachable at `postgres://postgres:postgres@localhost:5433/postgres`. Override with
`DATABASE_URL` if you need to. Reset the database at any time with:

```bash
docker compose down -v && docker compose up -d
```

## 2. Verify

```bash
go run ./cmd/verify
```

You should see `postgres is ready`.

## 3. Your work

- **`database/schema/*.sql`** — define your ontology here, on top of the provided
  `foundation.sql`. List each new file in `database/sqlc.yaml` (in dependency
  order) and give it a `-- requires:` header so provisioning applies it after its
  parents.
- **Generate typed Go** after each schema change, from the `database/` directory:
  ```bash
  cd database && sqlc generate
  ```
  Output lands in `database/generated/`.
- **`cmd/extract`** — your extraction pipeline. It already provisions the schema
  and opens the PDF; you build the extraction and inserts.
- **`stormland/`** — a complete worked example in a different domain (commercial
  real-estate leases). Read it as your reference for the whole loop, then delete
  it if you like. Its live end-to-end test runs against the compose database:
  ```bash
  go test ./stormland/
  ```

## Layout

```
compose.yaml            local Postgres (port 5433)
db/                     connection pool + declarative-schema provisioner
database/
  schema/foundation.sql base `object` class + touch trigger (provided)
  schema/               your entity types go here
  query/                your named queries for sqlc
  generated/            sqlc output (typed Go)
  sqlc.yaml
stormland/              worked example: schema -> sqlc -> normalized insert
cmd/verify              "postgres is ready" check
cmd/extract             your extraction pipeline (scaffold)
data/items_combined.pdf the source catalog
```
