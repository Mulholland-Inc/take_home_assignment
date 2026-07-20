// Package stormland is the worked example for the take-home: StormlandHoldings'
// commercial-real-estate lease ontology, built the same way we want you to build
// Reznar's. Read it end to end — schema/*.sql defines the ontology, sqlc
// generates the typed Go in generated/, and Seed shows the extraction pipeline's
// last mile: normalize a messy record into the canonical form the domains demand,
// then insert it through the generated queries.
package stormland

import (
	"embed"
	"io/fs"
)

//go:embed schema/*.sql
var schemaFS embed.FS

// Schema returns the worked example's declarative schema, ready for db.Apply.
func Schema() fs.FS {
	sub, err := fs.Sub(schemaFS, "schema")
	if err != nil {
		panic(err)
	}
	return sub
}
