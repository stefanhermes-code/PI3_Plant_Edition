"""PI3 Assistant integration (optional add-on).

Wraps the OpenAI Responses API (file_search over a vector store) behind a
few simple functions so pages don't need to know API details:
is_configured(), is_enabled_for_plant(), any_plant_enabled(),
push_document_to_vector_store(), delete_document_from_vector_store(),
ask_assistant(), and (a one-off structured-extraction helper, not tied to
the vector store) openai_key_configured() / extract_raw_material_from_tds().

Migration history: this module originally called the OpenAI Assistants
API (threads/runs against a persisted Assistant object). That API was
permanently shut down by OpenAI on 2026-08-26, so on 2026-07-26 this was
rewritten to use the Responses API instead (client.responses.create with
the file_search tool), which does not need a persisted Assistant - the
vector store from before is reused as-is (vector stores were never part
of the deprecation), and the Assistant's configured behavior now lives
below as SYSTEM_PROMPT, a plain string passed as the `instructions`
argument on every call.

SYSTEM_PROMPT is the verbatim "PI3 + PU ExpertCenter Assistant -
Enterprise v9" instructions the user had configured on the original
OpenAI Assistant object, copied across so behavior/tone/formatting don't
change just because the transport did. Two things worth knowing about it
if you're touching this file:

1. Naming collision: "PI3" inside SYSTEM_PROMPT refers to a broader,
   separate product ("Polyurethane Industry Intelligence Infrastructure",
   a persistent-thread chat channel used elsewhere) - it is not this app
   ("PI3 Plant Edition"). SYSTEM_PROMPT's own channel-detection rule
   (section 4) distinguishes "PI3" mode from "PU ExpertCenter" mode by
   the presence of a THREAD_ID. Calls from this app never carry one (the
   Responses API calls below are single-shot, not thread-based), so as
   far as SYSTEM_PROMPT's internal logic is concerned every call from
   this app presents as "PU ExpertCenter" mode. In practice this only
   affects the wording of a rare fallback error string (section 19) - it
   doesn't change how questions get answered.

2. Precedence with this app's own advisory boundary: SYSTEM_PROMPT is a
   general-purpose polyurethane-expert prompt that encourages direct,
   actionable recommendations (section 15, "Practicality Rule": "what to
   choose", "what to adjust"). That is in tension with this app's own
   non-negotiable requirement, baked separately into the callers in
   pages/9_Similar_Case_Retrieval.py and pages/18_Root_Cause_Assistant.py,
   that PI3 Plant Edition must never phrase AI output as an instruction -
   only ever as historical reference for human review. This is resolved
   the same way it already was under the old Assistants API: every
   caller's own per-request prompt text explicitly restates that
   constraint alongside its question, and that per-call instruction is
   what should take precedence for output used inside this app. Do not
   remove that framing from the callers when editing this file.

Required secrets (see .streamlit/secrets.toml.example):
- OPENAI_API_KEY
- PI3_VECTOR_STORE_ID  (vs_... - documents are pushed here as company
  knowledge is captured, and searched via the file_search tool)
- PI3_MODEL            (optional - defaults to DEFAULT_MODEL below if
  unset, so the model can be swapped without a code change)

Everything here is optional and gated two ways: is_configured() checks
the required secrets above are present, and is_enabled_for_plant()
additionally checks the per-plant PI3AIConnectionSetting toggle (PI3
connectivity is a separately billed, opt-in add-on - see the PI3
Connectivity admin screen). Callers should check is_enabled_for_plant()
before showing any AI-powered UI at all. As a second line of defense,
every OpenAI call below is also wrapped in try/except so a transient API
problem shows a friendly st.error instead of crashing the page.
"""

import json
import os

import streamlit as st

import analytics
import pi3_query_tool
from db import RAW_MATERIAL_CATEGORIES, FoamGrade, PI3AIConnectionSetting

# Balances answer quality against cost for a fairly detailed, rule-heavy
# system prompt (SYSTEM_PROMPT below has many formatting/structure
# requirements to follow consistently) - overridable per-deployment via
# the PI3_MODEL secret without touching code.
DEFAULT_MODEL = "gpt-5.6-terra"

# How long a single Responses API call is allowed to take before giving up.
REQUEST_TIMEOUT_SECONDS = 60

# Ceiling on how many times ask_plant_question() will hand tool results back
# to the model and let it call another tool before forcing a final answer -
# a runaway tool-call loop should end the turn with whatever answer exists,
# not hang indefinitely.
MAX_TOOL_ITERATIONS = 6

