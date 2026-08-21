"""PI3 migration runner and ledger.

Work package R-H, the first REACH Readiness package. Charlie's ruling of
21 August 2026: the migration runner and its ledger land BEFORE any REACH
schema expansion, and every schema change runs through this mechanism with
checksums and before/after row-count assertions.

WHY A LEDGER AND NOT JUST A FOLDER OF SQL

A migration folder alone answers "what did we intend". It cannot answer the two
questions that actually matter when something has gone wrong at a customer:

  - has THIS file already run against THIS database, and
  - is the file on disk still the file that ran?

The ledger answers both, and the checksum is what makes the second answer
trustworthy. A migration edited after it was applied is the classic way two
environments silently diverge: everyone's ledger says "applied", and the
databases do not match. The runner refuses to proceed when it sees that.

USAGE

    python migrate.py status              what is applied, what is pending
    python migrate.py up                  apply every pending migration
    python migrate.py up --dry-run        show what would run, touch nothing
    python migrate.py verify              re-check every applied checksum
    python migrate.py backfill NAME       record a migration as already applied

Connection comes from PI3_DB_URL, the same variable the application uses.

MIGRATION FILE FORMAT

Plain .sql, named NNNN_snake_case_name.sql, applied in filename order. An
optional header directs the runner:

    -- @count: physical_property_results, samples
    -- @expect: physical_property_results +0
    -- @no-transaction

@count names tables to count before and after; the counts go in the ledger.
@expect asserts a required change and FAILS the migration if it is not met -
"+0" means the count must not move, "+12" means exactly twelve rows added.
@no-transaction is for statements Postgres refuses to run inside one (CREATE
INDEX CONCURRENTLY). Use it rarely: without a transaction a partial failure
cannot be rolled back, and the ledger records the migration as failed with the
statement that broke.
"""
import argparse
import datetime as dt
import hashlib
import os
import re
import sys
import time

import sqlalchemy as sa

MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")
LEDGER_TABLE = "schema_migrations"
FILENAME_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")

STATUS_APPLIED = "applied"
STATUS_FAILED = "failed"
STATUS_BACKFILLED = "backfilled"


# --------------------------------------------------------------------------
# the ledger
# --------------------------------------------------------------------------
LEDGER_DDL = """
create table if not exists %s (
    id              serial primary key,
    migration_name  text        not null unique,
    checksum        text        not null,
    status          text        not null,
    applied_at      timestamptz not null default now(),
    applied_by      text,
    duration_ms     integer,
    row_counts      text,
    error           text,
    notes           text
);
""" % LEDGER_TABLE


def ensure_ledger(conn):
    conn.execute(sa.text(LEDGER_DDL))


def engine_from_env():
    url = os.environ.get("PI3_DB_URL")
    if not url:
        sys.exit("PI3_DB_URL is not set. It is the same variable the application uses.")
    return sa.create_engine(url, future=True)


# --------------------------------------------------------------------------
# migration files
# --------------------------------------------------------------------------
class Migration:
    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)
        match = FILENAME_RE.match(self.name)
        if not match:
            raise ValueError(
                "%s is not a valid migration filename. Expected NNNN_snake_case_name.sql"
                % self.name
            )
        self.sequence = int(match.group(1))
        with open(path, "rb") as handle:
            self.raw = handle.read()
        # Checksum the bytes on disk, not the parsed statements. A change to a
        # comment is still a change to the file somebody reviewed.
        self.checksum = hashlib.sha256(self.raw).hexdigest()
        self.sql = self.raw.decode("utf-8")
        self.count_tables = self._directive_list("count")
        self.expectations = self._expectations()
        self.in_transaction = "-- @no-transaction" not in self.sql

    def _directive_list(self, key):
        found = []
        for line in self.sql.splitlines():
            if line.strip().startswith("-- @%s:" % key):
                found += [p.strip() for p in line.split(":", 1)[1].split(",") if p.strip()]
        return found

    def _expectations(self):
        out = {}
        for line in self.sql.splitlines():
            if not line.strip().startswith("-- @expect:"):
                continue
            body = line.split(":", 1)[1].strip()
            parts = body.split()
            if len(parts) != 2 or parts[1][0] not in "+-":
                raise ValueError(
                    "%s has a malformed @expect line: %r. Expected '@expect: table +N'"
                    % (self.name, body)
                )
            out[parts[0]] = int(parts[1])
        return out

    def tables_to_count(self):
        return sorted(set(self.count_tables) | set(self.expectations))


