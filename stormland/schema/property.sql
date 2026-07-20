-- requires: foundation, types

create table property (
  address text not null,
  state   us_state,
  primary key (id),
  constraint property_address_present check (btrim(address) <> '')
) inherits (object);
comment on table property is 'A leasable commercial property.';
comment on column property.address is 'Street address as printed in the lease.';
comment on column property.state is 'US state the property sits in.';

create trigger touch before update on property for each row execute function touch();
