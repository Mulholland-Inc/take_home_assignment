-- The shared primitives every object type builds on: the base `object` class
-- every entity inherits, and the touch trigger that maintains updated_at.
-- This file is provided as your starting point — build your ontology on top of
-- it. See ../../stormland for a worked example of the whole pattern.

create function touch() returns trigger language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end $$;

-- Every object type inherits this base, so identity and timestamps are defined
-- once. uuidv7() is built into PostgreSQL 18 (why compose.yaml pins that image).
create table object (
  id         uuid        not null default uuidv7(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
comment on table object is 'Base interface implemented by every object type.';

-- ---------------------------------------------------------------------------
-- Build your ontology below, or in sibling schema/*.sql files that begin with
-- a `-- requires: foundation` header. The vocabulary is yours to design:
--
--   * `create domain` for constrained value types (a rarity, a gold price, a
--     weight) — semantics live in the type, not in prose.
--   * `create type ... as enum` for closed vocabularies.
--   * `create table ... inherits (object)` for each entity type, with
--     `comment on` describing what each table and column means. Those comments
--     are the ontology's self-description — keep them meaningful.
--   * foreign keys for the relationships between entities.
--   * a `touch` trigger per table:
--       create trigger touch before update on <t> for each row
--         execute function touch();
-- ---------------------------------------------------------------------------
