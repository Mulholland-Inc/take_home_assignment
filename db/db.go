// Package db is the connection seam to the local Postgres and the provisioner
// that applies a declarative schema to it.
//
// The pool reads DATABASE_URL, defaulting to the cluster compose.yaml starts
// (see SETUP.md). Schema lives as one .sql file per object type; Apply runs
// them in dependency order so a fresh database goes from empty to fully
// provisioned in one transaction. There is no migration ledger — the files are
// the source of truth, exactly as in the production ontology.
package db

import (
	"context"
	"fmt"
	"io/fs"
	"os"
	"sort"
	"strings"

	"github.com/jackc/pgx/v5/pgxpool"
)

// DefaultURL points at the cluster compose.yaml brings up on port 5433.
const DefaultURL = "postgres://postgres:postgres@localhost:5433/postgres?sslmode=disable"

// URL returns DATABASE_URL, or DefaultURL when it is unset.
func URL() string {
	if v := os.Getenv("DATABASE_URL"); v != "" {
		return v
	}
	return DefaultURL
}

// Connect opens a connection pool to the local Postgres. Close it when done.
func Connect(ctx context.Context) (*pgxpool.Pool, error) {
	return pgxpool.New(ctx, URL())
}

// Apply provisions every *.sql file in fsys into an empty database in one
// transaction, ordered by the "-- requires: a, b" header each file may carry:
// a file is applied only once every file it names has been applied, so
// inherited tables and foreign keys always see their parents first. Re-running
// against an already-provisioned database will error on the duplicate objects
// — provisioning is a once-per-database step, not a migration.
func Apply(ctx context.Context, pool *pgxpool.Pool, fsys fs.FS) error {
	names, err := fs.Glob(fsys, "*.sql")
	if err != nil {
		return err
	}
	sort.Strings(names)

	type file struct {
		name, stem, sql string
		reqs            []string
	}
	files := make([]file, 0, len(names))
	provided := map[string]bool{}
	for _, name := range names {
		body, err := fs.ReadFile(fsys, name)
		if err != nil {
			return err
		}
		stem := strings.TrimSuffix(name, ".sql")
		files = append(files, file{name, stem, string(body), requiresOf(string(body))})
		provided[stem] = true
	}
	if len(files) == 0 {
		return fmt.Errorf("schema directory contains no .sql files")
	}
	for _, f := range files {
		for _, dep := range f.reqs {
			if !provided[dep] {
				return fmt.Errorf("%s requires unknown schema file %q", f.name, dep)
			}
		}
	}

	tx, err := pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)

	applied := map[string]bool{}
	for len(applied) < len(files) {
		progress := false
		for _, f := range files {
			if applied[f.stem] || !ready(f.reqs, applied) {
				continue
			}
			if _, err := tx.Exec(ctx, f.sql); err != nil {
				return fmt.Errorf("%s: %w", f.name, err)
			}
			applied[f.stem] = true
			progress = true
		}
		if !progress {
			return fmt.Errorf("schema dependency cycle among %d files", len(files))
		}
	}
	return tx.Commit(ctx)
}

// requiresOf reads the "-- requires: a, b" header that orders schema files.
func requiresOf(src string) []string {
	for _, line := range strings.Split(src, "\n") {
		if line = strings.TrimSpace(line); line == "" {
			continue
		}
		if !strings.HasPrefix(line, "--") {
			return nil // first non-comment line: header, if any, is over
		}
		rest := strings.TrimSpace(strings.TrimPrefix(line, "--"))
		if list, isReq := strings.CutPrefix(rest, "requires:"); isReq {
			var out []string
			for _, part := range strings.Split(list, ",") {
				if part = strings.TrimSpace(part); part != "" {
					out = append(out, part)
				}
			}
			return out
		}
	}
	return nil
}

func ready(reqs []string, applied map[string]bool) bool {
	for _, r := range reqs {
		if !applied[r] {
			return false
		}
	}
	return true
}
