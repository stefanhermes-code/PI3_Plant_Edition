"""Immutable storage of the original regulatory source files (REACH R-A1).

Charlie's decision 2 of the architecture freeze: the official ECHA and CLP
files are kept in Supabase Storage, not inline in the database.

WHY NOT IN THE DATABASE, WHEN EVERY OTHER DOCUMENT IS

A supplier safety data sheet is a few hundred kilobytes and is read back
constantly, so holding its bytes in the row it belongs to is the simple
answer. A regulatory source file is different on both counts. Annex VI to CLP
is megabytes, every row of it is already parsed into
regulatory_reference_records, and the original is retained for PROVENANCE - to
prove what was loaded - rather than to be read. Putting it in the row would
make every query over the reference sets carry it.

WHY THE STANDARD LIBRARY AND NOT requests

requirements.txt pins exact versions and says, at length, that adding a
dependency is a deliberate act. Supabase Storage is a plain REST API over
HTTPS; urllib does it. Reaching for requests here would add an unpinned
transitive dependency to save nothing.

CONFIGURATION

SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY, read the same way the rest of the
application reads its settings: st.secrets first, then the environment. The
service role key is required because the bucket is private - an anon key
cannot write to it, and it must never be exposed to a browser.

When storage is not configured, is_configured() returns False and the loader
records the dataset with no original attached rather than failing. That is a
deliberate choice: the parsed records and their provenance are the load-bearing
part, and refusing to load a valid official file because a bucket is not
wired up yet would block the library for a reason that has nothing to do with
the data. The set says plainly that no original is retained, and
reference_state() reports it.
"""

import hashlib
import json
import os
import urllib.error
import urllib.request

BUCKET = "regulatory-sources"
BACKEND_SUPABASE = "supabase-storage"
BACKEND_NONE = "none"

# Supabase Storage rejects an upload to an existing key unless upsert is asked
# for. We never upsert: an object key is derived from the SHA-256 of the
# bytes, so the same key always means the same file, and overwriting it could
# only ever replace a file with itself or corrupt provenance.
_UPSERT = "false"

MAX_ORIGINAL_BYTES = 64 * 1024 * 1024


class StorageError(RuntimeError):
    """Storage was configured and the operation still failed."""


class StorageNotConfigured(RuntimeError):
    """SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not set."""


def _setting(name):
    try:
        import streamlit as st
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.environ.get(name)


def is_configured():
    return bool(_setting("SUPABASE_URL") and _setting("SUPABASE_SERVICE_ROLE_KEY"))


def _base_and_key():
    base = (_setting("SUPABASE_URL") or "").rstrip("/")
    key = _setting("SUPABASE_SERVICE_ROLE_KEY")
    if not base or not key:
        raise StorageNotConfigured(
            "Supabase Storage is not configured. Set SUPABASE_URL and "
            "SUPABASE_SERVICE_ROLE_KEY to retain the original source files."
        )
    return base, key


def object_key(dataset_slot, file_hash, original_file_name):
    """Content-addressed, and readable by a human looking at the bucket.

    The hash is the identity: the same bytes always land on the same key, so a
    re-upload is a no-op rather than a second copy under a different name. The
    slot and the original file name are there so somebody browsing the bucket
    can tell what they are looking at without opening it."""
    safe = "".join(c if (c.isalnum() or c in "._-") else "_"
                   for c in (original_file_name or "source"))[:120]
    return "%s/%s/%s" % (dataset_slot, file_hash, safe)


def _request(method, url, key, data=None, headers=None, transport=None):
    """One HTTP call. transport is injectable so the tests can exercise the
    key derivation, the headers and the error mapping without credentials and
    without reaching the network."""
    hdrs = {"Authorization": "Bearer %s" % key}
    hdrs.update(headers or {})
    if transport is not None:
        return transport(method=method, url=url, headers=hdrs, data=data)
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        raise StorageError("Supabase Storage could not be reached: %s" % exc.reason)


def put_original(dataset_slot, raw_bytes, original_file_name, *,
                 content_type="application/octet-stream", transport=None):
    """Upload one original source file. Returns the stored object's details.

    Raises StorageNotConfigured when there is nowhere to put it - the caller
    decides whether that is fatal."""
    if not raw_bytes:
        raise StorageError("There are no bytes to store.")
    if len(raw_bytes) > MAX_ORIGINAL_BYTES:
        raise StorageError(
            "The source file is %.1f MB, above the %d MB limit for a retained original."
            % (len(raw_bytes) / (1024 * 1024), MAX_ORIGINAL_BYTES // (1024 * 1024))
        )

    base, key = _base_and_key()
    file_hash = hashlib.sha256(raw_bytes).hexdigest()
    obj = object_key(dataset_slot, file_hash, original_file_name)
    url = "%s/storage/v1/object/%s/%s" % (base, BUCKET, obj)

    status, body = _request(
        "POST", url, key, data=raw_bytes,
        headers={"Content-Type": content_type, "x-upsert": _UPSERT},
        transport=transport,
    )

    if status in (200, 201):
        pass
    elif status == 409:
        # The same bytes are already stored under the same key. Nothing to do,
        # and nothing wrong: content-addressing makes this the expected result
        # of loading a file that was loaded before.
        pass
    else:
        raise StorageError(
            "Supabase Storage refused the upload (HTTP %s): %s"
            % (status, _message(body))
        )

    return {
        "storage_backend": BACKEND_SUPABASE,
        "storage_bucket": BUCKET,
        "storage_object_key": obj,
        "file_hash": file_hash,
        "file_size": len(raw_bytes),
    }


def get_original(bucket, object_key_, transport=None):
    """Read a stored original back, for proving what was loaded."""
    base, key = _base_and_key()
    url = "%s/storage/v1/object/%s/%s" % (base, bucket, object_key_)
    status, body = _request("GET", url, key, transport=transport)
    if status == 200:
        return body
    if status == 404:
        raise StorageError(
            "The stored original is missing from the bucket: %s/%s" % (bucket, object_key_)
        )
    raise StorageError(
        "Supabase Storage refused the download (HTTP %s): %s" % (status, _message(body))
    )


def _message(body):
    """Supabase returns JSON errors; fall back to raw text when it does not."""
    if not body:
        return "(no detail)"
    try:
        text = body.decode("utf-8", "replace")
    except Exception:
        return "(undecodable response)"
    try:
        parsed = json.loads(text)
    except ValueError:
        return text[:300]
    for field in ("message", "error", "msg"):
        if isinstance(parsed, dict) and parsed.get(field):
            return str(parsed[field])[:300]
    return text[:300]
