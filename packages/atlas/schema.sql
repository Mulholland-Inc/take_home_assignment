create schema if not exists api;
-- Note: no GRANTs to PUBLIC here. atlas uses a single connecting role
-- per database (the IAM user owns the DB + schema), so PUBLIC grants
-- are unused. On Cloud SQL the IAM user can't grant to PUBLIC on a
-- schema it doesn't own (notably `public` is owned by cloudsqlsuperuser
-- until ALTER SCHEMA OWNER runs), so this used to error on every
-- connect. If you ever introduce a second non-owner role on the same
-- DB, do the GRANT once at provision time (terraform), not here.


-- ───────────────────────────────────────────────────────────────────
-- Type registry (the t-box). One JSON Schema per registered class.
create table if not exists entity_type (
  type        text primary key,
  json_schema jsonb not null
);


create or replace function atlas_norm(s text) returns text
  language sql immutable as $$
    select regexp_replace(lower(btrim(s)), '\s+', ' ', 'g')
  $$;


-- ───────────────────────────────────────────────────────────────────
-- Source registry. Polymorphic by `kind`. 'document' is the first
-- kind; future sources (user input, agent inference, external feeds)
-- plug in with new kind values. Identified by `hash` (sha256 of the
-- content), so re-ingesting the same bytes is idempotent.
create table if not exists entity_source (
  id          uuid primary key default gen_random_uuid(),
  hash        text not null unique,             -- sha256 of the content / payload
  kind        text not null default 'document', -- 'document' | future kinds
  uri         text,                              -- file://path.pdf for kind='document'
  data        jsonb not null default '{}',      -- inline payload when there's no file
                                                  -- (chat msg body, api response, user text, …)
  metadata    jsonb not null default '{}',      -- filename, size, page_count, …
  ingested_at timestamptz not null default now()
);
create index if not exists entity_source_kind_idx on entity_source (kind);


-- ───────────────────────────────────────────────────────────────────
-- Entities (a-box). Hard-id dedup happens at write time on Unique fields.
create table if not exists entity (
  id            uuid primary key default gen_random_uuid(),
  type          text not null references entity_type(type) deferrable initially deferred,
  data          jsonb not null default '{}',
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index if not exists entity_type_idx on entity (type);
create index if not exists entity_data_idx on entity using gin (data jsonb_path_ops);

-- Migration: legacy splink-dedup column / index / helper, no longer used.
drop index if exists entity_canonical_idx;
drop function if exists canonical(uuid);
alter table entity drop column if exists canonical_id cascade;


-- ───────────────────────────────────────────────────────────────────
-- Source attribution as M:N. Lets you ask "which sources contributed
-- to this entity?" cheaply.
create table if not exists entity_source_link (
  entity_id  uuid not null references entity(id) on delete cascade,
  source_id  uuid not null references entity_source(id) on delete cascade,
  primary key (entity_id, source_id)
);
create index if not exists entity_source_link_source_idx on entity_source_link (source_id);


-- ───────────────────────────────────────────────────────────────────
-- Audit log. Every write (create / update / delete) carries a `reason`
-- the writer must justify. Source attribution itself lives on
-- entity_source_link; this is the per-action justification.
-- entity_id is a plain uuid (no FK) so deletions don't cascade-erase
-- their own audit trail — the audit row should outlive the entity.
create table if not exists entity_audit (
  id          uuid primary key default gen_random_uuid(),
  op          text not null,                    -- 'create' | 'update' | 'delete'
  entity_id   uuid not null,
  reason      text not null,
  at          timestamptz not null default now()
);
create index if not exists entity_audit_entity_idx on entity_audit (entity_id);
create index if not exists entity_audit_at_idx on entity_audit (at desc);


-- ───────────────────────────────────────────────────────────────────
-- Conflict log. Written when an upsert sees an incoming non-null
-- field whose value disagrees with the existing non-null value on a
-- matched entity. Default policy is `kept_existing`.
create table if not exists entity_conflict (
  id                 uuid primary key default gen_random_uuid(),
  entity_id          uuid not null references entity(id) on delete cascade,
  field              text not null,
  existing_value     jsonb,
  incoming_value     jsonb,
  existing_source_id uuid references entity_source(id) on delete set null,
  incoming_source_id uuid references entity_source(id) on delete set null,
  resolution         text not null,             -- 'kept_existing' | 'kept_incoming' | 'raised'
  detected_at        timestamptz not null default now()
);
create index if not exists entity_conflict_entity_idx on entity_conflict (entity_id);


-- ───────────────────────────────────────────────────────────────────
-- Per-field provenance. One row per (entity, field, write). Confirms
-- + corrections both log here. `source_id` is the single source most
-- responsible for this value; cross-source confirmations land in
-- entity_source_link at the entity level.
create table if not exists entity_provenance (
  id           uuid primary key default gen_random_uuid(),
  entity_id    uuid not null references entity(id) on delete cascade,
  field        text not null,
  value        jsonb,
  source_id    uuid references entity_source(id) on delete set null,
  recorded_at  timestamptz not null default now()
);
create index if not exists entity_provenance_entity_idx on entity_provenance (entity_id);
create index if not exists entity_provenance_entity_field_idx on entity_provenance (entity_id, field);
