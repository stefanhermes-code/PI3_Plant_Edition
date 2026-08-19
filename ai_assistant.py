"""PI3 Assistant integration (optional add-on).

Wraps the OpenAI Responses API (file_search over a vector store) behind a
few simple functions so pages don't need to know API details:
is_configured(), is_enabled_for_plant(),
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
   actionable recommendations (section 15, "Practicality Rule"). That used
   to sit in tension with this app's own non-negotiable requirement, baked
   separately into the callers in views/18_Root_Cause_Assistant.py, that
   PI3 Plant Edition must never phrase AI output as an instruction - only
   ever as a candidate recommendation for human evaluation.

   Two things resolve it now. Section 15a, the Plant Edition Technical
   Validation Rule (added 19 Aug 2026 per Charlie's AI Governance CR,
   section 4.1), states the boundary inside the shared prompt itself and
   says explicitly that it takes precedence over the practicality wording.
   And, as before, every caller's own per-request prompt restates the
   constraint alongside its question. Do not remove that framing from the
   callers when editing this file - the two are deliberately belt and
   braces, because SYSTEM_PROMPT is shared with the wider PU ExpertCenter
   and can change outside this repository.

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
import time

import streamlit as st

import ai_governance
import analytics
import audit_log
import pi3_query_tool
from db import RAW_MATERIAL_CATEGORIES, FoamGrade, PI3AIConnectionSetting, Plant, get_session
from version import APP_VERSION

# Balances answer quality against cost for a fairly detailed, rule-heavy
# system prompt (SYSTEM_PROMPT below has many formatting/structure
# requirements to follow consistently) - overridable per-deployment via
# the PI3_MODEL secret without touching code.
DEFAULT_MODEL = "gpt-5.6-terra"

# --- Prompt version identifiers (CR of 19 Aug 2026, section 9) -------------
# Governance identifiers, recorded on every interaction so a later prompt
# edit is visible in the audit trail without relying on Git history alone.
# The hash beside each one is computed from the live prompt text at call
# time (see _governance_fields), so an edit that someone forgets to
# re-version still changes the hash and the change stays detectable.
#
# PI3-PLANT-SYSTEM-v1.0 is the GOVERNANCE version of SYSTEM_PROMPT, and is
# not the same numbering as the "Enterprise v9" heading inside the prompt
# text itself - that heading belongs to the shared PU ExpertCenter prompt
# this app inherited. Identifiers follow the CR's naming.
# Bump the identifier in the same commit as any edit to the prompt text it
# names. The hash below it catches an edit that someone forgets to version,
# but a hash alone does not say WHICH revision an interaction ran under.
# v1.1 (19 Aug 2026): CR section 4.1 - implementation-authority wording out of
# the Role and Practicality rules, Plant Edition Technical Validation Rule in.
SYSTEM_PROMPT_VERSION = "PI3-PLANT-SYSTEM-v1.1"
PLANT_QUERY_PROMPT_VERSION = "PI3-APQ-v2.0"

CALL_PROMPT_VERSIONS = {
    # v2.1 on these three (19 Aug 2026): CR sections 5.1-5.3 - candidate
    # framing, plus a verification/checks section tailored to the actual
    # recommendation rather than a fixed list.
    "recipe_optimization": "PI3-RO-v2.1",
    "root_cause_assistant": "PI3-RCA-v2.1",
    "machine_settings_optimization": "PI3-MSO-v2.1",
    "trend_analysis": "PI3-TA-v2.0",
    "process_property_correlation": "PI3-PPC-v2.0",
    "ask_assistant": "PI3-ASK-v2.0",
    "ask_plant_question": "PI3-APQ-v2.0",
}

# How long a single Responses API call is allowed to take before giving up.
REQUEST_TIMEOUT_SECONDS = 60

# Ceiling on how many times ask_plant_question() will hand tool results back
# to the model and let it call another tool before forcing a final answer -
# a runaway tool-call loop should end the turn with whatever answer exists,
# not hang indefinitely.
MAX_TOOL_ITERATIONS = 6

SYSTEM_PROMPT = """PI3 + PU ExpertCenter Assistant — Enterprise v9

