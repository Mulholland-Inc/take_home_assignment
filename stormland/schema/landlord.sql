-- requires: foundation, types

create table landlord (
  name text not null,
  ein  ein,
  primary key (id),
  constraint landlord_name_present check (btrim(name) <> '')
) inherits (object);
comment on table landlord is 'A party leasing a property to a tenant.';
comment on column landlord.name is 'Legal name of the landlord entity.';
comment on column landlord.ein is 'Tax identifier, when the lease states one.';

create trigger touch before update on landlord for each row execute function touch();
