"""AI governance vocabulary and classification rules.

Central home for the CR "AI Governance, Human Verification, Audit
Traceability and Platform Admin Compliance View" (19 Aug 2026), first
slice: the audit trail.

Classification is here rather than on each page for the reason the CR
gives - a rule duplicated across six Intelligence pages drifts, and the
audit record then cannot say what governed a given answer. Every caller
goes through classify().

Nothing in this module touches the database or Streamlit, so it can be
imported from ai_assistant.py, audit_log.py and the admin page without a
cycle.
"""

import hashlib
import re


# --- Classification vocabulary -------------------------------------------
# Stored as text on pi3_interaction_logs.interaction_classification. Text
# rather than an enum: the CR calls these the MINIMUM classifications, so
# the set is expected to grow, and a Postgres enum would need a migration
# to add one.
INFORMATIONAL = "Informational"
TECHNICAL_ADVISORY = "Technical Advisory"
PROCESS_SAFETY_RELEVANT = "Process / Safety Relevant"

CLASSIFICATIONS = (INFORMATIONAL, TECHNICAL_ADVISORY, PROCESS_SAFETY_RELEVANT)

# How the classification was arrived at - recorded beside it so a reviewer
# can tell a deterministic call-site default from a content rule that fired.
SOURCE_FIXED_CALL_SITE = "fixed_call_site"
SOURCE_APPLICATION_RULE = "application_rule"
SOURCE_REVIEWER_OVERRIDE = "reviewer_override"

CLASSIFICATION_SOURCES = (
    SOURCE_FIXED_CALL_SITE,
    SOURCE_APPLICATION_RULE,
    SOURCE_REVIEWER_OVERRIDE,
)


# --- Deterministic defaults per call site --------------------------------
# The three fixed Intelligence functions that can influence what is dosed
# or how the line is set are Process / Safety Relevant by definition, per
# the CR's table. Trend Analysis and the correlation page report on what
# already happened, so they are advisory.
DEFAULT_CLASSIFICATION_BY_CALL_SITE = {
    "recipe_optimization": PROCESS_SAFETY_RELEVANT,
    "root_cause_assistant": PROCESS_SAFETY_RELEVANT,
    "machine_settings_optimization": PROCESS_SAFETY_RELEVANT,
    "trend_analysis": TECHNICAL_ADVISORY,
    "process_property_correlation": TECHNICAL_ADVISORY,
    "ask_assistant": TECHNICAL_ADVISORY,
    "ask_plant_question": TECHNICAL_ADVISORY,
}

# Call sites where a free-form question can itself turn an advisory answer
# into a process-relevant one. Only these are content-scanned; a fixed
# Intelligence function already carries its own default and is never
# downgraded by the scan.
CONTENT_SCANNED_CALL_SITES = ("ask_assistant", "ask_plant_question")

# Terms that put a free-form question into formulation or process-change
# territory. Kept deliberately concrete - a word that merely names a
# material ("polyol") is not enough, because asking what a polyol costs is
# not a process change. The pairing of a change verb with a process noun is
# what escalates.
_CHANGE_VERBS = (
    "increase", "decrease", "raise", "lower", "reduce", "adjust", "change",
    "replace", "substitute", "switch", "swap", "set", "retune", "tune",
    "correct", "fix", "modify", "optimis", "optimiz", "recommend", "propose",
    "should i", "should we", "can i", "can we", "what if",
)

_PROCESS_NOUNS = (
    "formulation", "recipe", "php", "index", "catalyst", "isocyanate", "tdi",
    "mdi", "amine", "tin", "water level", "water content", "surfactant",
    "silicone", "blowing agent", "dosage", "dosing", "dose", "metering",
    "component stream", "raw material", "grade of polyol", "polyol grade",
    "machine setting", "line speed", "conveyor", "throughput", "output",
    "temperature", "mixer speed", "pressure", "trough", "fall plate",
    "cream time", "rise time", "gel time", "cure",
)


def _matches_any(text, terms):
    """True when any term appears at a word start in text. Terms may be
    multi-word ("machine setting") or stems ("optimis", "optimiz")."""
    return any(re.search(r"\b" + re.escape(term), text) for term in terms)


def classify(call_site, question_text=None):
    """Returns (classification, classification_source) for one interaction.

    A fixed call site gets its deterministic default. A free-form call site
    is additionally scanned: if the question pairs a change verb with a
    process noun it is raised to Process / Safety Relevant and the source
    records that a rule, not the call site, decided it.

    An unknown call site defaults to Technical Advisory rather than
    Informational - a new AI surface should arrive in the audit trail
    marked as carrying technical content until someone classifies it
    deliberately.
    """
    site = (call_site or "").strip()
    classification = DEFAULT_CLASSIFICATION_BY_CALL_SITE.get(site, TECHNICAL_ADVISORY)
    source = SOURCE_FIXED_CALL_SITE

    if site in CONTENT_SCANNED_CALL_SITES and question_text:
        text = question_text.lower()
        # Match at a word start. Plain substring matching read the verb stem
        # "optimiz" out of the middle of nouns, which is how a page name
        # ("Recipe Optimization") escalated an innocent question.
        if _matches_any(text, _CHANGE_VERBS) and _matches_any(text, _PROCESS_NOUNS):
            classification = PROCESS_SAFETY_RELEVANT
            source = SOURCE_APPLICATION_RULE

    return classification, source


def verification_required(classification):
    """Only Process / Safety Relevant output needs a recorded human
    decision before trial or operational use."""
    return classification == PROCESS_SAFETY_RELEVANT


# --- Review vocabulary ---------------------------------------------------
REVIEW_PENDING = "Pending review"
REVIEW_ACCEPTED = "Accepted for controlled trial / evaluation"
REVIEW_MODIFIED = "Modified before trial / evaluation"
REVIEW_REJECTED = "Rejected"

REVIEW_STATUSES = (REVIEW_PENDING, REVIEW_ACCEPTED, REVIEW_MODIFIED, REVIEW_REJECTED)

# What the UI must show beside an answer for each state (CR section 13).
REVIEW_DISPLAY = {
    REVIEW_PENDING: "Technical validation required before trial or operational implementation.",
    REVIEW_ACCEPTED: "Accepted for controlled trial / evaluation.",
    REVIEW_MODIFIED: "Customer modification recorded.",
    REVIEW_REJECTED: "Recommendation rejected.",
}


# --- Prompt versioning ---------------------------------------------------
def prompt_hash(prompt_text):
    """SHA-256 of the exact instruction text sent to the model, so a later
    edit to a prompt is visible in the audit trail without relying on
    memory or on Git history alone. Truncated to 16 hex characters: enough
    to distinguish revisions, short enough to read in a table."""
    if not prompt_text:
        return None
    return hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:16]
