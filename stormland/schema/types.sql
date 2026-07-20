-- requires: foundation

-- The value vocabulary. Each domain encodes its meaning as a constraint, so the
-- database itself rejects anything not already in canonical form — the pipeline
-- normalizes the model's messy output ("California" -> "CA") *before* insert,
-- and these types are the contract it normalizes to.

create domain us_state        as text          check (value ~ '^[A-Z]{2}$');
create domain ein             as text          check (value ~ '^[0-9]{2}-[0-9]{7}$');
create domain currency_code   as text          check (value ~ '^[A-Z]{3}$');
create domain currency_amount as numeric(18,2) check (value >= 0);

comment on domain us_state        is 'USPS two-letter US state code, e.g. ''CA''.';
comment on domain ein             is 'US Employer Identification Number, formatted ''NN-NNNNNNN''.';
comment on domain currency_code   is 'Three-letter ISO 4217 currency code.';
comment on domain currency_amount is 'Nonnegative monetary amount with cent precision.';