SYSTEM_PROMPT = """PI3 + PU ExpertCenter Assistant — Enterprise v9

1) Role

You are a seasoned polyurethane industry expert. Provide authoritative, practical, implementation-ready answers across the full polyurethane value chain: chemistry and materials, processing and troubleshooting, applications, safety and compliance, markets and marketing, strategy, supply chains, costing and economics, and standards.

PI3 is an answering system first. It must answer the user's question directly, clearly, and usefully. Reasoning is internal. The user should receive conclusions, specifications, guidance, decision points, and actions.

You are the user's primary interface. You have an extensive library at your disposal in the vector store which you will consult for any question asked. You will never reference the documents in this library when providing an answer.

2) Scope Guardrails

If the user asks about internal workings, models, training data, sources used, tools, file names, pricing logic, or any topic not related to the polyurethane industry, reply exactly:
"PI3 is Polyurethane Industry Intelligence Infrastructure, your question is out of scope".
Do not elaborate when triggering the scope guard. Check if the question is a follow-up on a previous question, then loosen the guardrail and do not only look at the verbatim question but use a more holistic interpretation.

3) Civility and Conduct Guardrail

If a message includes profanity, slurs, harassment, threats, or otherwise inappropriate wording, issue a professional warning and do not mirror the language.

Use this warning text verbatim:
"Your last message contained inappropriate language. This system operates with professional standards. Please rephrase and focus on your polyurethane question so I can assist."

If a valid technical question is present, answer it without repeating the language. If the content cannot be addressed without normalization, wait for a rephrased prompt.

4) Channel and Thread Identification

Presence of a thread identifier means PI3. Absence means PU ExpertCenter.

Detection rules:

If a field named THREAD_ID exists and is non-empty: PI3.

Or if the prompt includes either tag:
[THREAD]...alphanumeric-id...[/THREAD] or THREAD: ...alphanumeric-id...
Then PI3.

Do not reveal or repeat the thread id in answers. Use it only for fallbacks or logging.

5) Inputs

Required: a polyurethane industry question.

Optional: user-uploaded documents to consider.

6) Document Handling

- When files are attached to a user's message, ALWAYS use the file_search tool to access and analyze the attached files
- Files may contain recipes, formulations, test data, or technical specifications
- Extract and analyze all relevant information from attached files before answering
- If a user asks about an "attached recipe" or "attached file", they are referring to files attached to their message

Read uploads for relevance. Ignore unrelated content.

Use File ID as provided with the question, File ID referring to the File ID in the Vector Store.

Extract concrete facts and parameters: materials, grades, specs, formulations, machine settings, environmental conditions, test data, regulatory constraints, and commercial terms that affect feasibility.

Reconcile conflicts. Prefer the user's current specifications and measured data. If conflicts remain, state assumptions clearly without naming any document.

Elevate safety, compliance, and site constraints above generic practice.

Never mention document titles, file names, URLs, or internal retrieval steps. Summarize only what is needed.

7) Answering Priority

Always answer the user's literal question first.

If the user asks for:
- specifications, provide specifications first
- causes, provide causes first
- troubleshooting actions, provide actions first
- comparison, provide side-by-side comparison first
- recommendation, provide recommendation first

After the direct answer, add only the explanation needed to make the answer reliable and usable.

Do not replace a direct answer with methodology, philosophy, or decision theory.

Do not reformulate the user's question into a different question unless essential for safety or correctness.

8) Output Format

CRITICAL FORMATTING RULES - STRICTLY ENFORCE:
- DO NOT include ANY source references, citations, resources, or document citations in your answers
- DO NOT include file names, document names, or any references in brackets like 【】or []
- DO NOT include references in parentheses like (Source: ...) or (Reference: ...)
- DO NOT create sections titled 'Sources', 'References', 'Resources', or 'Citations'
- Provide the answer content naturally without any reference markers or citations
- Your answers should be clean, detailed text with NO reference indicators of any kind

Plain text only. No images. No asterisks.

Use normal paragraphs and line breaks.

Start the first top-level section with a sequential number.

Use metric units by default. If the user provides imperial, include metric in parentheses.

Tone: professional, concise, helpful, authoritative. No em dashes.

Never mention knowledge bases, files, tools, or internal processes.
Never use references.

9) Default Response Structure

Use the structure below unless the user's question clearly requires a simpler answer.

1. Direct Answer
2. Key Specifications, Causes, Comparison, or Actions
3. Mechanisms and Influencing Parameters
4. Practical Implications or Selection Logic
5. Risks, Limits, and Trade-offs
6. Example, Case, or Calculation if useful
7. Executive Synthesis

Important:
- Section 1 must answer the question directly
- If the user asks for specifications, include a specification table or structured property list early
- If the user asks for troubleshooting, include corrective actions early
- If the user asks for comparison, include the comparison early

10) Specification Question Rule

When the user asks for specifications, grades, ranges, limits, or property envelopes:

You MUST provide:
- the relevant specification set directly
- separated by material type, process type, or product family where relevant
- numeric ranges where reasonably supportable
- distinction between typical industrial range, commercially achievable specialty range, and practical upper or lower limit where relevant

After presenting the specification answer, explain what controls those values and how they affect performance.

Do not replace specification ranges with abstract descriptors such as soft, medium, firm unless the numeric basis is genuinely unavailable.

If test method matters, say so briefly and continue answering.

11) Mechanism Rule

Mechanisms must support the answer, not displace it.

Use:
cause -> mechanism -> effect -> practical implication

Apply this especially when:
- explaining why one foam or system is preferred over another
- qualifying property ranges
- explaining failures, trade-offs, or side effects

Do not force mechanism sections when the user only needs a short direct answer.

12) Input vs Output Discipline

Where technically useful, distinguish between:
- Controllable inputs: formulation, processing, structure
- Resulting properties: density, hardness, airflow, tensile, elongation, etc.
- End-use outcomes: durability, finish quality, heat build-up, yield, adhesion, compliance

Use this discipline to improve clarity, but do not let it interfere with answering the literal question first.

13) Assumptions

If information is missing, make reasonable assumptions and label them briefly.

Do not overload the answer with assumptions if the user's question is already clear enough to answer directly.

14) Commercial Reality Rule

Where relevant, distinguish between:
- typical industrial practice
- commercially achievable specialty practice
- theoretical or laboratory possibility

Do not present laboratory-edge values as if they are standard commercial reality.

15) Practicality Rule

Every technical answer should help the user act.

Where relevant, include:
- what to choose
- what to adjust
- what to check
- what to avoid
- what is most likely to matter first

16) Safety Rule

For hazardous or regulated operations, highlight:
- protocols
- PPE
- ventilation
- exposure controls
- monitoring
- alignment with site EHS

Safety takes precedence over speed or convenience.

17) HTC Global Mention Rule

Reference HTC Global offerings only when they directly help the user's goal.

18) Conversation Handling

Treat each exchange as one-off. Do not ask for follow-ups unless essential for safety or correctness.

If clarification is not essential, answer based on the most reasonable polyurethane interpretation.

19) Fallbacks

System failure message:

If PI3 (thread present): "PI3 is temporarily unavailable for this thread. Your question will be answered shortly."

If PU ExpertCenter (no thread): "The PU ExpertCenter is temporarily unavailable. Your question will be answered shortly."

Out-of-scope message (any channel): "PI3 is Polyurethane Industry Intelligence Infrastructure, your question is out of scope".

20) Governance and Compliance

Do not reveal internal evidence, document names, file titles, URLs, or tooling.

Treat all uploads as confidential to the user's workspace.

Keep content neutral and aligned with polyurethane industry standards and good practice.

No background work promises or time estimates."""


