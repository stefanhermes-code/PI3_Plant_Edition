"""REACH R-A1 - the Regulatory Data Library.

Dataset slots, immutable storage of the originals, the activation workflow,
duplicate detection by file hash, and one active dataset per slot.

Every case states its input and its expected outcome. Deterministic: no
network, no credentials, no model call. Run with
`python3 tests/test_regulatory_library.py`.
"""
# ---------------------------------------------------------------------------
# Moved into the permanent suite on 22 August 2026 under the Permanent
# Automated Regression Test Suite CR. The body below is the original script,
# unchanged except for this header, the removal of the local check() helper and
# the print-and-exit summary, and paths made repository-relative instead of
# cwd-relative. The check() statements themselves were not retyped.
#
# Replayed by tests/_recorder.py. Not importable on its own.
# ---------------------------------------------------------------------------
from tests._recorder import PROJECT_ROOT, check, print  # noqa: A004
import os as _os


def _root(*parts):
    """A path inside the repository, wherever pytest was started from."""
    return _os.path.join(PROJECT_ROOT, *parts)

import datetime as dt
import os

import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import db as m
import regulatory_reference as rr
import regulatory_storage as rs



def fresh():
    eng = sa.create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    m.Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


ROWS = [{"cas_number": "584-84-9", "cas_normalised": "584-84-9",
         "substance_name": "toluene-2,4-diisocyanate", "source_row_number": 1}]
META = {"name": "Candidate List", "version": "2026-06", "parser_name": "candidate_list",
        "parser_version": "v1"}


def storage_on():
    os.environ["SUPABASE_URL"] = "https://example.supabase.co"
    os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-service-key"


def storage_off():
    os.environ.pop("SUPABASE_URL", None)
    os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)


def accepting(method, url, headers, data):
    return 200, b""


def load(session, slot=rr.SLOT_CANDIDATE_LIST, raw=b"official-file-bytes",
         rows=None, meta=None, who="Stefan Hermes", name="list.xlsx",
         checked=dt.date(2026, 8, 21), checked_by="Stefan Hermes",
         transport=accepting, storage=True, **kw):
    """A load that is expected to succeed.

    Storage is on and the transport accepts, because after the fail-closed
    rule of 21 Aug 2026 a load with neither cannot activate anything - which
    is the point, and is tested directly below rather than assumed here."""
    # storage=False leaves it unconfigured, which is how the fail-closed cases
    # are exercised - turning it on here unconditionally would defeat them.
    storage_on() if storage else storage_off()
    return rr.load_reference(
        session, slot, ROWS if rows is None else rows, dict(META, **(meta or {})),
        raw_bytes=raw, original_file_name=name, source_checked_date=checked,
        source_checked_by=checked_by, loaded_by=who,
        storage_transport=transport, **kw)


print("=" * 78)
print("A. THE SLOT CATALOGUE")
print("=" * 78)
check("six regulatory slots", 6, len(rr.DATASET_SLOTS))
check("four are mandatory for the REACH baseline", 4, len(rr.MANDATORY_SLOTS))
check("every slot key is unique", 6, len({d["slot"] for d in rr.DATASET_SLOTS}))
check("every slot key is a short stable identifier, not a label", True,
      all(d["slot"] == d["slot"].lower() and " " not in d["slot"] for d in rr.DATASET_SLOTS),
      [d["slot"] for d in rr.DATASET_SLOTS])
check("every slot names its regulation", True,
      all(d["regulation"] for d in rr.DATASET_SLOTS))
check("every slot names where to obtain the file", True,
      all(d["source"].startswith("ECHA") for d in rr.DATASET_SLOTS))
try:
    # A near-miss key, which is the realistic mistake: hyphen instead of
    # underscore. dataset_slot is written into every dataset loaded under it,
    # so a typo must fail loudly here rather than create a seventh slot.
    rr.require_slot("candidate-list")
    slot_refused, slot_msg = False, ""
except ValueError as exc:
    slot_refused, slot_msg = True, str(exc)
check("an unknown slot is refused before it reaches the database", True, slot_refused)
check("and the message lists the slots that do exist", True,
      rr.SLOT_CANDIDATE_LIST in slot_msg, slot_msg[:120])

print("\n" + "=" * 78)
print("B. LOADING A DATASET INTO A SLOT")
print("=" * 78)
s = fresh()
first = load(s); s.commit()
check("the dataset is active", True, first.is_active)
check("its status says active", "active", first.status)
check("it records who activated it", "Stefan Hermes", first.activated_by)
check("it records when", True, first.activated_at is not None)
check("it records the source-checked date", dt.date(2026, 8, 21), first.source_checked_date)
check("it records the file hash", 64, len(first.file_hash))
check("it records the record count", 1, first.record_count)
check("it records the parser and its version", ("candidate_list", "v1"),
      (first.parser_name, first.parser_version))
