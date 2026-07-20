-- requires: foundation, types, property, tenant, landlord

-- The relationship entity: a lease ties a tenant and landlord to a property for
-- a term, with a base rent. Foreign keys make the ontology traversable — from a
-- lease to its parties and premises — and keep it referentially honest.
create table lease (
  tenant_id           uuid not null references tenant (id)   on delete restrict,
  landlord_id         uuid not null references landlord (id) on delete restrict,
  premises_id         uuid not null references property (id) on delete restrict,
  start_date          date not null,
  end_date            date,
  base_rent_amount    currency_amount not null,
  base_rent_currency  currency_code   not null default 'USD',
  primary key (id),
  constraint lease_term check (end_date is null or end_date >= start_date)
) inherits (object);
comment on table lease is 'A commercial lease between a tenant and a landlord for a property.';
comment on column lease.tenant_id is 'The leasing tenant.';
comment on column lease.landlord_id is 'The owning landlord.';
comment on column lease.premises_id is 'The leased property.';
comment on column lease.start_date is 'Commencement date.';
comment on column lease.end_date is 'Expiration date; null for open-ended.';
comment on column lease.base_rent_amount is 'Base monthly rent.';
comment on column lease.base_rent_currency is 'Currency of the base rent.';

create trigger touch before update on lease for each row execute function touch();
create index lease_tenant_idx on lease (tenant_id);
create index lease_premises_idx on lease (premises_id);
