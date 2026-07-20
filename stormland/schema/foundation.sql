-- Worked example — StormlandHoldings' commercial-real-estate lease ontology.
-- This mirrors the pattern we want you to follow in ../database for Reznar's
-- magic items; it is a reference to read (and mimic), not part of the task.
--
-- foundation: the base `object` class every entity inherits, and the touch
-- trigger that maintains updated_at.

create function touch() returns trigger language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end $$;

create table object (
  id         uuid        not null default uuidv7(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
comment on table object is 'Base interface implemented by every object type.';