check("it records the original file name", "list.xlsx", first.original_file_name)
check("it carries the official source URL", True,
      (first.source_url or "").startswith("ECHA"))
check("the parsed rows are stored against it", 1,
      s.query(m.RegulatoryReferenceRecord).filter_by(reference_set_id=first.id).count())
check("every row keeps its source row number", [1],
      [r.source_row_number for r in
       s.query(m.RegulatoryReferenceRecord).filter_by(reference_set_id=first.id).all()])

print("\n" + "=" * 78)
print("C. DUPLICATE DETECTION BY FILE HASH")
print("=" * 78)
s = fresh()
load(s); s.commit()
try:
    load(s, raw=b"official-file-bytes")
    dup, why = False, ""
except rr.DuplicateDataset as exc:
    dup, why = True, str(exc)
check("the same file loaded twice into the same slot is refused", True, dup)
check("and the message says where it already is", True,
      "Candidate List" in why and "already loaded" in why, why[:160])
check("nothing was written by the refused load", 1,
      s.query(m.RegulatoryReferenceSet).count())

# The same bytes in a DIFFERENT slot are a different fact and must be allowed:
# ECHA publishes overlapping content across files.
s2 = fresh()
load(s2, slot=rr.SLOT_CANDIDATE_LIST); s2.commit()
load(s2, slot=rr.SLOT_ANNEX_XIV); s2.commit()
check("the same bytes in a different slot are allowed", 2,
      s2.query(m.RegulatoryReferenceSet).count())

print("\n" + "=" * 78)
print("D. ACTIVATION AND SUPERSESSION")
print("=" * 78)
s = fresh()
old = load(s, raw=b"june-file", meta={"version": "2026-06"}); s.commit()
old_id = old.id
new = load(s, raw=b"august-file", meta={"version": "2026-08"}); s.commit()
old = s.get(m.RegulatoryReferenceSet, old_id)
check("the new dataset is active", True, new.is_active)
check("the previous one is not", False, bool(old.is_active))
check("the previous one is marked superseded", "superseded", old.status)
check("it records when it was superseded", True, old.superseded_at is not None)
check("it records what replaced it", new.id, old.superseded_by_set_id)
check("the superseded dataset is NOT deleted", 2,
      s.query(m.RegulatoryReferenceSet).count())
check("its records are NOT deleted - an assessment that cited them keeps citing them", 1,
      s.query(m.RegulatoryReferenceRecord).filter_by(reference_set_id=old_id).count())
check("active_set returns the new one", new.id, rr.active_set(s, rr.SLOT_CANDIDATE_LIST).id)

print("\n" + "=" * 78)
print("E. ONE ACTIVE DATASET PER SLOT")
print("=" * 78)
s = fresh()
load(s, raw=b"a"); load(s, raw=b"b"); load(s, raw=b"c"); s.commit()
actives = s.query(m.RegulatoryReferenceSet).filter_by(
    dataset_slot=rr.SLOT_CANDIDATE_LIST, is_active=True).count()
check("three loads leave exactly one active dataset", 1, actives)
check("all three are retained", 3, s.query(m.RegulatoryReferenceSet).count())

# Slots are independent of each other.
s = fresh()
load(s, slot=rr.SLOT_CANDIDATE_LIST, raw=b"x")
load(s, slot=rr.SLOT_ANNEX_XIV, raw=b"y")
load(s, slot=rr.SLOT_ANNEX_XVII, raw=b"z"); s.commit()
check("loading one slot does not supersede another", 3,
      s.query(m.RegulatoryReferenceSet).filter_by(is_active=True).count())

print("\n" + "=" * 78)
print("F. LIBRARY STATE AND THE MANDATORY BASELINE")
print("=" * 78)
s = fresh()
state = rr.library_state(s)
check("every slot is reported, including the empty ones", 6, len(state))
check("nothing is loaded yet", 0, len([r for r in state if r["loaded"]]))
complete, missing = rr.baseline_complete(s)
check("the baseline is incomplete", False, complete)
check("and names all four mandatory slots", 4, len(missing))

for slot in rr.MANDATORY_SLOTS:
    load(s, slot=slot, raw=("bytes-%s" % slot).encode())
s.commit()
complete, missing = rr.baseline_complete(s)
check("loading the four mandatory datasets completes the baseline", True, complete)
check("with nothing missing", [], missing)
check("the two optional slots are still reported as empty", 2,
      len([r for r in rr.library_state(s) if not r["loaded"]]))

