-- Named queries for the worked example. sqlc turns each into a typed Go method
-- on *Queries; the pipeline calls these instead of writing SQL by hand.

-- name: InsertProperty :one
insert into property (address, state) values ($1, $2) returning id;

-- name: InsertTenant :one
insert into tenant (name, ein) values ($1, $2) returning id;

-- name: InsertLandlord :one
insert into landlord (name, ein) values ($1, $2) returning id;

-- name: InsertLease :one
insert into lease (
  tenant_id, landlord_id, premises_id,
  start_date, end_date, base_rent_amount, base_rent_currency
) values ($1, $2, $3, $4, $5, $6, $7)
returning id;

-- name: LeasesForTenant :many
select l.id, p.address, l.start_date, l.base_rent_amount, l.base_rent_currency
from lease l
join property p on p.id = l.premises_id
where l.tenant_id = $1
order by l.start_date;
