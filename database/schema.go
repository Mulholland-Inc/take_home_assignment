// Package database carries Reznar's declarative ontology: one .sql file per
// object type under schema/, applied by db.Apply in "-- requires:" order. These
// files are the single source of truth for the database — sqlc reads the same
// files to generate the typed Go in generated/.
package database

import (
	"embed"
	"io/fs"
)

//go:embed schema/*.sql
var schemaFS embed.FS

// Schema returns the declarative schema files, ready for db.Apply.
func Schema() fs.FS {
	sub, err := fs.Sub(schemaFS, "schema")
	if err != nil {
		panic(err)
	}
	return sub
}