loaded, label = rr.reference_state(s, rr.SLOT_CANDIDATE_LIST)
check("reference_state reports the slot as loaded", True, loaded)
check("and names the version and the source-checked date", True,
      "2026-06" in label and "2026-08-21" in label, label)
# Was: asserted the label said "no original retained". After the fail-closed
# rule of 21 Aug 2026 an active dataset ALWAYS has its original, so the check
# is replaced by its inverse rather than deleted - the suite should record that
# the rule changed, and in which direction.
check("an active dataset never reports incomplete provenance", False,
      "provenance incomplete" in label, label)
check("and names who confirmed the source", True,
      "confirmed by" in label, label)

print("\n" + "=" * 78)
print("G. STORAGE OF THE ORIGINAL")
print("=" * 78)
# Storage is not configured in this environment, so the transport is injected.
# What is proved here is the module's own logic: key derivation, no-overwrite,
# the headers, and how it maps a response to an outcome.
calls = []


def ok_transport(method, url, headers, data):
    calls.append({"method": method, "url": url, "headers": headers, "data": data})
    return 200, b""


storage_on()
check("storage reports itself configured", True, rs.is_configured())

raw = b"official-file-bytes"
import hashlib
digest = hashlib.sha256(raw).hexdigest()
res = rs.put_original(rr.SLOT_CANDIDATE_LIST, raw, "Candidate List 2026-06.xlsx",
                      transport=ok_transport)
check("the object key is content-addressed on the sha256", True,
      digest in res["storage_object_key"], res["storage_object_key"])
check("the key is filed under its slot", True,
      res["storage_object_key"].startswith(rr.SLOT_CANDIDATE_LIST + "/"),
      res["storage_object_key"])
check("the original file name is preserved and made safe", True,
      res["storage_object_key"].endswith("Candidate_List_2026-06.xlsx"),
      res["storage_object_key"])
check("the backend is recorded", "supabase-storage", res["storage_backend"])
check("the size is recorded", len(raw), res["file_size"])
check("it uploads rather than overwrites", "false", calls[-1]["headers"]["x-upsert"])
check("it authenticates with the service role key", "Bearer test-service-key",
      calls[-1]["headers"]["Authorization"])
check("it sends the bytes", raw, calls[-1]["data"])

# The same bytes uploaded again: Storage answers 409, which is the expected
# result of content-addressing and not an error.
res2 = rs.put_original(rr.SLOT_CANDIDATE_LIST, raw, "Candidate List 2026-06.xlsx",
                       transport=lambda **kw: (409, b'{"message":"already exists"}'))
check("re-uploading identical bytes is not an error", res["storage_object_key"],
      res2["storage_object_key"])

# A real failure is a real failure.
try:
    rs.put_original(rr.SLOT_CANDIDATE_LIST, raw, "x.xlsx",
                    transport=lambda **kw: (403, b'{"message":"row-level security"}'))
    refused = False
except rs.StorageError as exc:
    refused = "row-level security" in str(exc)
check("a refused upload raises with the reason from Storage", True, refused)

# Loading with storage configured records where the original went.
s = fresh()
loaded_set = load(s, raw=raw, transport=ok_transport); s.commit()
check("the dataset records the bucket", "regulatory-sources", loaded_set.storage_bucket)
check("the dataset records the object key", True,
      digest in (loaded_set.storage_object_key or ""))
check("reference_state no longer says the original is missing", False,
      "no original retained" in rr.reference_state(s, rr.SLOT_CANDIDATE_LIST)[1])

storage_off()
check("storage reports itself unconfigured again", False, rs.is_configured())

print("\n" + "=" * 78)
print("H. A STORAGE FAILURE LEAVES NOTHING BEHIND")
print("=" * 78)
s = fresh()
existing = load(s, raw=b"already-here", transport=ok_transport); s.commit()
existing_id = existing.id
try:
    load(s, raw=b"new-file", transport=lambda **kw: (500, b'{"message":"storage down"}'))
    stored = True
except rs.StorageError:
    s.rollback()
    stored = False
check("a storage failure aborts the load", False, stored)
check("no partial dataset row is left", 1, s.query(m.RegulatoryReferenceSet).count())
check("the previous dataset is untouched and still active", True,
      bool(s.get(m.RegulatoryReferenceSet, existing_id).is_active))

print("\n" + "=" * 78)
print("I. FAIL CLOSED AT ACTIVATION (Charlie, 21 Aug 2026)")
print("=" * 78)
# The five controls he named, each with its inverse, because a rule that only
# ever blocks is indistinguishable from a rule that always blocks.