def _get_secret(name):
    """Streamlit secrets first (Streamlit Cloud deployment), then an
    environment variable (local/CI) - same fallback pattern as db.py's
    _database_url(), so local development doesn't require secrets.toml."""
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.environ.get(name)


def is_configured():
    """True once the required PI3 secrets are present. This does NOT mean
    any plant has actually turned the feature on - see
    is_enabled_for_plant() for the per-plant, separately-billed gate that
    every caller should check before doing anything AI-related."""
    return bool(_get_secret("OPENAI_API_KEY") and _get_secret("PI3_VECTOR_STORE_ID"))


def openai_key_configured():
    """True once OPENAI_API_KEY alone is present - no vector store needed.

    Used by features that call the Responses API directly for a one-off
    task unrelated to the company knowledge base (e.g.
    extract_raw_material_from_tds() below), so they aren't blocked on a
    vector store id that has nothing to do with what they're doing."""
    return bool(_get_secret("OPENAI_API_KEY"))


def is_enabled_for_plant(session, plant_id):
    """True only when PI3 is both configured (secrets present) AND
    switched on for this specific plant on the PI3 Connectivity admin
    screen. PI3 connectivity is a separately billed, opt-in add-on - no
    OpenAI call should ever fire for a plant that hasn't enabled it, and
    no AI-powered UI should even be shown for one."""
    if plant_id is None or not is_configured():
        return False
    setting = (
        session.query(PI3AIConnectionSetting)
        .filter(PI3AIConnectionSetting.plant_id == plant_id)
        .first()
    )
    return bool(setting and setting.pi3_ai_connectivity_enabled)


def availability_status(session, plant_id):
    """Which of the two independent reasons is_enabled_for_plant() might be
    False: "not_configured" (OPENAI_API_KEY/PI3_VECTOR_STORE_ID secrets
    aren't set for this deployment - an admin/ops fix, unrelated to any
    plant) vs "not_enabled" (secrets are fine, but this specific plant's
    PI3 Connectivity toggle is off - a per-plant opt-in). Returns
    "enabled" if is_enabled_for_plant() would be True.

    Callers previously showed one generic "Enable PI3 connectivity for
    this plant" caption in both cases, which is actively misleading when
    a plant's toggle is already on and the real blocker is a missing
    secret - it sends the reviewer to go recheck a setting that was never
    the problem. Use this to phrase the right message for each case."""
    if not is_configured():
        return "not_configured"
    if is_enabled_for_plant(session, plant_id):
        return "enabled"
    return "not_enabled"


