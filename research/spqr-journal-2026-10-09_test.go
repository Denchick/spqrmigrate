// Copy into qdb/ of SPQR PR #3065 and run:
// go test ./qdb -run TestSpqrmigrateJournalPersistence -count=1
package qdb_test

import (
	"context"
	"path/filepath"
	"testing"

	"github.com/pg-sharding/spqr/qdb"
)

func TestSpqrmigrateJournalPersistence(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "qdb.json")
	db, err := qdb.RestoreQDB(path)
	if err != nil {
		t.Fatal(err)
	}
	const name = "spqrmigrate/v1/test%27namespace"
	const value = "json-base64-v1:eyJmb3JtYXQiOjEsImhpc3RvcnkiOltdfQ=="
	if err := db.SetMigration(ctx, name, value); err != nil {
		t.Fatal(err)
	}
	db, err = qdb.RestoreQDB(path)
	if err != nil {
		t.Fatal(err)
	}
	rows, err := db.ListMigrations(ctx)
	if err != nil || rows[name] != value {
		t.Fatalf("restored journal: %v, %v", rows, err)
	}
	rows[name] = "changed outside QDB"
	rows, err = db.ListMigrations(ctx)
	if err != nil || rows[name] != value {
		t.Fatalf("ListMigrations must return a copy: %v, %v", rows, err)
	}
	if err := db.SetMigration(ctx, name+"/other", value); err != nil {
		t.Fatal(err)
	}
	if err := db.ResetMigration(ctx, name); err != nil {
		t.Fatal(err)
	}
	db, err = qdb.RestoreQDB(path)
	if err != nil {
		t.Fatal(err)
	}
	rows, err = db.ListMigrations(ctx)
	if err != nil || len(rows) != 1 || rows[name+"/other"] != value {
		t.Fatalf("RESET must persist and delete only the exact key: %v, %v", rows, err)
	}
}