1) Role

You are a seasoned polyurethane industry expert. Provide authoritative, practical, technically specific answers across the full polyurethane value chain: chemistry and materials, processing and troubleshooting, applications, safety and compliance, markets and marketing, strategy, supply chains, costing and economics, and standards.

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

Every technical answer should help the user decide. Provide technically specific analysis, candidate recommendations, specifications, decision points and actions for qualified professional evaluation.

Where relevant, include:
- candidate selections, with the basis for each
- candidate adjustments, with the direction and magnitude the evidence supports
- what to check before acting
- what to avoid
- what is most likely to matter first

15a) Plant Edition Technical Validation Rule

This rule takes precedence over the generic practicality wording above.

Where a response could influence formulation composition, raw material selection, chemical substitution, catalyst level, water level, isocyanate level or index, process temperature, process conditions, machine settings, reaction behaviour, plant safety conditions, or any other production parameter with material consequences:

1. Provide technically specific candidate guidance where the available evidence supports it. Formulations, numerical values, ranges, process observations, troubleshooting actions, machine-setting analysis and trial recommendations all remain in scope.
2. Clearly separate verified facts and calculations from assumptions, interpretation and hypothesis.
3. Identify the critical checks required before trial or implementation, chosen for the actual recommendation rather than recited as a fixed list.
4. State that a suitably qualified professional at the customer evaluates the recommendation against the actual raw materials, equipment, process conditions, applicable technical documentation and site safety requirements before operational use.
5. Where missing information materially affects technical correctness or safety, identify that missing information before presenting a candidate recommendation as ready for evaluation.

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


def _client():
    from openai import OpenAI

    return OpenAI(api_key=_get_secret("OPENAI_API_KEY"))


