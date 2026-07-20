package stormland

import (
	"context"
	"testing"

	"oddities/db"
)

// TestSeed provisions the worked-example schema into the live compose Postgres
// and seeds one record end-to-end, exercising schema -> generated -> insert.
// Needs `docker compose up -d`.
func TestSeed(t *testing.T) {
	ctx := context.Background()

	// Reset to an empty database on a throwaway pool, then close it: recreating
	// the schema changes the domain type OIDs, so the working pool below must
	// open fresh connections that introspect the new ones.
	setup, err := db.Connect(ctx)
	if err != nil {
		t.Fatalf("connect: %v", err)
	}
	if _, err := setup.Exec(ctx, "drop schema public cascade; create schema public"); err != nil {
		t.Fatalf("reset: %v", err)
	}
	if err := db.Apply(ctx, setup, Schema()); err != nil {
		t.Fatalf("apply: %v", err)
	}
	setup.Close()

	pool, err := db.Connect(ctx)
	if err != nil {
		t.Fatalf("connect: %v", err)
	}
	defer pool.Close()

	err = Seed(ctx, pool, Record{
		PropertyAddress: "1 Market St",
		PropertyState:   "California", // folded to "CA" before insert
		TenantName:      "Acme LLC",
		LandlordName:    "Stormland Holdings",
		StartDate:       "2026-01-01",
		BaseRent:        "4200.00",
	})
	if err != nil {
		t.Fatalf("seed: %v", err)
	}

	var state string
	if err := pool.QueryRow(ctx, "select state from property").Scan(&state); err != nil {
		t.Fatalf("read back: %v", err)
	}
	if state != "CA" {
		t.Fatalf("want normalized state CA, got %q", state)
	}
}
