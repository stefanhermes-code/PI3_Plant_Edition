# -*- coding: utf-8 -*-
"""Controlled regulatory reference data, held by PI3 and versioned.

WHY THIS EXISTS
---------------
Charlie's CertiPUR review of 21 August 2026 made two checks depend on data that
is neither the customer's nor this application's opinion:

  Section 3.4  The CertiPUR requirement prohibits a raw material carrying a CMR
               1A/1B or STOT SE 1 classification. That is TWO facts, not one:
               what the supplier self-classifies on the safety data sheet, and
               what the harmonised classification under CLP Regulation
               1272/2008 says about the substance regardless of what any
               supplier wrote. The second needs Annex VI to CLP.

  Section 3.2  A clean CAS screen can never clear REACH Restriction Entry 43,
               because the restriction is about aromatic amines an azo
               colourant may RELEASE. But a POSITIVE match against a known
               restricted azo colourant is a finding that should be raised
               immediately, before anybody reads a supplier letter.

WHY IT IS NOT A CONSTANT IN CODE
--------------------------------
certipur_criteria.py holds the CertiPUR requirements as a transcribed constant,
which is right: twelve criteria from one published paper, stable, reviewable by
reading the module against the source document.

Annex VI to CLP is several thousand entries maintained by ECHA and amended
several times a year. Transcribing it into a Python file would create a
regulatory reference nobody could check and that would be wrong within months.
So the reference is LOADED FROM THE OFFICIAL FILE, versioned, hashed, and
recorded with the date somebody confirmed the source - the same discipline the
REACH Regulatory Data Library uses, because it is the same problem and, in the
case of Annex VI, literally the same dataset.

THE RULE WHEN NOTHING IS LOADED
-------------------------------
A reference that is not loaded does not silently disappear. Section 3.4 cannot
return "Meets requirement" while the harmonised check has not run - Charlie's
words: "A clean SDS alone cannot return Meets requirement while the
harmonised-classification check is absent." It returns Evidence missing and
names the reference. Section 3.2 is different: its CAS step can only ever ADD a
finding, so its absence is recorded in the evidence register and changes no
conclusion.
"""

import re

import regulatory_storage
from db import RegulatoryReferenceRecord, RegulatoryReferenceSet

STATUS_ACTIVE = "active"
STATUS_SUPERSEDED = "superseded"

# ---------------------------------------------------------------------------
# The dataset catalogue (REACH R-A1)
# ---------------------------------------------------------------------------
# One entry per REGULATORY SLOT. A slot is a place in the library that holds at
# most one active dataset - "the Candidate List" - as distinct from the file
# that happens to be sitting in it today.
#
# The key is short, lower case and STABLE. It is written into every row loaded
# under it, so it is an identifier and not a label: renaming "Candidate List"
# to "SVHC Candidate List" must never orphan a dataset. The label is the thing
# that is allowed to change.
#
# Held in code rather than in a table, for the same reason the CertiPUR
# criteria are: this is not customer data, nobody edits it in the application,
# and a code constant is reviewable in a pull request.

SLOT_CANDIDATE_LIST = "candidate_list"
SLOT_ANNEX_XIV = "annex_xiv"
SLOT_ANNEX_XVII = "annex_xvii"
SLOT_ENTRY_43_AMINES = "entry_43_appendix_8_amines"
SLOT_ENTRY_43_AZO_DYES = "entry_43_appendix_9_azo_dyes"
SLOT_ANNEX_VI_CLP = "annex_vi_clp"

# mandatory: the REACH Readiness baseline is INCOMPLETE without it, and the
# assessment reports Grey rather than a colour when one is missing. Annex VI to
# CLP is optional here because it supports a cross-check rather than a
# restriction, and because CertiPUR - which first needed it - no longer reads it
# at all (Charlie's scope separation, 21 Aug 2026).
DATASET_SLOTS = (
    {
        "slot": SLOT_CANDIDATE_LIST,
        "label": "Candidate List of substances of very high concern",
        "regulation": "REACH Article 59(10)",
        "mandatory": True,
        "file_kinds": ("xlsx", "csv"),
        "source": "ECHA - Candidate List of substances of very high concern for Authorisation: "
                  "https://echa.europa.eu/candidate-list-table",
    },
    {
        "slot": SLOT_ANNEX_XIV,
        "label": "Authorisation List (Annex XIV)",
        "regulation": "REACH Annex XIV",
        "mandatory": True,
        "file_kinds": ("xlsx", "csv"),
        "source": "ECHA - Authorisation List: "
                  "https://echa.europa.eu/authorisation-list",
    },
    {
        "slot": SLOT_ANNEX_XVII,
        "label": "Restriction List (Annex XVII)",
        "regulation": "REACH Annex XVII",
        "mandatory": True,
        "file_kinds": ("xlsx", "csv"),
        "source": "ECHA - Substances restricted under REACH: "
                  "https://echa.europa.eu/substances-restricted-under-reach",
    },
    {
        "slot": SLOT_ENTRY_43_AMINES,
        "label": "Entry 43 Appendix 8 - aromatic amines",
        "regulation": "REACH Annex XVII Entry 43, Appendix 8",
        "mandatory": True,
        "file_kinds": ("csv", "xlsx"),
        "source": "ECHA - Substances restricted under REACH, Entry 43 and its appendices: "
                  "https://echa.europa.eu/substances-restricted-under-reach",
    },
    {
        "slot": SLOT_ENTRY_43_AZO_DYES,
        "label": "Entry 43 Appendix 9 - azo dyes",
        "regulation": "REACH Annex XVII Entry 43, Appendix 9",
        "mandatory": False,
        "file_kinds": ("csv", "xlsx"),
        "source": "ECHA - Substances restricted under REACH, Entry 43 and its appendices: "
                  "https://echa.europa.eu/substances-restricted-under-reach",
    },
    {
        "slot": SLOT_ANNEX_VI_CLP,
        "label": "Harmonised classifications (Annex VI to CLP)",
        "regulation": "Regulation (EC) 1272/2008, Annex VI",
        "mandatory": False,
        "file_kinds": ("xlsx",),
        "source": "ECHA - Annex VI to CLP: "
                  "https://echa.europa.eu/information-on-chemicals/annex-vi-to-clp",
    },
)

