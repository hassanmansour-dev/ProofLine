"""The approval inbox (C4, ADR-0017): a phone-sized web page over runs paused at the
supervisor's gate. Approve or reject; the thread resumes from its checkpoint.

`service` has no web code (list, decide). `web` is the FastAPI app, which needs the
`inbox` extra (in this workspace: `uv sync --all-packages --extra inbox`). Run it with
`python -m measured_ops.inbox` (docs/INBOX.md).
"""
