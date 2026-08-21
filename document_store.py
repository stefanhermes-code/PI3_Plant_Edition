# -*- coding: utf-8 -*-
"""Storing supplier documents against a raw material, and reading them.

The application could already READ a technical data sheet to prefill a raw
material; it could not KEEP one. This module is the difference. It exists
because CertiPUR section 3.4 prohibits a raw material whose supplier
self-classifies it CMR 1a/1b or STOT SE 1 "from the moment this appear on the
SDS" - a criterion written by reference to a document, which therefore has to
survive the upload that produced it.

Three rules are enforced here rather than left to each caller:

  KEEP THE ORIGINAL. The bytes are stored, not only the extraction. An
  extraction is an interpretation, and when a compliance conclusion is
  challenged the answer has to be the supplier's document rather than this
  application's reading of it.

  NEVER OVERWRITE. A new revision is a new row; the previous one is marked not
  current and is neither edited nor deleted. A saved assessment references the
  document id it actually read, so a supplier reissuing a sheet cannot change
  what an assessment concluded last month.

  RECORD THE FAILURE. An extraction that does not work is stored with status
  Failed or Partial and a plain note. An unreadable safety data sheet is an
  evidence gap the assessment has to show, not an error to swallow - and the
  file is kept either way, so it can be re-read without asking the customer
  for it again.
"""

import datetime as dt
import hashlib

import ai_assistant
from db import (
    DOCUMENT_TYPE_SDS,
    DOCUMENT_TYPES,
    MAX_DOCUMENT_BYTES,
    RawMaterialDocument,
    RawMaterialSubstance,
)


def certipur_required(company):
    """True when this company has opted into CertiPUR Readiness, which is what
    makes a safety data sheet mandatory on a new raw material.

    Company may be None (platform owner viewing unscoped, or a development
    session with no resolved company), in which case nothing is required -
    an obligation with no company behind it is not an obligation."""
    return bool(company is not None and getattr(company, "certipur_enabled", False))


def _parse_iso_date(text):
    if not text:
        return None
    try:
        return dt.date.fromisoformat(str(text).strip()[:10])
    except Exception:
        return None


def current_document(session, raw_material_id, document_type=DOCUMENT_TYPE_SDS):
    """The current document of this type for a raw material, or None."""
    if not raw_material_id:
        return None
    return (
        session.query(RawMaterialDocument)
        .filter(
            RawMaterialDocument.raw_material_id == raw_material_id,
            RawMaterialDocument.document_type == document_type,
            RawMaterialDocument.is_current.is_(True),
        )
        .order_by(RawMaterialDocument.created_at.desc())
        .first()
    )


def current_document_of_any_type(session, raw_material_id, document_types):
    """The most recent current document whose type is one the caller accepts.

    Used by the readiness assessment, which asks "is there evidence of this
    kind" rather than "is there a document with this title" - see
    certipur_criteria.ACCEPTED_EVIDENCE for why those are different questions.
    Newest first, so a supplier who has since issued a fuller document is the
    one cited."""
    if not raw_material_id or not document_types:
        return None
    return (
        session.query(RawMaterialDocument)
        .filter(
            RawMaterialDocument.raw_material_id == raw_material_id,
            RawMaterialDocument.document_type.in_(list(document_types)),
            RawMaterialDocument.is_current.is_(True),
        )
        .order_by(RawMaterialDocument.created_at.desc())
        .first()
    )


def documents_for(session, raw_material_id):
    """Every document held against a raw material, newest first, including
    superseded revisions - which are the point of keeping them."""
    if not raw_material_id:
        return []
    return (
        session.query(RawMaterialDocument)
        .filter(RawMaterialDocument.raw_material_id == raw_material_id)
        .order_by(RawMaterialDocument.created_at.desc())
        .all()
    )


def material_ids_with_document(session, material_ids, document_type=DOCUMENT_TYPE_SDS):
    """Of the given raw materials, which hold a current document of this type.

    The complement is the evidence gap the readiness page reports, and the
    backfill worklist for a company that enabled CertiPUR after its raw
    materials were already on the system."""
    if not material_ids:
        return set()
    rows = (
        session.query(RawMaterialDocument.raw_material_id)
        .filter(
            RawMaterialDocument.raw_material_id.in_(list(material_ids)),
            RawMaterialDocument.document_type == document_type,
            RawMaterialDocument.is_current.is_(True),
        )
        .distinct()
        .all()
    )
    return {r[0] for r in rows}


