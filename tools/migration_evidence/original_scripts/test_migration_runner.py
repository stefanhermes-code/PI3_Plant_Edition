"""Tests for the PI3 migration runner (work package R-H).

Every case states its input and its expected outcome. Each builds its own
throwaway Postgres database, so no case can see another's state.

Needs a Postgres to talk to, named by PI3_TEST_DB_URL - the runner is a
Postgres tool (transactional DDL, dollar-quoted bodies, to_regclass) and
proving it against SQLite would prove the wrong thing. Skips, loudly, when
that variable is absent.

Run with `python3 test_migration_runner.py`.
"""
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sqlalchemy as sa

PASS, FAIL = [], []


def check(case, expect, got, detail=""):
    ok = expect == got
    (PASS if ok else FAIL).append(case)
    print(f'  [{"PASS" if ok else "FAIL"}] {case}\n         expected {expect!r}, got {got!r}'
          + (f'\n         {detail}' if detail else ''))


BASE_URL = os.environ.get("PI3_TEST_DB_URL")
if not BASE_URL:
    print("SKIPPED: PI3_TEST_DB_URL is not set.")
    print("These tests need a real Postgres - the runner relies on transactional DDL,")
    print("dollar-quoted function bodies and to_regclass, none of which SQLite has.")
    sys.exit(0)

import migrate


