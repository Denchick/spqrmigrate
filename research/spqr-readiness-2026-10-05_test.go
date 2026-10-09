package meta_test

import (
	"context"
	"strings"
	"testing"

	"github.com/pg-sharding/spqr/pkg/coord"
	"github.com/pg-sharding/spqr/pkg/meta"
	"github.com/pg-sharding/spqr/pkg/models/topology"
	"github.com/pg-sharding/spqr/qdb"
	spqrparser "github.com/pg-sharding/spqr/yacc/console"
)

func TestSpqrmigratePoCAuditMissingCapabilities(t *testing.T) {
	for _, query := range []string{
		"SHOW migrations;",
		"ALTER SYSTEM MIGRATION SET initial = applied;",
		"ALTER SYSTEM MIGRATION RESET initial;",
		"BEGIN;", "COMMIT;", "ROLLBACK;",
	} {
		t.Run(query, func(t *testing.T) {
			ast, err := spqrparser.Parse(query)
			if err != nil {
				t.Fatalf("parser rejected query: %v", err)
			}
			for _, statement := range ast {
				if statement == nil {
					continue
				}
				_, err = meta.ProcMetadataCommand(context.Background(), statement, nil, nil, nil, nil, false, nil)
				if err == nil {
					t.Fatal("expected missing handler; audit conclusion must be revisited")
				}
				t.Logf("actual execution result: %v", err)
			}
		})
	}
}

func TestSpqrmigratePoCAuditIdempotentDistributionAndRelation(t *testing.T) {
	ctx := context.Background()
	db, err := qdb.RestoreQDB("")
	if err != nil {
		t.Fatal(err)
	}
	mgr := coord.NewLocalInstanceMetadataMgr(db, nil, nil,
		topology.TopMgrFromMap(map[string]*topology.DataShard{}), false, nil, qdb.DefaultMaxTxnSize)
	for _, query := range []string{
		"CREATE DISTRIBUTION IF NOT EXISTS poc_audit COLUMN TYPES varchar;",
		"CREATE DISTRIBUTION IF NOT EXISTS poc_audit COLUMN TYPES varchar;",
		"ALTER DISTRIBUTION poc_audit ATTACH RELATION IF NOT EXISTS poc_orders DISTRIBUTION KEY id;",
		"ALTER DISTRIBUTION poc_audit ATTACH RELATION IF NOT EXISTS poc_orders DISTRIBUTION KEY id;",
		"ALTER DISTRIBUTION poc_audit DETACH RELATION IF EXISTS poc_orders;",
		"ALTER DISTRIBUTION poc_audit DETACH RELATION IF EXISTS poc_orders;",
		"DROP DISTRIBUTION IF EXISTS poc_audit;",
		"DROP DISTRIBUTION IF EXISTS poc_audit;",
	} {
		ast, parseErr := spqrparser.Parse(query)
		if parseErr != nil {
			t.Fatal(parseErr)
		}
		for _, statement := range ast {
			if statement == nil {
				continue
			}
			_, err = meta.ProcMetadataCommand(ctx, statement, mgr, nil, nil, nil, false, nil)
			if err != nil {
				t.Fatalf("%s: %v", query, err)
			}
		}
		t.Log(strings.TrimSpace(query) + " OK")
	}
}