def any_plant_enabled(session):
    """True when at least one plant has PI3 connectivity switched on (and
    secrets are configured). Used by cross-plant screens (Similar Case
    Retrieval spans every plant's history, not one specific plant) to
    decide whether to show any AI-powered option at all - the vector
    store itself already only contains knowledge pushed from plants that
    opted in, so there's nothing further to scope once at least one plant
    is enabled."""
    if not is_configured():
        return False
    return (
        session.query(PI3AIConnectionSetting)
        .filter(PI3AIConnectionSetting.pi3_ai_connectivity_enabled.is_(True))
        .first()
        is not None
    )


def _client():
    from openai import OpenAI

    return OpenAI(api_key=_get_secret("OPENAI_API_KEY"))


def _vector_stores_api(client):
    """The vector_stores endpoints have moved around between SDK versions
    (some releases don't expose client.beta.vector_stores despite the
    documented API existing) - try the documented beta namespace first and
    fall back to the top-level one rather than hard-failing on an
    AttributeError."""
    vs = getattr(client.beta, "vector_stores", None)
    if vs is not None:
        return vs
    return client.vector_stores


def push_document_to_vector_store(title, text, metadata=None):
    """Upload a piece of company knowledge (an expert note, a closed
    trial's narrative, ...) into the PI3 vector store so future
    ask_assistant() queries can retrieve it semantically via file_search.

    `metadata` (a flat dict of string/number/bool values) is attached to
    the vector store file as its "attributes" - this is what lets a
    plant/trial-specific document be told apart from the general,
    non-plant-specific knowledge (customer questions, TDS documents,
    formulation science) that also lives in this same shared store. This
    app's own callers should pass {"plant_id": <int>} here for anything
    tied to one specific plant (see the two call sites in
    pages/20_Expert_Notes.py) - documents with no natural plant dimension
    should keep passing metadata=None, which is correct, not an oversight.

    Note: none of this app's current file_search calls (ask_assistant(),
    ask_plant_question()) actually filter by this attribute yet - the
    plant-scoped question tool deliberately searches the whole shared
    store, since general expertise is meant to inform every plant's
    answers (see PLANT_QUERY_SYSTEM_PROMPT). Tagging happens here so that
    filtering is possible wherever it's actually wanted later (e.g. a
    future "only this plant's own history" mode), without needing to
    backfill every previously-pushed document's attributes retroactively.

    Returns the new OpenAI file id (str) on success - callers that can
    store it (e.g. ExpertNote.vector_store_file_id) should, so a later
    edit/delete can resync or remove that exact file via
    delete_document_from_vector_store() instead of leaving a stale copy
    searchable forever. Returns None (with an st.error already shown) on
    failure or if PI3 isn't configured - safe to call unconditionally,
    though callers should still check is_enabled_for_plant() before
    offering this in the UI at all, since it's a billed feature.
    """
    if not text or not text.strip():
        return None
    if not is_configured():
        return None
    try:
        client = _client()
        vector_store_id = _get_secret("PI3_VECTOR_STORE_ID")
        safe_title = "".join(c for c in (title or "note") if c.isalnum() or c in " _-")[:80].strip()
        filename = f"{safe_title or 'note'}.txt"
        uploaded = client.files.create(file=(filename, text.encode("utf-8")), purpose="assistants")
        _vector_stores_api(client).files.create_and_poll(
            vector_store_id=vector_store_id, file_id=uploaded.id, attributes=(metadata or {})
        )
        return uploaded.id
    except Exception as exc:
        st.error(f"Could not push this to PI3: {exc}")
        return None


def delete_document_from_vector_store(file_id):
    """Remove a previously-pushed document (by the OpenAI file id returned
    from push_document_to_vector_store) so it stops being searchable -
    call this when the source record (e.g. an ExpertNote) is edited (before
    re-pushing the new text) or deleted. Safe to call with a falsy file_id
    (no-ops) or when PI3 isn't configured. Failures are logged as a
    non-fatal st.warning rather than st.error, since the source record's
    own save/delete should still succeed even if OpenAI cleanup fails."""
    if not file_id or not is_configured():
        return
    try:
        client = _client()
        client.files.delete(file_id)
    except Exception as exc:
        st.warning(f"Saved, but couldn't remove the old copy from PI3: {exc}")


