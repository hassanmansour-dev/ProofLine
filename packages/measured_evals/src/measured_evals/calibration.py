"""Critic calibration: does a model critic agree with a person? (GROW-14)

A critic's score steers a pipeline (revise or ship), so it has to be checked against
the person it stands in for. The procedure, in order:

1. `build_sheet(outputs, critic)` makes a scoring sheet: one row per saved output
   with a **blank** `human_score`. The sheet is **blind** by default: it doesn't show
   the critic's scores, because a reviewer who sees a score first drifts towards it
   (anchoring), and the agreement would then measure the anchor.
2. A person reads each output (`sheet_markdown`) and types a score per row into
   the CSV (`sheet_csv`). Only the CSV is read back.
3. `agreement(critic, human, threshold=...)` compares the two.

**The metric is Cohen's kappa on pass/fail at the critic's threshold**, reported
with the four counts behind it. Why this one:

- The critic's number is only ever used as a decision: at or above the threshold the
  draft ships, below it the draft is revised. Agreement on that decision is the thing
  that matters; agreeing on 6 against 7 changes nothing.
- Raw agreement flatters a critic. If both raters pass most drafts, they agree often
  by chance alone. Kappa subtracts the agreement two raters with these pass rates
  would reach by chance: `(observed - expected) / (1 - expected)`.
- Correlation would hide a critic that is consistently two points generous: it would
  correlate perfectly and still ship drafts the person would send back.

Kappa says nothing about direction, so two plain numbers go with it: the mean
difference `critic - human` (positive: the critic is generous) and the mean absolute
difference. Kappa is undefined when both raters put every item on the same side
(nothing to tell apart); then it is reported as `None` with the reason, never as 0 or 1.

Limits, stated: with 20 items one changed decision moves raw agreement by 0.05 and
kappa by more, so read the counts first. One person's scores are a reference, not
ground truth. Nothing here calls a model, and nothing here invents a score: a blank
cell stops the computation.

Plain data in, plain data out: an output is a string and a score is a number, so
this module knows no app types (ADR-0012).
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

SHEET_COLUMNS: tuple[str, ...] = ("item_id", "words", "human_score", "notes")
CRITIC_COLUMN = "critic_score"
DEFAULT_SCALE: tuple[float, float] = (1.0, 10.0)

_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class CalibrationError(ValueError):
    """Bad calibration input. Messages name ids and counts; they never quote an output."""


@dataclass(frozen=True, slots=True)
class CriticScores:
    """One overall score per item id, on `scale`, from one critic.

    `synthetic` marks made-up scores (test fixtures, demos): every report built from
    them says so.
    """

    scores: Mapping[str, float]
    scale: tuple[float, float] = DEFAULT_SCALE
    source: str | None = None
    synthetic: bool = False


@dataclass(frozen=True, slots=True)
class SheetRow:
    item_id: str
    text: str
    words: int
    critic_score: float | None  # None on a blind sheet


@dataclass(frozen=True, slots=True)
class Sheet:
    rows: tuple[SheetRow, ...]
    scale: tuple[float, float]
    blind: bool
    synthetic: bool


@dataclass(frozen=True, slots=True)
class Agreement:
    """Critic against one person, on pass/fail at `threshold` (pass: score >= threshold)."""

    n: int
    threshold: float
    both_pass: int
    both_fail: int
    critic_only_pass: int
    human_only_pass: int
    observed_agreement: float
    expected_agreement: float
    kappa: float | None
    kappa_undefined_because: str | None
    mean_difference: float  # critic - human; positive: the critic is generous
    mean_absolute_difference: float
    synthetic: bool


def _number(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise CalibrationError(f"{what} must be a finite number")
    return float(value)


def _scale(value: object) -> tuple[float, float]:
    if not isinstance(value, list | tuple) or len(value) != 2:
        raise CalibrationError("scale must be [low, high]")
    low, high = _number(value[0], "scale low"), _number(value[1], "scale high")
    if low >= high:
        raise CalibrationError("scale low must be below scale high")
    return low, high


def _check_id(item_id: object) -> str:
    if not isinstance(item_id, str) or not _ID.match(item_id):
        raise CalibrationError("item ids must be lowercase slugs like 'loop-needs-exit'")
    return item_id


def parse_critic_scores(document: object) -> CriticScores:
    """Validate `{"scores": {id: number | {"overall": number}}, "scale"?, "source"?, "synthetic"?}`."""
    if not isinstance(document, Mapping) or not isinstance(document.get("scores"), Mapping):
        raise CalibrationError('critic scores need a top-level "scores" object')
    scale = _scale(document["scale"]) if "scale" in document else DEFAULT_SCALE
    scores: dict[str, float] = {}
    for item_id, raw in document["scores"].items():
        value = raw.get("overall") if isinstance(raw, Mapping) else raw
        score = _number(value, f"critic score for {_check_id(item_id)}")
        if not scale[0] <= score <= scale[1]:
            raise CalibrationError(f"critic score for {item_id} is outside the scale")
        scores[item_id] = score
    if not scores:
        raise CalibrationError("critic scores are empty")
    source = document.get("source")
    synthetic = document.get("synthetic", False)
    if source is not None and not isinstance(source, str):
        raise CalibrationError('"source" must be a string')
    if not isinstance(synthetic, bool):
        raise CalibrationError('"synthetic" must be true or false')
    return CriticScores(scores=scores, scale=scale, source=source, synthetic=synthetic)


def load_critic_scores(path: Path) -> CriticScores:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CalibrationError(f"{path.name} is not valid JSON") from exc
    return parse_critic_scores(document)


def read_outputs(directory: Path) -> dict[str, str]:
    """Saved outputs, one `<item-id>.md` per item (the layout `run --outputs` reads)."""
    outputs = {p.stem: p.read_text(encoding="utf-8") for p in sorted(directory.glob("*.md"))}
    if not outputs:
        raise CalibrationError(f"no <item-id>.md files in {directory.name}/")
    return outputs


def build_sheet(outputs: Mapping[str, str], critic: CriticScores, *, blind: bool = True) -> Sheet:
    """One row per output, ordered by id. Outputs and critic scores must cover the same ids."""
    for item_id in outputs:
        _check_id(item_id)
    missing = sorted(set(outputs) - set(critic.scores))
    extra = sorted(set(critic.scores) - set(outputs))
    if missing or extra:
        raise CalibrationError(
            f"outputs and critic scores differ: no critic score for {missing}, no output for {extra}"
        )
    empty = sorted(i for i, text in outputs.items() if not text.strip())
    if empty:
        raise CalibrationError(f"empty outputs: {empty}")
    rows = tuple(
        SheetRow(
            item_id=item_id,
            text=outputs[item_id].strip(),
            words=len(outputs[item_id].split()),
            critic_score=None if blind else critic.scores[item_id],
        )
        for item_id in sorted(outputs)
    )
    return Sheet(rows=rows, scale=critic.scale, blind=blind, synthetic=critic.synthetic)


def _fmt(value: float) -> str:
    return f"{value:g}"


def sheet_csv(sheet: Sheet) -> str:
    """The file the reviewer fills: `human_score` and `notes` are blank on every row."""
    columns = SHEET_COLUMNS if sheet.blind else (*SHEET_COLUMNS, CRITIC_COLUMN)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in sheet.rows:
        cells = [row.item_id, str(row.words), "", ""]
        if row.critic_score is not None:
            cells.append(_fmt(row.critic_score))
        writer.writerow(cells)
    return buffer.getvalue()


def sheet_markdown(sheet: Sheet) -> str:
    """The reading copy: every output in full, with a blank score line. Scores go in the CSV."""
    low, high = (_fmt(v) for v in sheet.scale)
    lines = ["# Critic calibration sheet", ""]
    if sheet.synthetic:
        lines += ["**SYNTHETIC: made-up critic scores. Nothing here measures a model.**", ""]
    lines += [
        f"{len(sheet.rows)} outputs. Score each from {low} to {high} with the same rubric the "
        "critic uses, before looking at any critic score. Type the scores into the CSV "
        "(`human_score`); only the CSV is read back.",
        "",
        "This sheet is blind: the critic's scores are left out so they can't pull yours."
        if sheet.blind
        else "This sheet shows the critic's scores. Scores given after seeing them are anchored; "
        "use the blind sheet for a number you can report.",
        "",
        "| # | Item | Words | Your score |" + ("" if sheet.blind else " Critic |"),
        "|---|---|---|---|" + ("" if sheet.blind else "---|"),
    ]
    for number, row in enumerate(sheet.rows, start=1):
        critic = "" if row.critic_score is None else f" {_fmt(row.critic_score)} |"
        lines.append(f"| {number} | `{row.item_id}` | {row.words} | |{critic}")
    for number, row in enumerate(sheet.rows, start=1):
        lines += ["", f"## {number}. `{row.item_id}`", ""]
        lines += [f"> {line}" if line else ">" for line in row.text.splitlines()]
        lines += ["", f"Your score ({low} to {high}): ____", "", "Notes:"]
    return "\n".join(lines) + "\n"


def parse_filled_sheet(
    text: str, *, scale: tuple[float, float] = DEFAULT_SCALE
) -> dict[str, float]:
    """Read the human scores back. A blank or out-of-scale cell is an error, never a guess."""
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or not {"item_id", "human_score"} <= set(reader.fieldnames):
        raise CalibrationError("the sheet needs item_id and human_score columns")
    human: dict[str, float] = {}
    blank: list[str] = []
    for record in reader:
        item_id = _check_id((record.get("item_id") or "").strip())
        if item_id in human or item_id in blank:
            raise CalibrationError(f"{item_id} appears twice in the sheet")
        cell = (record.get("human_score") or "").strip()
        if not cell:
            blank.append(item_id)
            continue
        try:
            score = float(cell)
        except ValueError as exc:
            raise CalibrationError(f"human_score for {item_id} is not a number") from exc
        if not math.isfinite(score) or not scale[0] <= score <= scale[1]:
            raise CalibrationError(f"human_score for {item_id} is outside the scale")
        human[item_id] = score
    if blank:
        raise CalibrationError(
            f"{len(blank)} of {len(blank) + len(human)} rows have no human_score yet: {blank}"
        )
    if not human:
        raise CalibrationError("the sheet has no rows")
    return human


def agreement(critic: CriticScores, human: Mapping[str, float], *, threshold: float) -> Agreement:
    """Cohen's kappa on pass/fail at `threshold`, with its counts and the score differences."""
    if set(critic.scores) != set(human):
        missing = sorted(set(critic.scores) - set(human))
        extra = sorted(set(human) - set(critic.scores))
        raise CalibrationError(
            f"sheet and critic scores differ: not in the sheet {missing}, no critic score {extra}"
        )
    if not critic.scale[0] <= threshold <= critic.scale[1]:
        raise CalibrationError("threshold is outside the scale")
    n = len(human)
    pairs = [(critic.scores[i] >= threshold, human[i] >= threshold) for i in sorted(human)]
    both_pass = sum(1 for c, h in pairs if c and h)
    both_fail = sum(1 for c, h in pairs if not c and not h)
    critic_only = sum(1 for c, h in pairs if c and not h)
    human_only = sum(1 for c, h in pairs if h and not c)
    observed = (both_pass + both_fail) / n
    critic_rate = (both_pass + critic_only) / n
    human_rate = (both_pass + human_only) / n
    expected = critic_rate * human_rate + (1 - critic_rate) * (1 - human_rate)
    kappa: float | None
    reason: str | None
    if math.isclose(expected, 1.0):
        kappa, reason = None, "both raters put every item on the same side of the threshold"
    else:
        kappa, reason = (observed - expected) / (1 - expected), None
    differences = [critic.scores[i] - human[i] for i in human]
    return Agreement(
        n=n,
        threshold=threshold,
        both_pass=both_pass,
        both_fail=both_fail,
        critic_only_pass=critic_only,
        human_only_pass=human_only,
        observed_agreement=observed,
        expected_agreement=expected,
        kappa=kappa,
        kappa_undefined_because=reason,
        mean_difference=sum(differences) / n,
        mean_absolute_difference=sum(abs(d) for d in differences) / n,
        synthetic=critic.synthetic,
    )


def agreement_lines(result: Agreement) -> list[str]:
    """A plain-text report: what was counted, the metric, and what to do about it."""
    kappa = (
        f"{result.kappa:.2f}"
        if result.kappa is not None
        else f"undefined ({result.kappa_undefined_because})"
    )
    lines = []
    if result.synthetic:
        lines.append("SYNTHETIC critic scores: this result measures nothing.")
    lines += [
        f"critic vs person · {result.n} items · pass means score >= {_fmt(result.threshold)}",
        f"  both pass: {result.both_pass} · both fail: {result.both_fail}",
        f"  critic passes, person fails: {result.critic_only_pass} (would ship unreviewed work)",
        f"  person passes, critic fails: {result.human_only_pass} (wasted revisions)",
        f"  observed agreement: {result.observed_agreement:.2f} · "
        f"expected by chance: {result.expected_agreement:.2f}",
        f"  Cohen's kappa: {kappa}",
        f"  mean difference (critic - person): {result.mean_difference:+.2f} · "
        f"mean absolute difference: {result.mean_absolute_difference:.2f}",
        "to improve: read the disagreeing items first; tighten the rubric anchor they share, "
        "bump the critic prompt version, score a fresh sheet.",
    ]
    return lines