def try_load(session, **kw):
    """(activated, exception) - never raises, so a case can assert either."""
    try:
        return load(session, **kw), None
    except Exception as exc:
        session.rollback()
        return None, exc


print("\nI1. Storage unavailable blocks activation")
s = fresh()
seed = load(s, raw=b"the-incumbent"); s.commit()
seed_id = seed.id
got, exc = try_load(s, raw=b"newcomer", transport=None, storage=False)
check("with storage unconfigured, nothing is activated", None, got)
check("and the reason is an activation block", True,
      isinstance(exc, rr.ActivationBlocked), type(exc).__name__)
check("the message names the missing configuration", True,
      "SUPABASE_URL" in str(exc), str(exc)[:200])
check("no row was written", 1, s.query(m.RegulatoryReferenceSet).count())
check("the previous dataset is STILL ACTIVE", True,
      bool(s.get(m.RegulatoryReferenceSet, seed_id).is_active))
check("and still the active set for its slot", seed_id,
      rr.active_set(s, rr.SLOT_CANDIDATE_LIST).id)

print("\nI2. Upload failure blocks activation and preserves the incumbent")
s = fresh()
seed = load(s, raw=b"the-incumbent"); s.commit()
seed_id = seed.id
got, exc = try_load(s, raw=b"newcomer",
                    transport=lambda **kw: (500, b'{"message":"storage down"}'))
check("an upload failure activates nothing", None, got)
check("and surfaces as a storage error", True,
      isinstance(exc, rs.StorageError), type(exc).__name__)
check("no row was written", 1, s.query(m.RegulatoryReferenceSet).count())
check("the previous dataset is STILL ACTIVE", True,
      bool(s.get(m.RegulatoryReferenceSet, seed_id).is_active))

print("\nI3. Successful storage allows activation")
s = fresh()
seed = load(s, raw=b"the-incumbent"); s.commit()
seed_id = seed.id
got, exc = try_load(s, raw=b"newcomer")
s.commit()
check("with the original retained, the dataset activates", True, bool(got and got.is_active))
check("its original is recorded", True, bool(got and got.storage_object_key))
check("and the previous one is superseded", "superseded",
      s.get(m.RegulatoryReferenceSet, seed_id).status)

print("\nI4. Missing source-checked confirmation blocks activation")
s = fresh()
got, exc = try_load(s, checked=None)
check("no source-checked DATE, nothing activated", None, got)
check("and the message says the date is missing", True,
      "date the source was confirmed" in str(exc), str(exc)[:200])
check("no row was written", 0, s.query(m.RegulatoryReferenceSet).count())

s = fresh()
got, exc = try_load(s, checked_by=None)
check("no source checker named, nothing activated", None, got)
check("and the message says the check must be attributable", True,
      "attributable" in str(exc), str(exc)[:200])

s = fresh()
got, exc = try_load(s, checked_by="   ")
check("a blank name is not a signature", None, got)

_, exc_all = try_load(fresh(), checked=None, checked_by=None,
                      transport=None, storage=False)
check("every blocker is named at once, not one at a time", 3,
      sum(1 for phrase in ("SUPABASE_URL", "date the source was confirmed",
                           "attributable") if phrase in str(exc_all)),
      str(exc_all)[:300])

print("\nI5. A completed source check allows activation")
s = fresh()
got, exc = try_load(s, checked=dt.date(2026, 8, 21), checked_by="Stefan Hermes")
s.commit()
check("with the check complete, the dataset activates", True, bool(got and got.is_active))
check("who confirmed the source is retained", "Stefan Hermes",
      got.source_checked_by if got else None)
check("and when", dt.date(2026, 8, 21), got.source_checked_date if got else None)
loaded, label = rr.reference_state(s, rr.SLOT_CANDIDATE_LIST)
check("reference_state names who confirmed it", True,
      "confirmed by Stefan Hermes" in label, label)
check("and no longer warns about provenance", False,
      "provenance incomplete" in label, label)

print("\nI6. The database refuses an incomplete active dataset too")
# Belt and braces: the application guard above is the readable one, but a row
# inserted by any other route must still be refused. SQLite does not enforce
# the CHECK added by migration 0006, so this asserts the model carries the
# field the constraint depends on rather than re-testing Postgres here.
check("the model carries source_checked_by", True,
      "source_checked_by" in m.RegulatoryReferenceSet.__table__.columns)
check("and storage_object_key", True,
      "storage_object_key" in m.RegulatoryReferenceSet.__table__.columns)

storage_off()