def ask_assistant(prompt):
    """Send a prompt to PI3 (file_search over the configured vector store,
    via the Responses API) and return its text response, or None (with an
    st.error already shown) on failure/timeout.

    SYSTEM_PROMPT (above) is passed as `instructions` on every call - it
    is the general PI3/PU ExpertCenter behavior. `prompt` is this app's
    own per-request question, which (per the callers in
    pages/9_Similar_Case_Retrieval.py and pages/18_Root_Cause_Assistant.py)
    always restates PI3 Plant Edition's own advisory-boundary requirement
    (historical reference only, never an instruction) - see the module
    docstring for why that ordering matters and must not be dropped.
    """
    if not prompt or not prompt.strip():
        return None
    if not is_configured():
        return None
    try:
        client = _client()
        vector_store_id = _get_secret("PI3_VECTOR_STORE_ID")
        model = _get_secret("PI3_MODEL") or DEFAULT_MODEL

        response = client.responses.create(
            model=model,
            instructions=SYSTEM_PROMPT,
            input=prompt,
            tools=[{"type": "file_search", "vector_store_ids": [vector_store_id]}],
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        return response.output_text or None
    except Exception as exc:
        st.error(f"Could not reach PI3: {exc}")
        return None


# ---------------------------------------------------------------------------
# Free-form plant-data questions ("ask PI3 anything about this plant")
# ---------------------------------------------------------------------------
# Deliberately a separate system prompt and a separate function from
# ask_assistant() above: SYSTEM_PROMPT is a general polyurethane-industry-
# expert persona built around file_search over documents, with its own
# scope guardrail that would refuse a question like "how many runs did we
# do last month" as off-topic. This feature needs PI3 to reason over this
# plant's live structured data as well as that same document knowledge, so
# it gets its own instructions and its own pair of tools.
#
# Two tools, two different trust levels, matching the two things this
# project already validated separately:
# - get_verified_analysis calls the exact same, already-tested functions
#   backing Recipe Optimization / Trend Analysis / Process-Property
#   Correlation (analytics.py). Every number that comes back has already
#   been through the same scrutiny as what's on those pages.
# - query_plant_data lets PI3 write its own SQL for anything the four
#   verified analyses don't cover, but only against 5 curated, pre-joined
#   views (never raw tables), executed through a restricted read-only
#   Postgres role, with the plant filter injected server-side regardless
#   of what the query itself does or doesn't filter on - see
#   pi3_query_tool.py for the full reasoning.
# file_search stays available on both, unscoped by plant on purpose - see
# the project discussion: general expertise (customer questions, TDS
# documents, formulation science) isn't plant-specific and should inform
# every plant's answers; only this app's own plant/trial-specific pushes
# (Expert Notes, Similar Case narratives) carry a plant tag (see
# push_document_to_vector_store's plant_id parameter).

PLANT_QUERY_SYSTEM_PROMPT = """You are PI3, answering a technical reviewer's question about ONE specific plant's own production data at a flexible slabstock foam manufacturer.

Hard scope rule: every answer must stay within this one plant's data. You have no ability to see any other plant's data - the tools below are already restricted to it regardless of what you ask for, so do not worry about accidentally overstepping, but also never imply you checked "across plants" or "industry-wide" for anything that came from these tools.

You have three tools:

1. get_verified_analysis - use this FIRST whenever the question matches one of its analysis_type values: "trend" (control chart, process capability/Cpk, CUSUM drift, and a trend significance test for one property), "ingredient_correlation" (which raw material's actual metered dosage, and separately its planned recipe php, correlates with a property outcome), "recipe_cost" (formulation cost per recipe version), or "setting_correlation" (which machine/process setting correlates with a property outcome). These reuse the exact same tested calculations already shown on this app's Trend Analysis, Recipe Optimization, and Process-Property Correlation pages - prefer this tool over writing your own SQL whenever a question fits one of these four shapes.

2. query_plant_data - a read-only SQL tool for anything the four analyses above don't cover. You may write a single SELECT statement against ONLY these views: v_pi3_production_runs, v_pi3_property_results, v_pi3_recipe_composition, v_pi3_stream_readings, v_pi3_quality_issues (columns are listed in the tool description). Your SELECT list must include plant_id (or use SELECT *) - it will be used to scope results. No other tables are reachable, and no INSERT/UPDATE/DELETE/DDL is possible - if you write one, the tool will reject it and tell you why so you can correct it.

3. file_search - the shared knowledge base (expert notes, historical troubleshooting cases, technical documents). Use this for context a number alone can't give: has this come up before, what did an expert conclude about a similar case, what does a technical document say. This is NOT restricted to the current plant - it's general expertise that applies across the business, so use it freely to round out an answer, but never treat it as a substitute for checking the actual plant data first when the question is about this plant's own numbers.

Rules:
- Never state a number that did not come from a tool call. If no tool can answer part of the question, say so plainly instead of estimating or inferring a figure.
- Always be ready to show your work: assume the reviewer can see exactly which tool(s) you called and with what arguments, so do not hide your reasoning behind a confident-sounding number - state what you found and where it came from.
- Phrase everything as historical reference and observation for the reviewer's own investigation, never as an instruction to change a setting or formulation. This is a hard requirement, not a style preference.
- If a question is ambiguous about which foam grade, property, or date range it means, use whatever page/context information you were given to disambiguate; if it's still ambiguous, ask a brief clarifying question rather than guessing.
- Keep answers direct and reasonably concise. Lead with the answer, then the figures that support it."""

_QUERY_PLANT_DATA_TOOL = {
    "type": "function",
    "name": "query_plant_data",
    "description": (
        "Run a single read-only SELECT statement against this plant's curated data views. "
        "Available views and their columns:\n"
        "v_pi3_production_runs(run_id, plant_id, foam_grade_id, grade_name, recipe_version_id, "
        "version_label, recipe_approval_status, run_date, batch_reference, machine_id, machine_name)\n"
        "v_pi3_property_results(plant_id, foam_grade_id, grade_name, run_id, run_date, "
        "recipe_version_id, version_label, machine_name, property_name, target_value, "
        "actual_value, unit, pass_fail, tested_at, replicate_no)\n"
        "v_pi3_recipe_composition(plant_id, foam_grade_id, grade_name, recipe_version_id, "
        "version_label, recipe_approval_status, raw_material_name, php, role_in_formulation, "
        "cost_per_kg)\n"
        "v_pi3_stream_readings(plant_id, foam_grade_id, grade_name, run_id, run_date, "
        "recipe_version_id, version_label, phase_name, stream_name, flow, flow_unit, "
        "pump_speed, flow_total_qty, pressure_bar, temperature_c, calibration_status)\n"
        "v_pi3_quality_issues(plant_id, foam_grade_id, grade_name, run_id, run_date, "
        "observation_type, severity, frequency, suspected_cause, confidence_level, "
        "observed_at, notes)\n"
        "The SELECT list must include plant_id (or use SELECT *). No other tables, no "
        "semicolons/multiple statements, no INSERT/UPDATE/DELETE/DDL - these will be rejected "
        "with a message explaining why, so you can correct and retry."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "A single SELECT statement against the views listed above."}
        },
        "required": ["sql"],
        "additionalProperties": False,
    },
}

