-- requires: foundation, types

create table tenant (
  name text not null,
  ein  ein,
  primary key (id),
  constraint tenant_name_present check (btrim(name) <> '')
) inherits (object);
comment on table tenant is 'A party leasing a property from a landlord.';
comment on column tenant.name is 'Legal name of the tenant entity.';
comment on column tenant.ein is 'Tax identifier, when the lease states one.';

create trigger touch before update on tenant for each row execute function touch();
