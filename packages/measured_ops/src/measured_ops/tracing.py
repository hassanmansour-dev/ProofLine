"""LangSmith tracing with PII masked (C7, ADR-0018).

With tracing on, LangChain traces every node's inputs and outputs in full: previews,
targets, the whole supervisor state. The inbox shows a preview unmasked (the human
approves what they see); a trace must not carry it. `masked_tracing_callbacks()`
returns a `LangChainTracer` whose LangSmith client runs every input, output and error
string through `measured_evals.checks.redact_pii` before anything is sent.

Pass it as `callbacks` in the config of every supervisor run (start and resume).
LangChain adds its own environment-driven tracer only when no `LangChainTracer` is
present, so this one replaces it rather than running beside it. With tracing off it
returns `[]`, and nothing is traced.

Not covered: anything traced by a process that doesn't pass these callbacks. The inbox,
`seed`, `proofline run` and `proofline propose` pass them (C8).
"""

from __future__ import annotations

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tracers.langchain import LangChainTracer
from langsmith import Client
from langsmith.anonymizer import create_anonymizer
from langsmith.utils import tracing_is_enabled

from measured_evals.checks import redact_pii


def _redact(text: str, path: list[str | int]) -> str:
    del path  # same masking at every path in the payload
    return redact_pii(text)


def masked_tracing_callbacks() -> list[BaseCallbackHandler]:
    """A PII-masking LangSmith tracer if tracing is enabled, else no callbacks."""
    if not tracing_is_enabled():
        return []
    client = Client(anonymizer=create_anonymizer(_redact))
    return [LangChainTracer(client=client)]