SLOTS_BY_KEY = {d["slot"]: d for d in DATASET_SLOTS}
SLOT_KEYS = tuple(d["slot"] for d in DATASET_SLOTS)
MANDATORY_SLOTS = tuple(d["slot"] for d in DATASET_SLOTS if d["mandatory"])

# Where a customer or HTC obtains each file. Shown in the UI as a source
# instruction, never as a claim that what is loaded is current.
REFERENCE_SOURCES = {d["slot"]: d["source"] for d in DATASET_SLOTS}


def slot_label(slot):
    entry = SLOTS_BY_KEY.get(slot)
    return entry["label"] if entry else slot


def require_slot(slot):
    """A slot key that is not in the catalogue is a programming error, not a
    user error, and it must not reach the database - dataset_slot is written
    into every record loaded under it."""
    if slot not in SLOTS_BY_KEY:
        raise ValueError(
            "%r is not a regulatory dataset slot. Known slots: %s"
            % (slot, ", ".join(SLOT_KEYS))
        )
    return SLOTS_BY_KEY[slot]

_CAS_RE = re.compile(r"^\d{2,7}-\d{2}-\d$")


def normalise_cas(value):
    """Digits and hyphens only, leading zeros stripped from the first block.

    Returns None for anything that is not shaped like a CAS number, so a
    free-text cell can never accidentally match a regulatory record."""
    if not value:
        return None
    c = re.sub(r"[^0-9-]", "", str(value))
    if not _CAS_RE.match(c):
        return None
    first, mid, last = c.split("-")
    first = first.lstrip("0") or "0"
    return "%s-%s-%s" % (first, mid, last)


def cas_check_digit_ok(value):
    """A CAS number carries a check digit. Validating it catches a
    transposition at the point the number enters the system rather than after
    it has failed to match something it should have matched."""
    c = normalise_cas(value)
    if not c:
        return False
    digits = c.replace("-", "")
    body, check = digits[:-1], int(digits[-1])
    total = sum(int(d) * (i + 1) for i, d in enumerate(reversed(body)))
    return total % 10 == check


def active_set(session, slot):
    """The active dataset in this slot, or None if the slot is empty."""
    return (
        session.query(RegulatoryReferenceSet)
        .filter(RegulatoryReferenceSet.dataset_slot == slot,
                RegulatoryReferenceSet.is_active.is_(True))
        .order_by(RegulatoryReferenceSet.id.desc())
        .first()
    )


def reference_state(session, slot):
    """(loaded, label). label names the version and the source-checked date so
    a report can say exactly what it looked the substance up against."""
    s = active_set(session, slot)
    if s is None:
        return False, "not loaded"
    parts = [s.version or "unversioned"]
    if s.source_checked_date:
        parts.append("source checked %s" % s.source_checked_date.isoformat())
    if not s.storage_object_key:
        # Said out loud rather than left to be discovered. A dataset whose
        # original was never retained is still usable, but it cannot be proved
        # against the file it came from.
        parts.append("no original retained")
    return True, "%s (%s)" % (s.name or slot_label(slot), ", ".join(parts))


def library_state(session):
    """One row per slot, for the library screen and for the baseline check.

    Reports every slot in the catalogue, including the empty ones - a library
    screen that lists only what has been loaded cannot show what is missing,
    which is the question somebody opens it to answer."""
    rows = []
    for entry in DATASET_SLOTS:
        active = active_set(session, entry["slot"])
        rows.append({
            "slot": entry["slot"],
            "label": entry["label"],
            "regulation": entry["regulation"],
            "mandatory": entry["mandatory"],
            "source": entry["source"],
            "loaded": active is not None,
            "set": active,
            "version": active.version if active else None,
            "source_checked_date": active.source_checked_date if active else None,
            "record_count": active.record_count if active else None,
            "original_retained": bool(active and active.storage_object_key),
        })
    return rows