def load_migrations():
    if not os.path.isdir(MIGRATIONS_DIR):
        return []
    files = [f for f in os.listdir(MIGRATIONS_DIR) if f.endswith(".sql")]
    migrations = sorted((Migration(os.path.join(MIGRATIONS_DIR, f)) for f in files),
                        key=lambda m: (m.sequence, m.name))
    seen = {}
    for m in migrations:
        if m.sequence in seen:
            raise ValueError(
                "Two migrations share sequence %04d: %s and %s. A sequence number must be "
                "unique, or the order two environments apply them in can differ."
                % (m.sequence, seen[m.sequence], m.name)
            )
        seen[m.sequence] = m.name
    return migrations


# --------------------------------------------------------------------------
# counting
# --------------------------------------------------------------------------
def count_tables(conn, tables):
    counts = {}
    for table in tables:
        if not re.match(r"^[a-z_][a-z0-9_]*$", table):
            raise ValueError("%r is not a plain table name" % table)
        exists = conn.execute(sa.text(
            "select to_regclass(:t) is not null"), {"t": "public." + table}).scalar()
        counts[table] = conn.execute(
            sa.text("select count(*) from public.%s" % table)).scalar() if exists else None
    return counts


def format_counts(before, after):
    if not before and not after:
        return ""
    parts = []
    for table in sorted(set(before) | set(after)):
        b, a = before.get(table), after.get(table)
        b_s = "-" if b is None else str(b)
        a_s = "-" if a is None else str(a)
        delta = ""
        if b is not None and a is not None:
            delta = " (%+d)" % (a - b)
        parts.append("%s: %s -> %s%s" % (table, b_s, a_s, delta))
    return "; ".join(parts)


def check_expectations(migration, before, after):
    problems = []
    for table, expected in sorted(migration.expectations.items()):
        b, a = before.get(table), after.get(table)
        if b is None or a is None:
            problems.append("%s: expected %+d but the table does not exist" % (table, expected))
            continue
        actual = a - b
        if actual != expected:
            problems.append("%s: expected %+d, got %+d (%d -> %d)"
                            % (table, expected, actual, b, a))
    return problems


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------
def ledger_rows(conn):
    ensure_ledger(conn)
    rows = conn.execute(sa.text(
        "select migration_name, checksum, status, applied_at, applied_by, duration_ms, "
        "row_counts, error, notes from %s order by migration_name" % LEDGER_TABLE)).all()
    return {r.migration_name: r for r in rows}


def drift(migrations, applied):
    """Applied migrations whose file no longer matches what ran."""
    out = []
    by_name = {m.name: m for m in migrations}
    for name, row in applied.items():
        if row.status == STATUS_FAILED:
            continue
        m = by_name.get(name)
        if m is None:
            out.append((name, "applied, but the file is missing from the migrations folder"))
        elif m.checksum != row.checksum:
            out.append((name, "the file has changed since it was applied\n"
                              "        ledger: %s\n        file:   %s"
                              % (row.checksum[:16], m.checksum[:16])))
    return out


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
def cmd_status(engine, args):
    migrations = load_migrations()
    with engine.begin() as conn:
        applied = ledger_rows(conn)
        problems = drift(migrations, applied)
        pending = [m for m in migrations if m.name not in applied
                   or applied[m.name].status == STATUS_FAILED]
        print("Database: %s" % engine.url.render_as_string(hide_password=True))
        print("Migrations folder: %d file(s)" % len(migrations))
        print()
        for m in migrations:
            row = applied.get(m.name)
            if row is None:
                state = "PENDING"
            elif row.status == STATUS_FAILED:
                state = "FAILED - will be retried"
            elif row.status == STATUS_BACKFILLED:
                state = "backfilled %s" % row.applied_at.strftime("%Y-%m-%d")
            else:
                state = "applied %s" % row.applied_at.strftime("%Y-%m-%d")
            print("  %-9s %s" % (state, m.name))
            if row is not None and row.row_counts:
                print("            %s" % row.row_counts)
        orphans = [n for n in applied if n not in {m.name for m in migrations}]
        for name in sorted(orphans):
            print("  %-9s %s" % ("ORPHAN", name))
        print()
        if problems:
            print("CHECKSUM DRIFT - the runner will not apply anything until this is resolved:")
            for name, why in problems:
                print("  %s\n        %s" % (name, why))
            print()
        ok = sum(1 for r in applied.values() if r.status != STATUS_FAILED)
        print("%d pending, %d applied, %d failed, %d drifted"
              % (len(pending), ok - len(problems),
                 sum(1 for r in applied.values() if r.status == STATUS_FAILED),
                 len(problems)))
    return 1 if problems else 0