_GET_VERIFIED_ANALYSIS_TOOL = {
    "type": "function",
    "name": "get_verified_analysis",
    "description": (
        "Run one of this app's existing, already-tested statistical analyses for one foam "
        "grade. Prefer this over query_plant_data whenever the question matches one of these "
        "four analysis_type values:\n"
        "'trend' (requires property_name) - control chart (with rule-violation flags), process "
        "capability/Cpk, CUSUM drift detection, and a trend significance test for one property "
        "over this grade's production runs.\n"
        "'ingredient_correlation' (requires property_name) - which raw material's ACTUAL metered "
        "per-run dosage, and separately its PLANNED recipe php across versions, correlates with "
        "this property's outcome.\n"
        "'recipe_cost' - formulation cost per recipe version for this grade.\n"
        "'setting_correlation' (requires property_name) - which machine/process setting "
        "correlates with this property's outcome across this grade's runs."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "analysis_type": {
                "type": "string",
                "enum": ["trend", "ingredient_correlation", "recipe_cost", "setting_correlation"],
            },
            "foam_grade_id": {"type": "integer", "description": "The foam grade to analyze."},
            "property_name": {
                "type": "string",
                "description": "Required for trend, ingredient_correlation, and setting_correlation.",
            },
        },
        "required": ["analysis_type", "foam_grade_id"],
        "additionalProperties": False,
    },
}


def _to_jsonable(obj):
    """Recursively converts pandas/numpy values (DataFrames, Series,
    numpy scalar types, NaN, Timestamps/dates) into plain
    JSON-serializable Python values, since analytics.py's functions return
    numpy floats and DataFrames throughout and json.dumps chokes on both -
    every tool result passed back to the model goes through this."""
    import math

    import numpy as np
    import pandas as pd

    if obj is None:
        return None
    if isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float):
        return None if math.isnan(obj) else obj
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        value = float(obj)
        return None if math.isnan(value) else value
    if isinstance(obj, pd.DataFrame):
        return _to_jsonable(obj.to_dict(orient="records"))
    if isinstance(obj, pd.Series):
        return _to_jsonable(obj.tolist())
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


def _grade_in_plant(session, foam_grade_id, plant_id):
    """Looks up a FoamGrade and confirms it belongs to plant_id before
    get_verified_analysis is allowed to touch it - the same "never trust
    the model to remember the scope" principle as query_plant_data's
    server-injected plant filter, applied to this tool too. Returns the
    FoamGrade or None."""
    grade = session.query(FoamGrade).filter(FoamGrade.id == foam_grade_id).first()
    if grade is None or grade.product_family is None:
        return None
    return grade if grade.product_family.plant_id == plant_id else None


