// Command extract is the entry point for your extraction pipeline: read
// data/items_combined.pdf, turn it into Reznar's ontology objects, and populate
// the database.
//
//	go run ./cmd/extract
//
// This is a starting skeleton — provisioning a fresh database and opening the
// PDF are wired up; the extraction itself is yours to build. The database
// begins empty, so run against a fresh cluster (docker compose down -v && up).
package main

import (
	"context"
	"fmt"
	"os"

	"oddities/database"
	"oddities/db"
)

const pdfPath = "data/items_combined.pdf"

func main() {
	ctx := context.Background()

	pool, err := db.Connect(ctx)
	if err != nil {
		fatal("connect: %v", err)
	}
	defer pool.Close()

	// Provision the ontology from database/schema/*.sql into the empty database.
	if err := db.Apply(ctx, pool, database.Schema()); err != nil {
		fatal("provision schema: %v", err)
	}

	if _, err := os.Stat(pdfPath); err != nil {
		fatal("open %s: %v", pdfPath, err)
	}

	// TODO: this is the assignment.
	//   1. Read the pages of pdfPath (pick a PDF library, or hand the bytes to a
	//      multimodal model — the source is messy, so lean on the model).
	//   2. Extract entities against your ontology. We are an AI-first company and
	//      expect an AI extraction step here, not hand-written parsing.
	//   3. Normalize each record into the canonical vocabulary your domains
	//      demand, then insert it through your sqlc-generated queries.
	//      See ../../stormland/example.go (Seed) for the write path's shape.
	fmt.Println("connected and schema applied; extraction not implemented yet")
}

func fatal(format string, args ...any) {
	fmt.Fprintf(os.Stderr, format+"\n", args...)
	os.Exit(1)
}