def _extract_token_usage(response):
    """Item 50. response.usage on the Responses API is an SDK object, not
    a dict - field names have shifted across SDK/API versions, so every
    field is read defensively via getattr rather than assumed present.
    Returns (prompt_tokens, completion_tokens, total_tokens), any of
    which may be None if the SDK in use doesn't expose it."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return None, None, None
    prompt_tokens = getattr(usage, "input_tokens", None)
    completion_tokens = getattr(usage, "output_tokens", None)
    total_tokens = getattr(usage, "total_tokens", None)
    if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
        total_tokens = prompt_tokens + completion_tokens
    return prompt_tokens, completion_tokens, total_tokens


def _estimate_cost_usd(prompt_tokens, completion_tokens):
    """Item 50. Cost is derived from optional per-1M-token rates in
    st.secrets (PI3_INPUT_COST_PER_1M_TOKENS / PI3_OUTPUT_COST_PER_1M_TOKENS,
    both USD) rather than a rate hard-coded here - OpenAI pricing changes
    over time and can vary by contract, so a baked-in figure would go
    stale silently and mislead whoever reviews the pilot-analysis page
    (Item 56). Returns None (no fabricated figure) if either rate isn't
    configured or either token count is unknown."""
    if prompt_tokens is None or completion_tokens is None:
        return None
    try:
        input_rate = _get_secret("PI3_INPUT_COST_PER_1M_TOKENS")
        output_rate = _get_secret("PI3_OUTPUT_COST_PER_1M_TOKENS")
        if input_rate is None or output_rate is None:
            return None
        return (prompt_tokens / 1_000_000) * float(input_rate) + (completion_tokens / 1_000_000) * float(output_rate)
    except Exception:
        return None


def _governance_fields(
    call_site, model, system_prompt, input_text,
    response_id=None, response_chain=None, tool_log=None, retrieval_evidence=None,
):
    """Builds the AI-governance evidence recorded against one interaction
    (CR of 19 Aug 2026, section 8.1).

    On call_prompt_hash: the fixed Intelligence pages build their prompt
    inline from live run data rather than from an isolated template, so a
    hash of the sent text is not a template fingerprint - it is an
    integrity fingerprint of the exact input, and it is stored as that.
    call_prompt_version carries the template identity instead.

    Anything that cannot be determined is left out of the dict, so the
    column stays NULL. A blank column reads as "not recorded"; a filled-in
    guess would be a false audit trail.
    """
    classification, source = ai_governance.classify(call_site, input_text)
    fields = {
        "model_name": model,
        "application_version": APP_VERSION,
        "system_prompt_version": SYSTEM_PROMPT_VERSION,
        "system_prompt_hash": ai_governance.prompt_hash(system_prompt),
        "call_prompt_version": CALL_PROMPT_VERSIONS.get(call_site),
        "call_prompt_hash": ai_governance.prompt_hash(input_text),
        "interaction_classification": classification,
        "classification_source": source,
        "verification_required": ai_governance.verification_required(classification),
        # The verification panel on the answer itself is the next slice of
        # the CR. Until it ships this stays False rather than claiming a
        # notice was shown that was not.
        "verification_message_shown": False,
    }
    if response_id:
        fields["openai_response_id"] = response_id
    if response_chain and len(response_chain) > 1:
        fields["openai_response_chain_json"] = _safe_json(response_chain)
    if tool_log:
        fields["tool_log_json"] = _safe_json(tool_log)
    if retrieval_evidence:
        fields["retrieval_evidence_json"] = _safe_json(retrieval_evidence)
    return fields


def _safe_json(value):
    """json.dumps that never raises - audit evidence is best-effort and must
    not be able to break the answer the reviewer is waiting for."""
    try:
        return json.dumps(_to_jsonable(value))
    except Exception:
        try:
            return json.dumps({"unserializable": str(value)[:2000]})
        except Exception:
            return None


def _record_pi3_interaction(
    call_site, question_text, response_text, company_id=None, plant_id=None,
    prompt_tokens=None, completion_tokens=None, total_tokens=None, start_time=None,
    governance=None,
):
    """Items 49-51. Called from both ask_assistant() and
    ask_plant_question() right after a successful call, so every PI3
    question/answer across every call site (all 5 fixed-prompt Intelligence
    sections plus every free-form 'Ask PI3' box) is captured the same way
    with no per-page wiring needed. Token counts are passed in already
    extracted (see _extract_token_usage) rather than a raw response object,
    since ask_plant_question's tool-calling loop makes several Responses
    API calls per question and needs to sum usage across all of them, not
    just the last one. Returns the new PI3InteractionLog row (or None on
    failure) - callers that want to attach a feedback control (Item 55)
    should hold onto its .id."""
    estimated_cost_usd = _estimate_cost_usd(prompt_tokens, completion_tokens)
    response_time_ms = (time.monotonic() - start_time) * 1000 if start_time is not None else None
    try:
        session = get_session()
        return audit_log.log_pi3_interaction(
            session,
            call_site=call_site,
            question_text=question_text,
            response_text=response_text,
            user_id=st.session_state.get("user_id"),
            company_id=company_id,
            plant_id=plant_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=estimated_cost_usd,
            response_time_ms=response_time_ms,
            governance=governance,
        )
    except Exception:
        return None


def _record_pi3_error(call_site, exc, company_id=None, plant_id=None):
    """Item 52. Records a failed PI3 call - this is one of the two
    highest-value error points in the app (the other being DB session
    recovery, see db.py), so it's captured here centrally rather than
    waiting on the broader app-wide error-logging pass."""
    try:
        session = get_session()
        audit_log.log_error(
            session,
            error_message=f"PI3 call failed in {call_site}",
            exc=exc,
            user_id=st.session_state.get("user_id"),
            company_id=company_id,
            page_name=st.session_state.get("_current_page_title"),
        )
    except Exception:
        pass


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
    app's own callers should pass {"plant_id": <int>, "company_id": <int>}
    here for anything tied to one specific plant (see the two call sites
    in views/20_Expert_Notes.py and helpers.render_save_to_expert_notes_button)
    - documents with no natural plant dimension should keep passing
    metadata=None, which is correct, not an oversight (see the "shared"
    tag note below for how those stay searchable under the filter).

    company_id is the key both ask_assistant() and ask_plant_question()
    now filter on (see _file_search_filters() below) - always include it
    alongside plant_id, not just plant_id alone, so a pushed document is
    actually excluded from a different company's searches rather than
    only carrying a plant tag that nothing filters on.

    FIXED 2026-08-02 (Gate 3, Item 21 of the Duroflex pilot readiness
    list; originally flagged as PI3_Gaps_and_Ambiguities.docx finding 3.1,
    2026-08-01): both ask_assistant() and ask_plant_question() now pass a
    `filters` parameter on the file_search tool (OpenAI's Responses API
    ComparisonFilter/CompoundFilter - see
    developers.openai.com/api/docs/guides/retrieval#attribute-filtering,
    confirmed current 2026-08-02) that restricts results to documents
    tagged company_id=<the asking company> OR tagged shared=True - see
    _file_search_filters() below. The structured-data path
    (pi3_query_tool.py's SQL views/guard, and get_verified_analysis's
    _grade_in_plant check) was already separately scoped by plant_id and
    was never affected by this gap.

    REQUIRED ONE-TIME STEP: this filter only works for documents that
    already carry one of those two tags. Every document pushed through
    this function from now on gets company_id automatically (as long as
    callers follow the convention above), but documents pushed before
    this fix - the pre-existing general reference library (uploaded
    directly via OpenAI, never through this function, so it has no
    attributes at all) and any Expert Notes saved before 2026-08-02
    (tagged with plant_id only, no company_id) - are NOT covered
    retroactively by this code change alone. Run
    backfill_vector_store_tenant_tags.py once against production (see
    that script's own docstring) to tag the existing library shared=True
    and backfill company_id onto existing plant-tagged files from this
    app's own Plant.company_id column. Until that script has been run,
    older documents matching neither condition will stop appearing in
    filtered search results (fail closed, not open - they go missing
    rather than leak, which is the safe direction for this gap to fail
    in, but still worth doing promptly so nothing useful goes dark).

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
    except Exception:
        st.error("Could not push this to PI3 right now. It's still saved - try again in a moment.")
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
    except Exception:
        st.warning("Saved, but couldn't remove the old copy from PI3. It may still appear in search results.")


def _file_search_filters(company_id):
    """The `filters` argument for the file_search tool (OpenAI Responses
    API ComparisonFilter/CompoundFilter - see
    developers.openai.com/api/docs/guides/retrieval#attribute-filtering)
    that scopes semantic search to the asking company: documents tagged
    company_id=<company_id> (this app's own Expert Notes - see
    push_document_to_vector_store), OR documents tagged shared=True (the
    pre-existing general reference library, meant to inform every
    company's answers - see backfill_vector_store_tenant_tags.py).

    Returns None (meaning "no filter", i.e. today's fully-open behavior)
    if company_id is unknown - every current caller can resolve one, but
    a caller that genuinely can't should never be made worse off by this
    change than it was before. Fixes Gate 3, Item 21 of the Duroflex
    pilot readiness list (cross-company semantic-search leak - see
    push_document_to_vector_store's docstring for the full history and
    the required one-time backfill step)."""
    if company_id is None:
        return None
    return {
        "type": "or",
        "filters": [
            {"type": "eq", "key": "company_id", "value": company_id},
            {"type": "eq", "key": "shared", "value": True},
        ],
    }


def ask_assistant(prompt, company_id=None, call_site="ask_assistant"):
    """Send a prompt to PI3 (file_search over the configured vector store,
    via the Responses API) and return (answer, interaction_log_id) - answer
    is the text response, or None (with an st.error already shown) on
    failure/timeout; interaction_log_id is the id of the PI3InteractionLog
    row this call was recorded under (see _record_pi3_interaction, Gate 6
    Items 49-51), or None if that logging itself failed. Callers that show
    a feedback control (Item 55 - see helpers.render_pi3_feedback_control)
    need this id to link a thumbs up/down back to the specific answer it's
    reacting to.

    SYSTEM_PROMPT (above) is passed as `instructions` on every call - it
    is the general PI3/PU ExpertCenter behavior. `prompt` is this app's
    own per-request question, which (per the callers in
    views/18_Root_Cause_Assistant.py)
    always restates PI3 Plant Edition's own advisory-boundary requirement
    (historical reference only, never an instruction) - see the module
    docstring for why that ordering matters and must not be dropped.

    `company_id` scopes file_search to this company's own tagged
    documents plus the shared general library - see
    _file_search_filters(). Every current caller (pages 15-19) already
    has this in scope as `active_company_id` from company_picker(); pass
    it through rather than defaulting to None, or this call goes back to
    searching every company's documents.

    `call_site` distinguishes which of the 5 fixed-prompt Intelligence
    sections is asking (pages 15-19 each pass their own label - see those
    callers) - added 2026-08-05 so the Performance admin page can break
    PI3 response time down by page instead of pooling every fixed-prompt
    call under one bucket. Defaults to the original "ask_assistant" label
    for any caller that doesn't pass one.
    """
    if not prompt or not prompt.strip():
        return None, None
    if not is_configured():
        return None, None
    start_time = time.monotonic()
    try:
        client = _client()
        vector_store_id = _get_secret("PI3_VECTOR_STORE_ID")
        model = _get_secret("PI3_MODEL") or DEFAULT_MODEL

        file_search_tool = {"type": "file_search", "vector_store_ids": [vector_store_id]}
        filters = _file_search_filters(company_id)
        if filters is not None:
            file_search_tool["filters"] = filters

        response = client.responses.create(
            model=model,
            instructions=SYSTEM_PROMPT,
            input=prompt,
            tools=[file_search_tool],
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        answer = response.output_text or None
        prompt_tokens, completion_tokens, total_tokens = _extract_token_usage(response)
        log_row = _record_pi3_interaction(
            call_site=call_site,
            question_text=prompt,
            response_text=answer,
            company_id=company_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            start_time=start_time,
            governance=_governance_fields(
                call_site=call_site,
                model=model,
                system_prompt=SYSTEM_PROMPT,
                input_text=prompt,
                response_id=getattr(response, "id", None),
                # Retrieval evidence for this path is which corpus was
                # searched: the vector store and the company filter applied
                # to it. That is what determines whether the answer could
                # have seen a given document.
                retrieval_evidence={
                    "tool": "file_search",
                    "vector_store_id": vector_store_id,
                    "filters": filters,
                },
            ),
        )
        return answer, (log_row.id if log_row is not None else None)
    except Exception as exc:
        _record_pi3_error(call_site, exc, company_id=company_id)
        st.error("Could not reach PI3 right now. Try again in a moment, or contact your administrator if this continues.")
        return None, None


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
# file_search stays available on both, unscoped by PLANT on purpose - see
# the project discussion: general expertise (customer questions, TDS
# documents, formulation science) isn't plant-specific and should inform
# every plant within the same company's answers. It IS now scoped by
# COMPANY (see _file_search_filters() below, fixed 2026-08-02) - a
# different paying customer's Expert Notes should never surface in this
# company's answers, which unscoped file_search allowed before this fix.
# This app's own plant/trial-specific pushes (Expert Notes) carry both a
# plant tag and a company tag (see push_document_to_vector_store's
# metadata parameter); the pre-existing general library carries a
# shared=True tag instead, so it keeps showing up for every company.

PLANT_QUERY_SYSTEM_PROMPT = """You are PI3, answering a technical reviewer's question about ONE specific plant's own production data at a flexible slabstock foam manufacturer.

Hard scope rule: every answer must stay within this one plant's data for tools 1 and 2 below - those results are already restricted to it regardless of what you ask for, so do not worry about accidentally overstepping with either of them, and never imply you checked "across plants" or "industry-wide" for anything that came from them. Tool 3 (file_search) is different - see its own note below - treat anything it returns as general context, not a plant-specific data point, unless you can tell from the content itself that it plainly concerns this plant.

You have three tools:

1. get_verified_analysis - use this FIRST whenever the question matches one of its analysis_type values: "trend" (control chart, process capability/Cpk, CUSUM drift, and a trend significance test for one property), "ingredient_correlation" (which raw material's actual metered dosage correlates with a property outcome), "recipe_cost" (formulation cost per recipe version), or "setting_correlation" (which machine/process setting correlates with a property outcome). These reuse the exact same tested calculations already shown on this app's Trend Analysis, Recipe Optimization, and Machine Settings vs Physical Properties Correlation pages - prefer this tool over writing your own SQL whenever a question fits one of these four shapes.

2. query_plant_data - a read-only SQL tool for anything the four analyses above don't cover. You may write a single SELECT statement against ONLY these views: v_pi3_production_runs, v_pi3_property_results, v_pi3_recipe_composition, v_pi3_stream_readings, v_pi3_quality_issues (columns are listed in the tool description). Your SELECT list must include plant_id (or use SELECT *) - it will be used to scope results. No other tables are reachable, and no INSERT/UPDATE/DELETE/DDL is possible - if you write one, the tool will reject it and tell you why so you can correct it.

3. file_search - the shared knowledge base (expert notes, historical troubleshooting cases, technical documents). Use this for context a number alone can't give: has this come up before, what did an expert conclude about a similar case, what does a technical document say. Unlike tools 1 and 2, this searches the FULL shared store across every plant that has contributed knowledge to it, not just this one - so treat what it returns as general expertise to round out an answer, never as confirmed proof of something happening at THIS plant unless the content is unambiguously about this plant. Never treat it as a substitute for checking the actual plant data first (tools 1/2) when the question is about this plant's own numbers.

Rules:
- Never state a number that did not come from a tool call. If no tool can answer part of the question, say so plainly instead of estimating or inferring a figure.
- Always be ready to show your work: assume the reviewer can see exactly which tool(s) you called and with what arguments, so do not hide your reasoning behind a confident-sounding number - state what you found and where it came from.
- Phrase everything as historical reference and observation for the reviewer's own investigation, never as an instruction to change a setting or formulation. This is a hard requirement, not a style preference.
- If a question is ambiguous about which foam grade, property, or date range it means, use whatever page/context information you were given to disambiguate; if it's still ambiguous, ask a brief clarifying question rather than guessing.
- Keep answers direct and reasonably concise. Lead with the answer, then the figures that support it.
- Never use statistical or technical jargon in your answer - not Cpk, Cpu, Cpl, CUSUM, p-value, R-squared, sigma, control limit, moving range, or similar terms, even though the tools you call use them internally. Translate every finding into plain operational language a foam-plant technician without a statistics background would understand. For example: instead of "Cpk 0.87", say the process is running close to the edge of spec; instead of a CUSUM breach, say a slow drift has been building up since a certain point; instead of a p-value, say plainly whether a pattern looks like a real, sustained trend or just normal run-to-run variation. Numbers themselves (dates, quantities, costs, percentages) are fine - it's the statistical vocabulary that should disappear, not the underlying facts."""

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
        "per-run dosage correlates with this property's outcome.\n"
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
        return _to_jsonable(
            {
                "actual_usage_correlation": actual.to_dict(orient="records") if not actual.empty else [],
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

    Returns (answer, tool_log, interaction_log_id) where tool_log is a list
    of dicts recording every tool call made (the exact SQL run, or the
    verified-analysis arguments) - callers should show this alongside the
    answer so a reviewer can check PI3's work rather than trust it
    blindly, per this feature's own design. interaction_log_id is the id
    of the PI3InteractionLog row this call was recorded under (see
    _record_pi3_interaction, Gate 6 Items 49-51) - callers that show a
    feedback control (Item 55) need this to link a thumbs up/down back to
    the specific answer. Returns (None, [], None) if PI3 isn't configured,
    the question is empty, or a call fails (an st.error is already shown
    in that case, same as ask_assistant())."""
    tool_log = []
    if not question or not question.strip():
        return None, tool_log, None
    if not is_configured():
        return None, tool_log, None

    vector_store_id = _get_secret("PI3_VECTOR_STORE_ID")
    model = _get_secret("PI3_MODEL") or DEFAULT_MODEL

    # Derive company_id from plant_id (Plant.company_id is a direct FK - see
    # db.py) so this function scopes file_search to the asking company
    # without requiring every caller to also pass company_id explicitly -
    # they already pass plant_id, which is enough. See _file_search_filters().
    plant = session.get(Plant, plant_id)
    company_id = plant.company_id if plant else None

    file_search_tool = {"type": "file_search", "vector_store_ids": [vector_store_id]}
    filters = _file_search_filters(company_id)
    if filters is not None:
        file_search_tool["filters"] = filters

    tools = [
        file_search_tool,
        _QUERY_PLANT_DATA_TOOL,
        _GET_VERIFIED_ANALYSIS_TOOL,
    ]
    input_text = question.strip()
    if page_context:
        input_text = f"Context: {page_context}\n\nQuestion: {question.strip()}"

    start_time = time.monotonic()
    prompt_tokens_sum = 0
    completion_tokens_sum = 0
    usage_seen = False

    def _accumulate_usage(resp):
        nonlocal prompt_tokens_sum, completion_tokens_sum, usage_seen
        pt, ct, _ = _extract_token_usage(resp)
        if pt is not None:
            prompt_tokens_sum += pt
            usage_seen = True
        if ct is not None:
            completion_tokens_sum += ct
            usage_seen = True

    try:
        client = _client()
        response = client.responses.create(
            model=model,
            instructions=PLANT_QUERY_SYSTEM_PROMPT,
            input=input_text,
            tools=tools,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        _accumulate_usage(response)
        # One question here can take several Responses API calls, chained by
        # previous_response_id. The chain - not just the last id - is what
        # makes the interaction reconstructable afterwards.
        response_chain = [getattr(response, "id", None)]

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
                            {
                                "tool": "query_plant_data",
                                "sql": executed_sql,
                                "rows_returned": len(rows),
                                # Full result rows, kept alongside the summary above so a
                                # report export (see reports.render_pi3_qa_report_docx) can
                                # show the actual data PI3 checked, not just row counts.
                                "rows": _to_jsonable(rows),
                            }
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
                    tool_log.append({"tool": "get_verified_analysis", "args": args, "result": result})
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
            _accumulate_usage(response)
            response_chain.append(getattr(response, "id", None))

        answer = response.output_text or None
        log_row = _record_pi3_interaction(
            call_site="ask_plant_question",
            question_text=input_text,
            response_text=answer,
            company_id=company_id,
            plant_id=plant_id,
            prompt_tokens=prompt_tokens_sum if usage_seen else None,
            completion_tokens=completion_tokens_sum if usage_seen else None,
            total_tokens=(prompt_tokens_sum + completion_tokens_sum) if usage_seen else None,
            start_time=start_time,
            governance=_governance_fields(
                call_site="ask_plant_question",
                model=model,
                system_prompt=PLANT_QUERY_SYSTEM_PROMPT,
                input_text=input_text,
                response_id=getattr(response, "id", None),
                response_chain=[r for r in response_chain if r],
                # Tool evidence is captured HERE, at answer time. It used to
                # survive only if the user happened to save the answer as an
                # Expert Note, so the evidence for every other question was
                # lost (CR section 12).
                tool_log=tool_log,
            ),
        )
        return answer, tool_log, (log_row.id if log_row is not None else None)
    except Exception as exc:
        _record_pi3_error("ask_plant_question", exc, company_id=company_id, plant_id=plant_id)
        st.error("Could not reach PI3 right now. Try again in a moment, or contact your administrator if this continues.")
        return None, tool_log, None


def extract_raw_material_from_tds(tds_text, sds_text=None):
    """Pull a structured raw-material record out of a technical data
    sheet's extracted text, for prefilling the Add Raw Material form (see
    views/14_Raw_Materials.py). An SDS's extracted text can optionally be
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
    except Exception:
        st.error("Could not extract raw material data from this document. Use Manual entry instead.")
        return None