def baseline_complete(session):
    """(complete, missing_slot_keys) for the mandatory datasets.

    R-E2 reports Grey rather than a colour when this is False. Kept here
    because the answer is a property of the library, not of an assessment."""
    missing = [s for s in MANDATORY_SLOTS if active_set(session, s) is None]
    return (not missing), missing


def lookup(session, slot, cas_numbers):
    """Every record in the active dataset matching one of `cas_numbers`.

    Matching is on the normalised CAS only. Name similarity is never used: a
    substance name is not an identifier and a near-match would be a regulatory
    conclusion this application cannot defend."""
    s = active_set(session, slot)
    if s is None:
        return []
    wanted = {normalise_cas(c) for c in (cas_numbers or [])}
    wanted.discard(None)
    if not wanted:
        return []
    return list(
        session.query(RegulatoryReferenceRecord)
        .filter(RegulatoryReferenceRecord.reference_set_id == s.id,
                RegulatoryReferenceRecord.cas_normalised.in_(sorted(wanted)))
        .order_by(RegulatoryReferenceRecord.id)
        .all()
    )


class DuplicateDataset(ValueError):
    """This exact file is already loaded in this slot."""

    def __init__(self, existing):
        self.existing = existing
        super().__init__(
            "This file is already loaded in the %s slot as version %s, loaded %s by %s. "
            "Loading it again would create a second dataset nobody could tell apart. "
            "If the official file has genuinely changed, its content will differ and its "
            "hash with it."
            % (slot_label(existing.dataset_slot),
               existing.version or "unversioned",
               existing.created_at.date().isoformat() if existing.created_at else "previously",
               existing.loaded_by or "an unrecorded user")
        )


def find_by_hash(session, slot, file_hash):
    """Any dataset already holding these exact bytes in this slot."""
    return (
        session.query(RegulatoryReferenceSet)
        .filter(RegulatoryReferenceSet.dataset_slot == slot,
                RegulatoryReferenceSet.file_hash == file_hash)
        .order_by(RegulatoryReferenceSet.id.desc())
        .first()
    )


def load_reference(session, slot, records, meta, *, raw_bytes,
                   original_file_name, source_checked_date, loaded_by,
                   storage_transport=None, retain_original=True):
    """Store one parsed official file as the new ACTIVE dataset in its slot.

    The sequence matters and is deliberate:

      1. the slot is checked against the catalogue - an unknown slot never
         reaches the database;
      2. the file hash is checked against this slot - the same official file
         loaded twice is refused, not silently duplicated;
      3. the original is uploaded to Supabase Storage BEFORE any row is
         written, so a storage failure leaves nothing behind to tidy up;
      4. the previous active dataset is superseded rather than deleted, and it
         records what replaced it. An assessment that cited it keeps citing it,
         which is the discipline a superseded safety data sheet already follows;
      5. the new dataset and its records are written.

    Does not commit - the caller does, so a failed parse leaves nothing.
    """
    import datetime as _dt
    import hashlib

    entry = require_slot(slot)
    file_hash = hashlib.sha256(raw_bytes).hexdigest()

    existing = find_by_hash(session, slot, file_hash)
    if existing is not None:
        raise DuplicateDataset(existing)

    storage = {"storage_backend": regulatory_storage.BACKEND_NONE,
               "storage_bucket": None, "storage_object_key": None,
               "file_size": len(raw_bytes)}
    if retain_original and regulatory_storage.is_configured():
        storage = regulatory_storage.put_original(
            slot, raw_bytes, original_file_name, transport=storage_transport
        )

    now = _dt.datetime.now(_dt.timezone.utc)

    previous = active_set(session, slot)
    if previous is not None:
        previous.is_active = False
        previous.status = STATUS_SUPERSEDED
        previous.superseded_at = now
        session.flush()

    new = RegulatoryReferenceSet(
        dataset_slot=slot,
        name=meta.get("name") or entry["label"],
        version=meta.get("version"),
        source=(meta.get("disclaimer") or "")[:400] or None,
        source_url=entry["source"],
        source_checked_date=source_checked_date,
        original_file_name=original_file_name,
        file_hash=file_hash,
        parser_name=meta.get("parser_name"),
        parser_version=meta.get("parser_version"),
        record_count=len(records),
        is_active=True,
        status=STATUS_ACTIVE,
        activated_at=now,
        activated_by=loaded_by,
        loaded_by=loaded_by,
        storage_backend=storage["storage_backend"],
        storage_bucket=storage["storage_bucket"],
        storage_object_key=storage["storage_object_key"],
        file_size=storage["file_size"],
    )
    session.add(new)
    session.flush()

    if previous is not None:
        previous.superseded_by_set_id = new.id

    for r in records:
        session.add(RegulatoryReferenceRecord(reference_set_id=new.id, **r))
    return new
