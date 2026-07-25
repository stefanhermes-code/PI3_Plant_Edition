"""PI3/AI Assistant integration (optional add-on).

Wraps the OpenAI Assistants API (threads/runs, file_search over a vector
store) behind three simple functions so pages don't need to know API
details: is_configured(), is_enabled_for_plant(), push_document_to_vector_store(),
and ask_assistant().

*** DEPRECATION WARNING - READ BEFORE TOUCHING THIS FILE ***
OpenAI is permanently shutting down the Assistants API (the assistants/
threads/runs endpoints this module calls) on 2026-08-26. This was a known,
explicit trade-off when this module was built (2026-07-25) - the user
already had an Assistant + vector store set up and chose to build against
it now rather than wait. Vector stores themselves are NOT affected by the
deprecation and can be reused; what needs replacing before the shutdown
date is everything in _ask_assistant_impl() (threads/runs) - migrate that
to client.responses.create(..., tools=[{"type": "file_search",
"vector_store_ids": [...]}]) using the same PI3_VECTOR_STORE_ID, with the
assistant's behavior/instructions moved into this file as a system prompt
string instead of a persisted OpenAI Assistant object.

Required secrets (see .streamlit/secrets.toml.example):
- OPENAI_API_KEY
- PI3_ASSISTANT_ID     (asst_... - created in the OpenAI dashboard/API,
  with the file_search tool enabled)
- PI3_VECTOR_STORE_ID  (vs_... - the vector store attached to that
  assistant; documents are pushed here as company knowledge is captured)

Everything here is optional and gated two ways: is_configured() checks the
three secrets above are present, and is_enabled_for_plant() additionally
checks the per-plant PI3AIConnectionSetting toggle (PI3/AI is a
separately billed, opt-in add-on - see the PI3/AI Connectivity admin
screen). Callers should check is_enabled_for_plant() before showing any
AI-powered UI at all. As a second line of defense, every OpenAI call below
is also wrapped in try/except so a transient API problem shows a friendly
st.error instead of crashing the page.
"""

import os
import time

import streamlit as st

from db import PI3AIConnectionSetting

# How long ask_assistant() will wait for a run to finish before giving up.
MAX_WAIT_SECONDS = 60
POLL_INTERVAL_SECONDS = 1.5


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
    """True once all three PI3/AI secrets are present. This does NOT mean
    any plant has actually turned the feature on - see
    is_enabled_for_plant() for the per-plant, separately-billed gate that
    every caller should check before doing anything AI-related."""
    return bool(
        _get_secret("OPENAI_API_KEY")
        and _get_secret("PI3_ASSISTANT_ID")
        and _get_secret("PI3_VECTOR_STORE_ID")
    )


def is_enabled_for_plant(session, plant_id):
    """True only when PI3/AI is both configured (secrets present) AND
    switched on for this specific plant on the PI3/AI Connectivity admin
    screen. PI3/AI is a separately billed, opt-in add-on - no OpenAI call
    should ever fire for a plant that hasn't enabled it, and no AI-powered
    UI should even be shown for one."""
    if plant_id is None or not is_configured():
        return False
    setting = (
        session.query(PI3AIConnectionSetting)
        .filter(PI3AIConnectionSetting.plant_id == plant_id)
        .first()
    )
    return bool(setting and setting.pi3_ai_connectivity_enabled)


def any_plant_enabled(session):
    """True when at least one plant has PI3/AI switched on (and secrets are
    configured). Used by cross-plant screens (Similar Case Retrieval spans
    every plant's history, not one specific plant) to decide whether to
    show any AI-powered option at all - the vector store itself already
    only contains knowledge pushed from plants that opted in, so there's
    nothing further to scope once at least one plant is enabled."""
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
    trial's narrative, ...) into the PI3/AI vector store so future
    Assistant queries can retrieve it semantically.

    Returns the new OpenAI file id (str) on success - callers that can
    store it (e.g. ExpertNote.vector_store_file_id) should, so a later
    edit/delete can resync or remove that exact file via
    delete_document_from_vector_store() instead of leaving a stale copy
    searchable forever. Returns None (with an st.error already shown) on
    failure or if PI3/AI isn't configured - safe to call unconditionally,
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
    (no-ops) or when PI3/AI isn't configured. Failures are logged as a
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
    """Send a prompt to the PI3/AI Assistant (file_search over the
    configured vector store) and return its text response, or None (with
    an st.error already shown) on failure/timeout.

    Uses manual thread/run polling with only the long-stable
    threads/messages/runs methods (create, retrieve, list) rather than any
    SDK convenience wrapper, since this is exactly the code that needs
    replacing before the Aug 2026 shutdown anyway - see the module
    docstring.
    """
    if not prompt or not prompt.strip():
        return None
    if not is_configured():
        return None
    try:
        client = _client()
        assistant_id = _get_secret("PI3_ASSISTANT_ID")

        thread = client.beta.threads.create()
        client.beta.threads.messages.create(thread_id=thread.id, role="user", content=prompt)
        run = client.beta.threads.runs.create(thread_id=thread.id, assistant_id=assistant_id)

        waited = 0.0
        while run.status in ("queued", "in_progress", "cancelling"):
            if waited >= MAX_WAIT_SECONDS:
                st.error("PI3 took too long to respond - try again.")
                return None
            time.sleep(POLL_INTERVAL_SECONDS)
            waited += POLL_INTERVAL_SECONDS
            run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)

        if run.status != "completed":
            st.error(f"PI3 did not complete (status: {run.status}).")
            return None

        messages = client.beta.threads.messages.list(thread_id=thread.id, order="desc", limit=1)
        if not messages.data:
            return None
        text_parts = [
            block.text.value
            for block in messages.data[0].content
            if getattr(block, "type", None) == "text"
        ]
        return "\n".join(text_parts) if text_parts else None
    except Exception as exc:
        st.error(f"Could not reach PI3: {exc}")
        return None
