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

from db import RegulatoryReferenceRecord, RegulatoryReferenceSet

REFERENCE_HARMONISED_CLP = "Harmonised classification (Annex VI to CLP)"
REFERENCE_RESTRICTED_AZO = "Restricted azo colourants (REACH Annex XVII Entry 43)"

REFERENCE_TYPES = (REFERENCE_HARMONISED_CLP, REFERENCE_RESTRICTED_AZO)

# Where a customer or HTC obtains each file. Shown in the UI as a source
# instruction, never as a claim that what is loaded is current.
REFERENCE_SOURCES = {
    REFERENCE_HARMONISED_CLP:
        "ECHA - Annex VI to CLP: https://echa.europa.eu/information-on-chemicals/annex-vi-to-clp",
    REFERENCE_RESTRICTED_AZO:
        "ECHA - Substances restricted under REACH, Entry 43 and its appendices: "
        "https://echa.europa.eu/substances-restricted-under-reach",
}

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


def active_set(session, reference_type):
    """The active reference set of this type, or None if none is loaded."""
    return (
        session.query(RegulatoryReferenceSet)
        .filter(RegulatoryReferenceSet.reference_type == reference_type,
                RegulatoryReferenceSet.is_active.is_(True))
        .order_by(RegulatoryReferenceSet.id.desc())
        .first()
    )


def reference_state(session, reference_type):
    """(loaded, label). label names the version and the source-checked date so
    a report can say exactly what it looked the substance up against."""
    s = active_set(session, reference_type)
    if s is None:
        return False, "not loaded"
    parts = [s.version or "unversioned"]
    if s.source_checked_date:
        parts.append("source checked %s" % s.source_checked_date.isoformat())
    return True, "%s (%s)" % (s.name or reference_type, ", ".join(parts))


def lookup(session, reference_type, cas_numbers):
    """Every record in the active set matching one of `cas_numbers`.

    Matching is on the normalised CAS only. Name similarity is never used: a
    substance name is not an identifier and a near-match would be a regulatory
    conclusion this application cannot defend."""
    s = active_set(session, reference_type)
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


def load_reference(session, reference_type, records, meta, *, raw_bytes,
                   original_file_name, source_checked_date, loaded_by):
    """Store one parsed file as the new ACTIVE set of its type.

    The previous active set of the same type is marked superseded rather than
    deleted, and its records stay. An assessment that cited it keeps citing it,
    which is the same discipline a superseded safety data sheet follows.

    Does not commit - the caller does, so a failed parse leaves nothing."""
    import hashlib

    previous = active_set(session, reference_type)
    if previous is not None:
        previous.is_active = False
        session.flush()

    new = RegulatoryReferenceSet(
        reference_type=reference_type,
        name=meta.get("name") or reference_type,
        version=meta.get("version"),
        source=(meta.get("disclaimer") or "")[:400] or None,
        source_url=REFERENCE_SOURCES.get(reference_type),
        source_checked_date=source_checked_date,
        original_file_name=original_file_name,
        file_hash=hashlib.sha256(raw_bytes).hexdigest(),
        parser_name=meta.get("parser_name"),
        parser_version=meta.get("parser_version"),
        record_count=len(records),
        is_active=True,
        loaded_by=loaded_by,
    )
    session.add(new)
    session.flush()
    for r in records:
        session.add(RegulatoryReferenceRecord(reference_set_id=new.id, **r))
    return new
