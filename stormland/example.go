package stormland

import (
	"context"
	"fmt"
	"strings"

	"github.com/jackc/pgx/v5/pgtype"
	"github.com/jackc/pgx/v5/pgxpool"

	"oddities/stormland/generated"
)

// Record is one lease as an extraction model might first hand it back: raw,
// possibly in the messy form the source used. The pipeline's job is to fold it
// into the canonical shapes the ontology's domains require, then persist it.
type Record struct {
	PropertyAddress string
	PropertyState   string // "California" or "CA"
	TenantName      string
	LandlordName    string
	StartDate       string // YYYY-MM-DD
	BaseRent        string // decimal string
}

// Seed writes one Record through the whole ontology: normalize -> typed insert.
// It is the shape your Reznar extraction pipeline's write path should take —
// the model produces records, you normalize them to the canonical vocabulary,
// and the generated queries persist them with the database enforcing the types.
func Seed(ctx context.Context, pool *pgxpool.Pool, r Record) error {
	state, err := normalizeState(r.PropertyState)
	if err != nil {
		return err
	}

	q := generated.New(pool)

	propertyID, err := q.InsertProperty(ctx, generated.InsertPropertyParams{
		Address: r.PropertyAddress,
		State:   state, // us_state domain rejects anything but a 2-letter code
	})
	if err != nil {
		return fmt.Errorf("insert property: %w", err)
	}
	tenantID, err := q.InsertTenant(ctx, generated.InsertTenantParams{Name: r.TenantName})
	if err != nil {
		return fmt.Errorf("insert tenant: %w", err)
	}
	landlordID, err := q.InsertLandlord(ctx, generated.InsertLandlordParams{Name: r.LandlordName})
	if err != nil {
		return fmt.Errorf("insert landlord: %w", err)
	}

	var start pgtype.Date
	if err := start.Scan(r.StartDate); err != nil {
		return fmt.Errorf("start_date %q: %w", r.StartDate, err)
	}
	_, err = q.InsertLease(ctx, generated.InsertLeaseParams{
		TenantID:         tenantID,
		LandlordID:       landlordID,
		PremisesID:       propertyID,
		StartDate:        start,
		BaseRentAmount:   r.BaseRent, // currency_amount domain checks it is numeric and >= 0
		BaseRentCurrency: "USD",
	})
	if err != nil {
		return fmt.Errorf("insert lease: %w", err)
	}
	return nil
}

// normalizeState folds a state code or full name into its USPS code — the sort
// of fold you write once per messy field. Abbreviated to the states Stormland
// operates in.
func normalizeState(v string) (string, error) {
	v = strings.TrimSpace(v)
	if len(v) == 2 {
		return strings.ToUpper(v), nil
	}
	byName := map[string]string{
		"california": "CA",
		"new york":   "NY",
		"texas":      "TX",
		"washington": "WA",
	}
	if code, ok := byName[strings.ToLower(v)]; ok {
		return code, nil
	}
	return "", fmt.Errorf("unknown US state: %q", v)
}