def cmd_verify(engine, args):
    migrations = load_migrations()
    with engine.begin() as conn:
        applied = ledger_rows(conn)
    problems = drift(migrations, applied)
    if not problems:
        print("All %d applied migration(s) match their file on disk." % len(applied))
        return 0
    print("CHECKSUM DRIFT:")
    for name, why in problems:
        print("  %s\n        %s" % (name, why))
    return 1


def split_statements(sql):
    """Split SQL on the semicolons that actually end a statement.

    Four things contain a semicolon that does not: a line comment, a block
    comment, a quoted string, and a dollar-quoted body. Function and trigger
    bodies are the reason this matters most - they are dollar-quoted and full
    of semicolons, so a naive split cuts them in half. The comment cases were
    found by the recovery demonstration: a semicolon inside a `--` comment was
    ending the statement early and producing a syntax error that pointed at the
    prose rather than at the SQL."""
    statements, buf = [], []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        rest = sql[i:]

        if rest.startswith("--"):
            end = sql.find("\n", i)
            end = n if end == -1 else end
            buf.append(sql[i:end]); i = end; continue

        if rest.startswith("/*"):
            end = sql.find("*/", i + 2)
            end = n if end == -1 else end + 2
            buf.append(sql[i:end]); i = end; continue

        if ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":   # '' is an escaped quote
                        j += 2; continue
                    j += 1; break
                j += 1
            buf.append(sql[i:j]); i = j; continue

        if ch == '"':
            j = sql.find('"', i + 1)
            j = n if j == -1 else j + 1
            buf.append(sql[i:j]); i = j; continue

        match = re.match(r"\$[a-zA-Z_]*\$", rest)
        if match:
            tag = match.group(0)
            end = sql.find(tag, i + len(tag))
            end = n if end == -1 else end + len(tag)
            buf.append(sql[i:end]); i = end; continue

        if ch == ";":
            statements.append("".join(buf)); buf = []; i += 1; continue

        buf.append(ch); i += 1

    if "".join(buf).strip():
        statements.append("".join(buf))

    def is_executable(statement):
        """Strip comments; what is left is what Postgres would run.

        A trailing comment after the last semicolon is not a statement. Sending
        it produced "can't execute an empty query" and masked the real error
        further up - also found by the recovery demonstration."""
        body = re.sub(r"/\*.*?\*/", " ", statement, flags=re.S)
        body = "\n".join(re.sub(r"--.*$", "", line) for line in body.splitlines())
        return bool(body.strip())

    return [s.strip() for s in statements if is_executable(s)]


class AssertionFailed(Exception):
    """Raised inside the migration transaction so that a migration which did
    not do what it claimed is rolled back rather than merely reported."""


def apply_one(engine, migration, applied_by, dry_run=False):
    tables = migration.tables_to_count()

    if dry_run:
        print("  would apply %s" % migration.name)
        if tables:
            print("    counting: %s" % ", ".join(tables))
        for table, expected in sorted(migration.expectations.items()):
            print("    expects:  %s %+d" % (table, expected))
        return True

    started = time.time()
    error = None
    before = after = {}

    if migration.in_transaction:
        # Counts, statements and assertions all inside ONE transaction. The
        # assertion has to be in here: checking after the commit would leave a
        # migration that is recorded as failed but has already changed the
        # data, which is the worst of both outcomes.
        try:
            with engine.begin() as conn:
                before = count_tables(conn, tables)
                for statement in split_statements(migration.sql):
                    conn.execute(sa.text(statement))
                after = count_tables(conn, tables)
                problems = check_expectations(migration, before, after)
                if problems:
                    raise AssertionFailed("; ".join(problems))
        except AssertionFailed as exc:
            error = "row-count assertion failed - %s (rolled back)" % exc
            # The counts observed inside the transaction are in the error text
            # above. What the ledger records is the state that SURVIVED, which
            # after a rollback is the state we started from - reporting the
            # in-transaction numbers would say the table changed when it did not.
            after = before
        except Exception as exc:
            error = str(exc).strip().splitlines()[0][:500]
            after = before
    else:
        # No transaction was requested, so nothing here can be rolled back.
        # Counts are still recorded, and a failed assertion is still a failure -
        # it just cannot be undone, which is the cost of @no-transaction.
        with engine.begin() as conn:
            before = count_tables(conn, tables)
        try:
            with engine.connect() as conn:
                conn = conn.execution_options(isolation_level="AUTOCOMMIT")
                for statement in split_statements(migration.sql):
                    conn.execute(sa.text(statement))
        except Exception as exc:
            error = str(exc).strip().splitlines()[0][:500]
        with engine.begin() as conn:
            after = count_tables(conn, tables)
        if error is None:
            problems = check_expectations(migration, before, after)
            if problems:
                error = ("row-count assertion failed - %s (NOT rolled back: this migration "
                         "declared @no-transaction)" % "; ".join(problems))

    duration = int((time.time() - started) * 1000)
    counts = format_counts(before, after)
    status = STATUS_APPLIED if error is None else STATUS_FAILED

    with engine.begin() as conn:
        ensure_ledger(conn)
        conn.execute(sa.text(
            "insert into %s (migration_name, checksum, status, applied_by, duration_ms, "
            "row_counts, error) values (:n, :c, :s, :b, :d, :r, :e) "
            "on conflict (migration_name) do update set checksum = excluded.checksum, "
            "status = excluded.status, applied_at = now(), applied_by = excluded.applied_by, "
            "duration_ms = excluded.duration_ms, row_counts = excluded.row_counts, "
            "error = excluded.error" % LEDGER_TABLE),
            {"n": migration.name, "c": migration.checksum, "s": status, "b": applied_by,
             "d": duration, "r": counts, "e": error})

    if error is None:
        print("  applied  %s  (%d ms)%s"
              % (migration.name, duration, ("  " + counts) if counts else ""))
        return True
    print("  FAILED   %s  (%d ms)" % (migration.name, duration))
    print("           %s" % error)
    if counts:
        print("           %s" % counts)
    return False


