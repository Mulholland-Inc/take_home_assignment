// Command verify checks that the local Postgres is reachable.
//
//	go run ./cmd/verify
//
// Prints "postgres is ready" on success. Start the cluster first with
// `docker compose up -d` (see SETUP.md).
package main

import (
	"context"
	"fmt"
	"os"
	"time"

	"oddities/db"
)

func main() {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	pool, err := db.Connect(ctx)
	if err != nil {
		fmt.Fprintf(os.Stderr, "connect: %v\n", err)
		os.Exit(1)
	}
	defer pool.Close()

	if err := pool.Ping(ctx); err != nil {
		fmt.Fprintf(os.Stderr, "ping %s: %v\n", db.URL(), err)
		os.Exit(1)
	}
	fmt.Println("postgres is ready")
}