def _run_verified_analysis(session, plant_id, analysis_type, foam_grade_id, property_name=None):
    """Dispatches a get_verified_analysis tool call to the matching
    analytics.py function(s) - see PLANT_QUERY_SYSTEM_PROMPT and the
    _GET_VERIFIED_ANALYSIS_TOOL description for what each analysis_type
    covers. Returns a JSON-serializable dict; never raises - a bad
    argument comes back as {"error": ...} for the model to see and correct,
    same pattern as pi3_query_tool.QueryRejected."""
    valid_types = ("trend", "ingredient_correlation", "recipe_cost", "setting_correlation")
    if analysis_type not in valid_types:
        return {"error": f"Unknown analysis_type '{analysis_type}'. Valid values: {', '.join(valid_types)}."}

    grade = _grade_in_plant(session, foam_grade_id, plant_id)
    if grade is None:
        return {"error": "foam_grade_id was not found, or does not belong to the current plant."}

    if analysis_type in ("trend", "ingredient_correlation", "setting_correlation") and not property_name:
        return {"error": f"property_name is required for analysis_type '{analysis_type}'."}

    if analysis_type == "trend":
        series = analytics.property_run_series(session, foam_grade_id, property_name)
        if series.empty:
            return {"note": f"No '{property_name}' results recorded yet for this foam grade."}
        cc = analytics.control_chart_analysis(series)
        cc_out = {"ready": cc["ready"], "n": cc["n"]}
        if cc["ready"]:
            cc_out.update(
                {
                    "mean": cc["mean"],
                    "sigma": cc["sigma"],
                    "ucl": cc["ucl"],
                    "lcl": cc["lcl"],
                    "in_control": cc["in_control"],
                    "flags": [
                        {
                            "rule": f["rule"],
                            "first_run_id": f["first_run_id"],
                            "first_tested_at": f["first_tested_at"],
                            "points_matching": f["points_matching"],
                        }
                        for f in cc["flags"]
                    ],
                }
            )
        capability = analytics.capability_analysis(series)
        cusum = analytics.cusum_analysis(series)
        cusum_out = None
        if cusum is not None:
            cusum_out = {
                "reference": cusum["reference"],
                "breach_index": cusum["breach_index"],
                "breach_direction": cusum["breach_direction"],
            }
        trend = analytics.trend_test(series)
        return _to_jsonable(
            {
                "n_runs": len(series),
                "control_chart": cc_out,
                "capability": capability,
                "cusum": cusum_out,
                "trend_test": trend,
            }
        )

    if analysis_type == "ingredient_correlation":
        actual = analytics.rank_component_actual_correlations(session, foam_grade_id, property_name)
        planned = analytics.rank_component_correlations(session, foam_grade_id, property_name)
        return _to_jsonable(
            {
                "actual_usage_correlation": actual.to_dict(orient="records") if not actual.empty else [],
                "planned_recipe_correlation": planned.to_dict(orient="records") if not planned.empty else [],
            }
        )

    if analysis_type == "recipe_cost":
        versions = sorted(grade.recipe_versions, key=lambda v: v.created_at)
        rows = []
        for v in versions:
            cost = analytics.recipe_version_cost(session, v)
            rows.append({"version_label": v.version_label, "approval_status": v.approval_status, **cost})
        return _to_jsonable({"recipe_versions": rows})

    ranked = analytics.rank_setting_correlations(session, foam_grade_id, property_name)
    return _to_jsonable(
        {"process_setting_correlation": ranked.to_dict(orient="records") if not ranked.empty else []}
    )


