"""Check modules carried across from the pre-CR working scripts.

These modules are not importable on their own. Each is replayed exactly once,
by the pytest wrapper that owns it, through ``tests._recorder.replay``.
See ``tests/README.md`` for why they exist and when they go away.
"""
