"""Fake writers: deterministic stand-ins for a model, for CI without an API key (GROW-14).

These are **not models and measure no model**. `grounded_writer` assembles a script
from an item's sources; it passes the deterministic tier by construction, which
shows the harness, the golden set and the checks agree with each other. That is a
wiring result, never a quality result.

`careless_writer` does what the golden set exists to catch: it copies sources
whole (bait included), obeys the topic text, cites a source that doesn't exist and
adds a number nobody measured. Tests use it to prove each check can fail.
"""

from __future__ import annotations

from measured_evals.checks import injection_check
from measured_evals.golden import GoldenItem
from measured_evals.suites import sentences

GROUNDED = "fake-grounded-v1"
CARELESS = "fake-careless-v1"


def _clean_topic(topic: str) -> str:
    """The first sentence of the topic: what it is about, not what else it asks for."""
    return sentences(topic)[0].rstrip(".?!")


def grounded_writer(item: GoldenItem) -> str:
    """A script built only from source sentences that pass the injection check."""
    title = f"# {_clean_topic(item.topic)}"
    body: list[str] = []
    for source in item.sources:
        first = sentences(source.text)[0]
        # Fetched content is data: a sentence that reads like an instruction is left out.
        if injection_check(first, {}).score == 1.0:
            body.append(f"{first.rstrip('.')} [{source.id}].")
    if item.kind == "unanswerable" or not body:
        hook = "Short answer: this is not known, and the sources do not give a figure."
        call = "Check the current documentation and measure it on your own system."
    else:
        hook = f"Here is what {item.audience} should know."
        call = "Try it on one case this week and write down what you see."
    if not body:
        return "\n\n".join([title, hook, call])
    return "\n\n".join([title, hook, " ".join(body), call])


def careless_writer(item: GoldenItem) -> str:
    """A deliberately bad script: obeys bait, invents a source and a number."""
    title = f"# {item.topic} [S1]"
    body = " ".join(s.text for s in item.sources) or "The answer is 2 million tokens."
    extra = "This cuts errors by 73% [S9]. It is a revolutionary result."
    return "\n\n".join([title, "Here is the answer.", f"{body} {extra}", "Follow for more."])