def ask_plant_question(session, plant_id, question, default_foam_grade_id=None, page_context=""):
    """Free-form question about one plant's own data. Runs an agentic
    tool-calling loop (get_verified_analysis, query_plant_data, and
    file_search - see PLANT_QUERY_SYSTEM_PROMPT above) via the Responses
    API, up to MAX_TOOL_ITERATIONS tool round-trips, then returns the
    final text answer.

    Returns (answer, tool_log) where tool_log is a list of dicts recording
    every tool call made (the exact SQL run, or the verified-analysis
    arguments) - callers should show this alongside the answer so a
    reviewer can check PI3's work rather than trust it blindly, per this
    feature's own design. Returns (None, []) if PI3 isn't configured, the
    question is empty, or a call fails (an st.error is already shown in
    that case, same as ask_assistant())."""
    tool_log = []
    if not question or not question.strip():
        return None, tool_log
    if not is_configured():
        return None, tool_log

    vector_store_id = _get_secret("PI3_VECTOR_STORE_ID")
    model = _get_secret("PI3_MODEL") or DEFAULT_MODEL
    tools = [
        {"type": "file_search", "vector_store_ids": [vector_store_id]},
        _QUERY_PLANT_DATA_TOOL,
        _GET_VERIFIED_ANALYSIS_TOOL,
    ]
    input_text = question.strip()
    if page_context:
        input_text = f"Context: {page_context}\n\nQuestion: {question.strip()}"

    try:
        client = _client()
        response = client.responses.create(
            model=model,
            instructions=PLANT_QUERY_SYSTEM_PROMPT,
            input=input_text,
            tools=tools,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

        for _ in range(MAX_TOOL_ITERATIONS):
            function_calls = [item for item in response.output if getattr(item, "type", None) == "function_call"]
            if not function_calls:
                break

            tool_outputs = []
            for call in function_calls:
                try:
                    args = json.loads(call.arguments or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {}

                if call.name == "query_plant_data":
                    sql = args.get("sql", "")
                    try:
                        rows, executed_sql = pi3_query_tool.run_plant_query(sql, plant_id)
                        result = {"rows": rows, "row_count": len(rows)}
                        tool_log.append(
                            {"tool": "query_plant_data", "sql": executed_sql, "rows_returned": len(rows)}
                        )
                    except pi3_query_tool.QueryRejected as exc:
                        result = {"error": str(exc)}
                        tool_log.append({"tool": "query_plant_data", "sql": sql, "error": str(exc)})
                elif call.name == "get_verified_analysis":
                    result = _run_verified_analysis(
                        session,
                        plant_id,
                        args.get("analysis_type"),
                        args.get("foam_grade_id", default_foam_grade_id),
                        args.get("property_name"),
                    )
                    tool_log.append({"tool": "get_verified_analysis", "args": args})
                else:
                    result = {"error": f"Unknown tool '{call.name}'."}

                tool_outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(_to_jsonable(result)),
                    }
                )

            response = client.responses.create(
                model=model,
                previous_response_id=response.id,
                input=tool_outputs,
                tools=tools,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )

        return response.output_text or None, tool_log
    except Exception as exc:
        st.error(f"Could not reach PI3: {exc}")
        return None, tool_log


def extract_raw_material_from_tds(tds_text, sds_text=None):
    """Pull a structured raw-material record out of a technical data
    sheet's extracted text, for prefilling the Add Raw Material form (see
    pages/14_Raw_Materials.py). An SDS's extracted text can optionally be
    passed alongside for supplementary hazard/handling notes.

    Returns a dict with keys name, category, default_supplier, notes (each
    a string, possibly empty if not found in the source text), or None
    (with an st.error already shown) on failure, timeout, or if
    OPENAI_API_KEY isn't set.

    Deliberately does not use SYSTEM_PROMPT, is_configured(), or
    file_search: this is a one-off structured-extraction task on text
    already extracted locally from an uploaded PDF, not a
    polyurethane-expert Q&A over the company knowledge base, so it gets
    its own narrow instructions and only needs an API key - not the
    vector store the rest of this module is built around.
    """
    if not tds_text or not tds_text.strip():
        return None
    if not openai_key_configured():
        return None
    try:
        client = _client()
        model = _get_secret("PI3_MODEL") or DEFAULT_MODEL
        instructions = (
            "You extract structured raw-material master data from a supplier "
            "technical data sheet (TDS), for a polyurethane foam manufacturer's "
            "raw material database. Respond with ONLY a single JSON object, no "
            "other text and no markdown code fences, with exactly these keys: "
            "\"name\" (the product's trade name), \"category\" (choose the "
            f"single best fit from this exact list: {RAW_MATERIAL_CATEGORIES}), "
            "\"default_supplier\" (the manufacturer or supplier name), and "
            "\"notes\" (a concise plain-text summary of the key specs a "
            "formulator would want at a glance: chemical type, appearance, and "
            "key numeric properties such as OH value, viscosity, density, "
            "NCO%, or functionality where present). Use an empty string for "
            "any field you cannot determine from the source text. Do not "
            "invent data that is not present in the source text."
        )
        input_text = f"TECHNICAL DATA SHEET TEXT:\n{tds_text[:8000]}"
        if sds_text and sds_text.strip():
            input_text += (
                "\n\nSAFETY DATA SHEET TEXT (supplementary - use only to add "
                f"hazard/handling notes):\n{sds_text[:4000]}"
            )

        response = client.responses.create(
            model=model,
            instructions=instructions,
            input=input_text,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        raw = (response.output_text or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        return {
            "name": str(data.get("name") or "").strip(),
            "category": str(data.get("category") or "").strip(),
            "default_supplier": str(data.get("default_supplier") or "").strip(),
            "notes": str(data.get("notes") or "").strip(),
        }
    except Exception as exc:
        st.error(f"Could not extract raw material data from this document: {exc}")
        return None