def store_document(
    session, raw_material, file_bytes, file_name, document_type,
    uploaded_by=None, extracted_text=None, extract=True, content_type="application/pdf",
):
    """Store one supplier document against a raw material and return it.

    Does NOT commit - the caller owns the transaction, so a document and the
    raw material it belongs to are written together or not at all.

    Raises ValueError with a message meant for the user on anything that should
    stop the save: no file, an oversized file, an unknown document type. A
    failed EXTRACTION is not one of those - it is recorded on the row.

    Re-uploading a byte-identical file returns the existing current document
    untouched rather than creating a revision. A revision that did not happen
    should not appear in the evidence trail."""
    if document_type not in DOCUMENT_TYPES:
        raise ValueError("%s is not a document type this application stores." % document_type)
    if not file_bytes:
        raise ValueError("The file is empty.")
    if len(file_bytes) > MAX_DOCUMENT_BYTES:
        raise ValueError(
            "The file is %.1f MB. The limit is %d MB - a safety data sheet that large is "
            "usually a scan of something else."
            % (len(file_bytes) / (1024 * 1024), MAX_DOCUMENT_BYTES // (1024 * 1024))
        )

    file_hash = hashlib.sha256(file_bytes).hexdigest()

    existing = current_document(session, raw_material.id, document_type)
    if existing is not None and existing.file_hash == file_hash:
        return existing

    if extracted_text is None:
        extracted_text = ""

    evidence = {
        "hazard_codes": [], "signal_word": "", "supplier_name": "",
        "document_revision": "", "document_date": "", "substances": [],
        "status": "Not attempted", "notes": "", "model": "",
    }
    if extract and document_type == DOCUMENT_TYPE_SDS:
        evidence = ai_assistant.extract_sds_evidence(extracted_text)

    doc = RawMaterialDocument(
        raw_material_id=raw_material.id,
        company_id=raw_material.company_id,
        document_type=document_type,
        file_name=(file_name or "")[:300],
        content_type=content_type,
        file_bytes=file_bytes,
        file_size=len(file_bytes),
        file_hash=file_hash,
        extracted_text=extracted_text or None,
        supplier_name=(evidence.get("supplier_name") or raw_material.default_supplier or None),
        document_revision=(evidence.get("document_revision") or None),
        document_date=_parse_iso_date(evidence.get("document_date")),
        hazard_codes=",".join(evidence.get("hazard_codes") or []) or None,
        signal_word=(evidence.get("signal_word") or None),
        extraction_model=(evidence.get("model") or None),
        extraction_status=evidence.get("status"),
        extraction_notes=(evidence.get("notes") or None),
        extracted_at=dt.datetime.utcnow() if extract else None,
        is_current=True,
        uploaded_by=uploaded_by,
    )
    session.add(doc)
    session.flush()

    for item in evidence.get("substances") or []:
        session.add(
            RawMaterialSubstance(
                document_id=doc.id,
                name=(item.get("name") or "")[:300] or None,
                cas_number=(item.get("cas_number") or "")[:50] or None,
                ec_number=(item.get("ec_number") or "")[:50] or None,
                concentration=(item.get("concentration") or "")[:100] or None,
                hazard_codes=(item.get("hazard_codes") or "")[:500] or None,
            )
        )

    # Only after the new row exists, so a failure above leaves the previous
    # revision current rather than leaving the material with no document at all.
    if existing is not None:
        existing.is_current = False

    return doc


def extraction_summary(doc):
    """One line describing what was read out of a stored document, for a
    screen or a report. Written to be read by someone deciding whether to
    trust it, so it names what is missing before what is present."""
    if doc is None:
        return "No document held."
    if doc.extraction_status == "Failed":
        return "Stored, but nothing could be read from it. " + (doc.extraction_notes or "")
    codes = [c for c in (doc.hazard_codes or "").split(",") if c]
    parts = []
    if codes:
        parts.append("%d hazard code%s: %s" % (len(codes), "" if len(codes) == 1 else "s", ", ".join(codes)))
    else:
        parts.append("no hazard codes printed")
    n = len(doc.substances or [])
    parts.append("%d substance%s in the composition" % (n, "" if n == 1 else "s"))
    if doc.document_revision:
        parts.append("revision %s" % doc.document_revision)
    if doc.document_date:
        parts.append("dated %s" % doc.document_date.isoformat())
    line = "; ".join(parts) + "."
    if doc.extraction_notes:
        line += " " + doc.extraction_notes
    return line
