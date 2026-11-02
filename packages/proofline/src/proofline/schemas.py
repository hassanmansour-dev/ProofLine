"""Pydantic models for every LLM output, every value stored in graph state, and API payloads.

Field descriptions are sent to the model with structured output — write them as
instructions. Derived numbers (word counts, durations, overall score) are computed
here, never requested from the model (ADR-0002, ADR-0008). They are plain
properties, not computed fields, so dumps round-trip under extra="forbid".
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ModelRole = Literal["planner", "researcher", "writer", "critic"]

# One output format (ADR-0012): a short script for a LinkedIn post or a 1-minute video.
MAX_WORDS = 200
WORDS_PER_MINUTE = 150

SOURCE_ID_RE = re.compile(r"^S\d+$")
# An inline citation: [S3] or, if the model groups them, [S2, S5].
CITATION_RE = re.compile(r"\[(S\d+(?:\s*,\s*S\d+)*)\]")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ── Run configuration (snapshotted into state at run start, ADR-0004) ─────────


class ModelSpec(_Model):
    model: str
    temperature: float = Field(ge=0.0, le=1.0)
    max_tokens: int = Field(default=4096, gt=0)


class RunConfig(_Model):
    score_threshold: float = Field(default=8.0, ge=1.0, le=10.0)
    max_revisions: int = Field(default=3, ge=0, le=10)
    max_sources: int = Field(default=8, ge=1, le=30)
    results_per_query: int = Field(default=5, ge=1, le=20)
    max_source_chars: int = Field(default=4000, ge=500)
    max_words: int = Field(default=MAX_WORDS, ge=50, le=1000)
    brand_name: str = Field(default="Build Clearly", min_length=1, max_length=80)
    models: dict[ModelRole, ModelSpec]


# ── Planner ───────────────────────────────────────────────────────────────────


class OutlineSection(_Model):
    title: str = Field(description="Beat title, 2-6 words.")
    goal: str = Field(
        description="What the reader or viewer should understand after this beat, one sentence."
    )


class Outline(_Model):
    angle: str = Field(
        description="The specific take on the topic for this audience, one sentence. Not a restatement of the topic."
    )
    key_questions: list[str] = Field(
        min_length=2,
        max_length=4,
        description="Questions the script must answer, answerable from sources.",
    )
    search_queries: list[str] = Field(
        min_length=3,
        max_length=5,
        description="Web search queries (3-8 words each) that would find primary sources: official docs, changelogs, papers, maintainer posts.",
    )
    sections: list[OutlineSection] = Field(
        min_length=2, max_length=4, description="The beats of the script body, in order."
    )


# ── Researcher ────────────────────────────────────────────────────────────────


class Source(_Model):
    id: str = Field(pattern=SOURCE_ID_RE.pattern)
    url: str
    title: str
    content: str
    score: float | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Finding(_Model):
    claim: str = Field(description="One factual claim stated plainly, as a full sentence.")
    source_ids: list[str] = Field(
        min_length=1,
        description="Ids of the <source> blocks that directly support the claim, e.g. ['S2'].",
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description="high = stated directly by an authoritative source; medium = implied or single secondary source; low = conflicting or dated."
    )


class ExtractedFindings(_Model):
    """What the researcher LLM returns. Sources are attached in code, not by the model."""

    findings: list[Finding] = Field(max_length=25)
    gaps: list[str] = Field(
        default_factory=list, description="Key questions the sources do not answer."
    )


class ResearchNotes(_Model):
    sources: list[Source]
    findings: list[Finding]
    gaps: list[str] = Field(default_factory=list)

    @property
    def source_ids(self) -> set[str]:
        return {s.id for s in self.sources}


# ── Writer ────────────────────────────────────────────────────────────────────


class Draft(_Model):
    title: str = Field(description="Title, under 70 characters, specific, no clickbait.")
    hook: str = Field(description="First 1-2 sentences. Must create a reason to keep reading.")
    body: str = Field(
        description="The main content, following the outline's beats. Short paragraphs separated by blank lines. Written for the ear. Cite sources inline as [S1]; several as [S1][S4]."
    )
    call_to_action: str = Field(description="Closing line: one specific next step for the reader.")

    @property
    def spoken_text(self) -> str:
        return "\n\n".join([self.hook, self.body, self.call_to_action])

    @property
    def cited_source_ids(self) -> list[str]:
        """Source ids cited inline in the spoken text, in first-seen order (ADR-0011).

        Parsed from the narration, never reported by the model, so it can't disagree with it.
        """
        ids = (
            i.strip() for m in CITATION_RE.finditer(self.spoken_text) for i in m.group(1).split(",")
        )
        return list(dict.fromkeys(ids))

    @property
    def word_count(self) -> int:
        # drop the space before a citation too, so "step [S1]." doesn't leave a "." word
        text = re.sub(r"\s*" + CITATION_RE.pattern, "", self.spoken_text)
        return len(text.split())

    @property
    def estimated_seconds(self) -> int:
        return round(self.word_count / WORDS_PER_MINUTE * 60)


# ── Critic ────────────────────────────────────────────────────────────────────

Criterion = Literal[
    "hook",
    "clarity",
    "technical_accuracy",
    "structure",
    "audience_fit",
    "actionability",
    "voice",
    "length_fit",
]

CRITERION_WEIGHTS: dict[Criterion, float] = {
    "hook": 1.5,
    "clarity": 1.5,
    "technical_accuracy": 2.0,
    "structure": 1.0,
    "audience_fit": 1.0,
    "actionability": 1.0,
    "voice": 1.0,
    "length_fit": 1.0,
}
ACCURACY_GATE = 6
ACCURACY_GATE_CAP = 6.0


class CriterionScore(_Model):
    criterion: Criterion
    score: int = Field(ge=1, le=10, description="Integer 1-10 using the rubric anchors.")
    evidence: str = Field(description="A verbatim quote from the draft that justifies the score.")
    fix: str = Field(
        description="The concrete change that would raise this score. Empty if score is 10."
    )


class Critique(_Model):
    scores: list[CriterionScore] = Field(
        min_length=len(CRITERION_WEIGHTS), max_length=len(CRITERION_WEIGHTS)
    )
    must_fix: list[str] = Field(
        max_length=5,
        description="Up to 5 fixes ranked by impact. Each: criterion — “verbatim quote from the draft” — the change.",
    )
    unsupported_claims: list[str] = Field(
        default_factory=list,
        description="Verbatim quotes of draft claims that have no matching finding, cite the wrong source id, or state more than the finding supports.",
    )

    @model_validator(mode="after")
    def _one_score_per_criterion(self) -> Critique:
        """overall and score_for need every criterion exactly once (ADR-0011).

        Failing here, inside validation, lets the LLM adapter's repair retry fix it.
        """
        seen = [s.criterion for s in self.scores]
        missing = sorted(set(CRITERION_WEIGHTS) - set(seen))
        repeated = sorted({c for c in seen if seen.count(c) > 1})
        if missing or repeated:
            raise ValueError(
                f"score each criterion exactly once; missing={missing} repeated={repeated}"
            )
        return self

    def score_for(self, criterion: Criterion) -> int:
        for s in self.scores:
            if s.criterion == criterion:
                return s.score
        raise KeyError(criterion)

    @property
    def overall(self) -> float:
        """Weighted mean, 1 dp; capped when technical accuracy fails the gate (ADR-0008)."""
        total_weight = sum(CRITERION_WEIGHTS.values())
        weighted = sum(CRITERION_WEIGHTS[s.criterion] * s.score for s in self.scores)
        value = round(weighted / total_weight, 1)
        if self.score_for("technical_accuracy") < ACCURACY_GATE:
            value = min(value, ACCURACY_GATE_CAP)
        return value


# ── Human review / history / final output ─────────────────────────────────────


class HumanDecision(_Model):
    approved: bool
    feedback: str | None = Field(default=None, max_length=4000)


class RevisionRecord(_Model):
    kind: Literal["draft", "critique", "human"]
    revision: int
    score: float | None = None
    note: str = ""
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FinalScript(_Model):
    thread_id: str
    title: str
    markdown: str
    word_count: int
    estimated_seconds: int
    revision_count: int
    final_score: float | None = Field(
        default=None, description="Critic's overall score; None when no critic ran (Week 1)."
    )
    source_urls: list[str]
    prompt_versions: dict[str, str]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ── API payloads ──────────────────────────────────────────────────────────────


class StartRunRequest(_Model):
    topic: str = Field(min_length=3, max_length=300)
    audience: str = Field(default="developers new to AI", min_length=3, max_length=300)


class RunView(_Model):
    thread_id: str
    status: Literal["awaiting_review", "completed", "running"]
    revision_count: int = 0
    score: float | None = None
    draft: Draft | None = None
    critique: Critique | None = None
    final_script: FinalScript | None = None


# Every Pydantic model that can appear in ScriptState — the checkpointer's serde
# allowlist is built from this tuple (ADR-0006). tests/test_checkpointing.py checks it.
STATE_MODELS: tuple[type[BaseModel], ...] = (
    RunConfig,
    ModelSpec,
    Outline,
    OutlineSection,
    ResearchNotes,
    Source,
    Finding,
    Draft,
    Critique,
    CriterionScore,
    RevisionRecord,
    FinalScript,
)
