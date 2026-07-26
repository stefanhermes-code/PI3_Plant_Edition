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

from db import RAW_MATERIAL_CATEGORIES, PI3AIConnectionSetting

# Balances answer quality against cost for a fairly detailed, rule-heavy
# system prompt (SYSTEM_PROMPT below has many formatting/structure
# requirements to follow consistently) - overridable per-deployment via
# the PI3_MODEL secret without touching code.
DEFAULT_MODEL = "gpt-5.6-terra"

# How long a single Responses API call is allowed to take before giving up.
REQUEST_TIMEOUT_SECONDS = 60

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
            vector_store_id=vector_store_id, file_id=uploaded.id
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