def cmd_up(engine, args):
    migrations = load_migrations()
    with engine.begin() as conn:
        applied = ledger_rows(conn)
    problems = drift(migrations, applied)
    if problems:
        print("Refusing to apply anything - checksum drift:")
        for name, why in problems:
            print("  %s\n        %s" % (name, why))
        print("\nA migration that has already run must not be edited. Add a new one.")
        return 1

    pending = [m for m in migrations
               if m.name not in applied or applied[m.name].status == STATUS_FAILED]
    if not pending:
        print("Nothing to do - %d migration(s) already applied." % len(applied))
        return 0

    print("%d migration(s) to apply:" % len(pending))
    who = args.by or os.environ.get("USER") or "unknown"
    for migration in pending:
        if not apply_one(engine, migration, who, dry_run=args.dry_run):
            # Stop at the first failure. Running the next migration on top of a
            # half-applied schema is how a recoverable problem becomes an
            # unrecoverable one.
            print("\nStopped at the first failure. Nothing after this ran.")
            return 1
    print("\nDone.")
    return 0


def cmd_backfill(engine, args):
    """Record a migration as already applied, without running it.

    For a schema change applied before this runner existed. The file must be
    present so its checksum can be recorded - back-filling a name with no file
    would put a hash in the ledger that nothing can ever verify."""
    migrations = {m.name: m for m in load_migrations()}
    migration = migrations.get(args.name)
    if migration is None:
        print("No migration file named %s. Present: %s"
              % (args.name, ", ".join(sorted(migrations)) or "none"))
        return 1
    with engine.begin() as conn:
        ensure_ledger(conn)
        existing = conn.execute(sa.text(
            "select status from %s where migration_name = :n" % LEDGER_TABLE),
            {"n": args.name}).scalar()
        if existing in (STATUS_APPLIED, STATUS_BACKFILLED):
            print("%s is already recorded as %s." % (args.name, existing))
            return 0
        conn.execute(sa.text(
            "insert into %s (migration_name, checksum, status, applied_by, notes) "
            "values (:n, :c, :s, :b, :t) on conflict (migration_name) do update set "
            "checksum = excluded.checksum, status = excluded.status, applied_at = now(), "
            "applied_by = excluded.applied_by, notes = excluded.notes, error = null"
            % LEDGER_TABLE),
            {"n": args.name, "c": migration.checksum, "s": STATUS_BACKFILLED,
             "b": args.by or os.environ.get("USER") or "unknown",
             "t": args.note or "Applied before the migration runner existed."})
    print("Recorded %s as backfilled. It will not be run." % args.name)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="PI3 migration runner")
    sub = parser.add_subparsers(dest="command", required=True)
    p_status = sub.add_parser("status", help="what is applied and what is pending")
    p_up = sub.add_parser("up", help="apply every pending migration")
    p_up.add_argument("--dry-run", action="store_true")
    p_up.add_argument("--by")
    sub.add_parser("verify", help="re-check every applied checksum")
    p_bf = sub.add_parser("backfill", help="record a migration as already applied")
    p_bf.add_argument("name")
    p_bf.add_argument("--by")
    p_bf.add_argument("--note")
    args = parser.parse_args(argv)

    engine = engine_from_env()
    return {
        "status": cmd_status, "up": cmd_up, "verify": cmd_verify, "backfill": cmd_backfill,
    }[args.command](engine, args)


if __name__ == "__main__":
    sys.exit(main())