class Sandbox:
    """A throwaway database plus a throwaway migrations folder."""

    counter = 0

    def __init__(self):
        Sandbox.counter += 1
        self.db = "pi3_test_%d_%d" % (os.getpid(), Sandbox.counter)
        admin = sa.create_engine(BASE_URL + "/postgres", isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(sa.text('drop database if exists "%s"' % self.db))
            conn.execute(sa.text('create database "%s"' % self.db))
        admin.dispose()
        self.url = "%s/%s" % (BASE_URL, self.db)
        self.engine = sa.create_engine(self.url, future=True)
        self.dir = tempfile.mkdtemp(prefix="pi3mig_")
        self._saved_dir = migrate.MIGRATIONS_DIR
        migrate.MIGRATIONS_DIR = self.dir

    def write(self, name, sql):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as handle:
            handle.write(sql)

    def sql(self, statement):
        with self.engine.begin() as conn:
            return conn.execute(sa.text(statement)).scalar()

    def exec(self, statement):
        with self.engine.begin() as conn:
            conn.execute(sa.text(statement))

    def up(self, by="test"):
        class Args:
            dry_run = False
        Args.by = by
        return migrate.cmd_up(self.engine, Args)

    def ledger(self, name):
        with self.engine.begin() as conn:
            return conn.execute(sa.text(
                "select status, error, row_counts, checksum from schema_migrations "
                "where migration_name = :n"), {"n": name}).first()

    def close(self):
        # Restore FIRST and unconditionally. A case that failed before its
        # cleanup used to leave migrate.MIGRATIONS_DIR pointing at a temporary
        # directory for the rest of the process. It could never write into the
        # live directory, but a later case would then read migrations that had
        # just been deleted - a confusing failure with an unrelated cause.
        try:
            migrate.MIGRATIONS_DIR = self._saved_dir
        finally:
            try:
                self.engine.dispose()
                shutil.rmtree(self.dir, ignore_errors=True)
                admin = sa.create_engine(BASE_URL + "/postgres",
                                         isolation_level="AUTOCOMMIT")
                with admin.connect() as conn:
                    conn.execute(sa.text('drop database if exists "%s"' % self.db))
                admin.dispose()
            except Exception:
                # Cleanup of a throwaway database must never mask the real
                # failure that brought us here.
                pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def seeded(box):
    box.exec("create table widgets (id serial primary key, label text)")
    box.exec("insert into widgets (label) values ('a'), ('b'), ('c')")


print("=" * 78)
print("A. THE LEDGER RECORDS WHAT RAN")
print("=" * 78)
box = Sandbox()
seeded(box)
box.write("0001_add_column.sql", "-- @count: widgets\nalter table widgets add column note text;\n")
box.up()
row = box.ledger("0001_add_column.sql")
check("a successful migration is recorded as applied", "applied", row.status)
check("its row counts are recorded", "widgets: 3 -> 3 (+0)", row.row_counts)
check("its checksum is recorded", 64, len(row.checksum))
check("the schema change actually happened", True,
      box.sql("select to_regclass('public.widgets') is not null")
      and box.sql("select count(*) from information_schema.columns "
                  "where table_name='widgets' and column_name='note'") == 1)
check("re-running applies nothing", 0, box.up())
check("and does not duplicate the ledger row", 1,
      box.sql("select count(*) from schema_migrations where migration_name='0001_add_column.sql'"))
box.close()

print("\n" + "=" * 78)
print("B. A FAILURE ROLLS BACK AND IS RECORDED")
print("=" * 78)
box = Sandbox()
seeded(box)
box.write("0001_half_broken.sql",
          "-- @count: widgets\n"
          "insert into widgets (label) values ('d');\n"
          "alter table widgets add constraint bad check (no_such_column > 0);\n")
rc = box.up()
check("the run reports failure", 1, rc)
check("the earlier statement was rolled back", 3, box.sql("select count(*) from widgets"))
row = box.ledger("0001_half_broken.sql")
check("the ledger records it as failed", "failed", row.status)
check("and names the real cause", True, "no_such_column" in (row.error or ""), row.error)
box.close()

print("\n" + "=" * 78)
print("C. RECOVERY: FIX THE FILE AND RE-RUN")
print("=" * 78)
box = Sandbox()
seeded(box)
box.write("0001_recoverable.sql",
          "-- @count: widgets\ninsert into widgets (label) values ('d');\n"
          "select * from nowhere;\n")
box.up()
check("first attempt fails", "failed", box.ledger("0001_recoverable.sql").status)
check("nothing was left behind", 3, box.sql("select count(*) from widgets"))
box.write("0001_recoverable.sql",
          "-- @count: widgets\n-- @expect: widgets +1\n"
          "insert into widgets (label) values ('d');\n")
check("the corrected migration applies", 0, box.up())
check("the ledger flips to applied", "applied", box.ledger("0001_recoverable.sql").status)
check("the error is cleared", None, box.ledger("0001_recoverable.sql").error)
check("the row is there now", 4, box.sql("select count(*) from widgets"))
box.close()

print("\n" + "=" * 78)
print("D. ROW-COUNT ASSERTIONS ARE ENFORCED, NOT DECORATION")
print("=" * 78)
box = Sandbox()
seeded(box)
box.write("0001_lies.sql",
          "-- @expect: widgets +3\ninsert into widgets (label) values ('d');\n")
check("a migration that does not do what it claims fails", 1, box.up())
check("and its change is rolled back", 3, box.sql("select count(*) from widgets"))
row = box.ledger("0001_lies.sql")
check("the ledger explains the mismatch", True,
      "expected +3, got +1" in (row.error or ""), row.error)
check("the ledger reports the state that survived, not the one inside the transaction",
      "widgets: 3 -> 3 (+0)", row.row_counts)
box.close()

box = Sandbox()
seeded(box)
box.write("0001_honest.sql",
          "-- @expect: widgets +2\ninsert into widgets (label) values ('d'), ('e');\n")
check("a migration that keeps its promise passes", 0, box.up())
box.close()

print("\n" + "=" * 78)
print("E. CHECKSUM DRIFT STOPS EVERYTHING")
print("=" * 78)
box = Sandbox()
seeded(box)
box.write("0001_first.sql", "alter table widgets add column note text;\n")
box.up()
box.write("0001_first.sql", "alter table widgets add column note text;\n-- added later\n")
box.write("0002_second.sql", "alter table widgets add column other text;\n")


class _A:
    dry_run = False
    by = "test"


check("verify reports drift", 1, migrate.cmd_verify(box.engine, _A))
check("up refuses to run", 1, box.up())
check("and the pending migration did NOT run", 0,
      box.sql("select count(*) from information_schema.columns "
              "where table_name='widgets' and column_name='other'"))
check("an applied migration whose file is deleted is an orphan", True,
      bool(migrate.drift(migrate.load_migrations(),
                         {"0009_gone.sql": type("R", (), {
                             "status": "applied", "checksum": "x"})()})))
box.close()

print("\n" + "=" * 78)
print("F. BACKFILL RECORDS WITHOUT RUNNING")
print("=" * 78)
box = Sandbox()
seeded(box)
# A migration that would fail loudly if it ran - proving backfill does not run it.
box.write("0001_already_done.sql", "-- @count: widgets\nalter table widgets add column id text;\n")


class _B:
    name = "0001_already_done.sql"
    by = "Stefan Hermes"
    note = "Applied before the runner existed."


check("backfill succeeds", 0, migrate.cmd_backfill(box.engine, _B))
check("it is recorded as backfilled", "backfilled", box.ledger("0001_already_done.sql").status)
check("the migration did NOT run", 0,
      box.sql("select count(*) from information_schema.columns "
              "where table_name='widgets' and column_name='id' and data_type='text'"))
check("up now has nothing to do", 0, box.up())
check("backfilling a name with no file is refused", 1,
      migrate.cmd_backfill(box.engine, type("C", (), {
          "name": "9999_imaginary.sql", "by": None, "note": None})))
box.close()

print("\n" + "=" * 78)
print("G. THE STATEMENT SPLITTER")
print("=" * 78)
split = migrate.split_statements
check("a dollar-quoted body is not cut at its semicolons", 1,
      len(split("create function f() returns int language plpgsql as $$ "
                "begin a; b; return 1; end; $$;")))
check("a semicolon inside a line comment does not split", 1,
      len(split("select 1 -- one; two\n;")))
check("a semicolon inside a block comment does not split", 1,
      len(split("select 1 /* one; two */;")))
check("a semicolon inside a string does not split", 1,
      len(split("insert into t values ('a;b');")))
check("an escaped quote does not end the string early", 1,
      len(split("insert into t values ('it''s; fine');")))
check("a trailing comment is not an empty statement", 1,
      len(split("select 1;\n-- done\n")))
check("ordinary statements still split", 3,
      len(split("select 1; select 2; select 3;")))

print("\n" + "=" * 78)
print("H. FILENAME AND SEQUENCE DISCIPLINE")
print("=" * 78)
box = Sandbox()
box.write("not-a-migration.sql", "select 1;")
try:
    migrate.load_migrations()
    bad_name = False
except ValueError as exc:
    bad_name = "valid migration filename" in str(exc)
check("a badly named file is rejected", True, bad_name)
os.remove(os.path.join(box.dir, "not-a-migration.sql"))
box.write("0001_one.sql", "select 1;")
box.write("0001_two.sql", "select 1;")
try:
    migrate.load_migrations()
    dupe = False
except ValueError as exc:
    dupe = "share sequence" in str(exc)
check("two migrations with the same sequence number are rejected", True, dupe)
os.remove(os.path.join(box.dir, "0001_two.sql"))
box.write("0002_bad_expect.sql", "-- @expect: widgets 3\nselect 1;\n")
try:
    migrate.load_migrations()
    malformed = False
except ValueError as exc:
    malformed = "malformed @expect" in str(exc)
check("a malformed @expect line is rejected", True, malformed)
box.close()

print("\n" + "=" * 78)
print("I. TEST FIXTURES ARE ISOLATED FROM THE LIVE MIGRATIONS DIRECTORY")
print("=" * 78)
# Charlie's R-A1 release, 21 Aug 2026: the deliberately broken and
# wrong-assertion migrations used to prove rollback must remain test fixtures
# only, and must never be capable of appearing in a production sequence.
live = migrate.MIGRATIONS_DIR
check("the tests restored the live migrations directory", True,
      os.path.isdir(live) and "pi3mig_" not in live, live)

live_files = sorted(f for f in os.listdir(live) if f.endswith(".sql"))
check("every file in the live directory is a numbered migration", True,
      all(migrate.FILENAME_RE.match(f) for f in live_files), live_files)
for banned in ("deliberately_broken", "wrong_assertion", "broken", "fixture"):
    check(f'no live migration is named "{banned}"', [],
          [f for f in live_files if banned in f])
check("the live directory loads cleanly", True,
      len(migrate.load_migrations()) == len(live_files), live_files)

# The fixtures the tests DO create must be somewhere else entirely.
probe = Sandbox()
check("a test's migrations directory is a fresh temporary one", True,
      probe.dir != live and "pi3mig_" in probe.dir, probe.dir)
probe.write("0001_deliberately_broken.sql", "select * from nowhere;")
check("writing a fixture does not touch the live directory", live_files,
      sorted(f for f in os.listdir(live) if f.endswith(".sql")))
probe.close()
check("and the live directory is restored afterwards", live, migrate.MIGRATIONS_DIR)

print("\n" + "=" * 78)
print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:")
    [print("  -", f) for f in FAIL]
print("=" * 78)
sys.exit(1 if FAIL else 0)
