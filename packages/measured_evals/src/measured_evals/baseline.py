"""Regression baselines: a recorded eval result to compare later runs against (GROW-14).

A baseline is a JSON file. Every number in it is an entry with the same fields as a
reality input (`value`, `status`, `source`, `how_to_get_it`, `used_in`):

- `status: "unmeasured"`: nobody ran it; the value is a placeholder;
- `status: "needs_eval"`: a value exists (a placeholder or a run) but the owner has
  not approved it;
- `status: "real"`: the owner approved a real run; `source` says which.

**A baseline gates nothing until it is real.** `compare()` only reports regressions
as enforced when the file's own `status` is `"real"` and every metric it compares
is `"real"`; otherwise it returns the same differences marked `enforced=False`.
A placeholder can therefore never fail or pass a build as if it were a measurement.

Metrics (all in [0, 1]):
- `deterministic_pass_rate`: share of items whose deterministic checks all passed;
- `judge_mean`: mean model-graded score (model-graded tier only; noisy, so it has
  a tolerance).
Per item: `deterministic_pass` (bool).
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from measured_evals.suites import SuiteReport

SCHEMA_VERSION = 1
STATUSES: tuple[str, ...] = ("unmeasured", "needs_eval", "real")
METRICS: tuple[str, ...] = ("deterministic_pass_rate", "judge_mean")
_ENTRY_KEYS = {"value", "status", "source", "how_to_get_it", "used_in"}
_TOP_KEYS = {
    "schema_version",
    "golden_set",
    "status",
    "approved_by",
    "produced_by",
    "tolerance",
    "metrics",
    "items",
}


class BaselineError(ValueError):
    """A baseline file that doesn't match the format."""


@dataclass(frozen=True, slots=True)
class Entry:
    """One recorded value and whether it is real."""

    value: float | bool | None
    status: str
    source: str | None = None
    how_to_get_it: str = ""
    used_in: tuple[str, ...] = ()

    @property
    def real(self) -> bool:
        return self.status == "real"


@dataclass(frozen=True, slots=True)
class Baseline:
    golden_set: str
    status: str
    approved_by: str | None
    produced_by: Mapping[str, object]
    tolerance: Mapping[str, float]
    metrics: Mapping[str, Entry]
    items: Mapping[str, Entry]

    @property
    def real(self) -> bool:
        return self.status == "real"

    def placeholders(self) -> tuple[str, ...]:
        """Ids of every entry that is not real, for the make-it-real list."""
        out = [f"metrics.{k}" for k, e in self.metrics.items() if not e.real]
        out += [f"items.{k}" for k, e in self.items.items() if not e.real]
        return tuple(out)


@dataclass(frozen=True, slots=True)
class Regression:
    what: str  # "metrics.deterministic_pass_rate" or "items.<id>"
    baseline: float | bool
    current: float | bool


@dataclass(frozen=True, slots=True)
class Comparison:
    """`enforced` is False when the baseline (or a compared value) is not real: the
    differences are information, not a verdict."""

    enforced: bool
    regressions: tuple[Regression, ...]
    not_compared: tuple[str, ...]

    @property
    def failed(self) -> bool:
        return self.enforced and bool(self.regressions)


def _entry(raw: object, where: str, *, boolean: bool) -> Entry:
    if not isinstance(raw, dict) or set(raw) - _ENTRY_KEYS or "status" not in raw:
        raise BaselineError(f"{where}: must be an entry with keys {sorted(_ENTRY_KEYS)}")
    status = raw["status"]
    if status not in STATUSES:
        raise BaselineError(f"{where}: status must be one of {list(STATUSES)}")
    value = raw.get("value")
    if value is not None:
        if boolean and not isinstance(value, bool):
            raise BaselineError(f"{where}: value must be true, false or null")
        if not boolean and (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or math.isnan(value)
            or not 0.0 <= value <= 1.0
        ):
            raise BaselineError(f"{where}: value must be a number in [0, 1] or null")
    source = raw.get("source")
    if status == "real" and (value is None or not isinstance(source, str) or not source.strip()):
        raise BaselineError(f"{where}: a real entry needs a value and a source")
    if status == "unmeasured" and source is not None:
        raise BaselineError(f"{where}: an unmeasured entry has no source")
    used_in = raw.get("used_in", [])
    if not isinstance(used_in, list) or not all(isinstance(u, str) for u in used_in):
        raise BaselineError(f"{where}: used_in must be a list of strings")
    how = raw.get("how_to_get_it", "")
    if not isinstance(how, str):
        raise BaselineError(f"{where}: how_to_get_it must be text")
    return Entry(value, status, source if isinstance(source, str) else None, how, tuple(used_in))


def parse_baseline(data: Mapping[str, object]) -> Baseline:
    if set(data) - _TOP_KEYS:
        raise BaselineError(f"unknown keys {sorted(set(data) - _TOP_KEYS)}")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise BaselineError(f"schema_version must be {SCHEMA_VERSION}")
    golden_set, status = data.get("golden_set"), data.get("status")
    if not isinstance(golden_set, str) or not golden_set:
        raise BaselineError("golden_set must name the set")
    if status not in STATUSES:
        raise BaselineError(f"status must be one of {list(STATUSES)}")
    approved_by = data.get("approved_by")
    if approved_by is not None and (not isinstance(approved_by, str) or not approved_by.strip()):
        raise BaselineError("approved_by must be text or null")
    raw_metrics, raw_items = data.get("metrics", {}), data.get("items", {})
    if not isinstance(raw_metrics, dict) or set(raw_metrics) - set(METRICS):
        raise BaselineError(f"metrics must be a table with keys from {list(METRICS)}")
    if not isinstance(raw_items, dict):
        raise BaselineError("items must be a table")
    metrics = {k: _entry(v, f"metrics.{k}", boolean=False) for k, v in raw_metrics.items()}
    items = {k: _entry(v, f"items.{k}", boolean=True) for k, v in raw_items.items()}
    if status == "real":
        if approved_by is None:
            raise BaselineError("a real baseline needs approved_by")
        unreal = [k for k, e in {**metrics, **items}.items() if not e.real]
        if unreal:
            raise BaselineError(f"a real baseline can't hold placeholder entries: {unreal[:3]}")
    elif approved_by is not None:
        raise BaselineError("approved_by is only for a real baseline")
    raw_tolerance = data.get("tolerance", {})
    if not isinstance(raw_tolerance, dict) or set(raw_tolerance) - set(METRICS):
        raise BaselineError(f"tolerance must be a table with keys from {list(METRICS)}")
    tolerance: dict[str, float] = {}
    for key, value in raw_tolerance.items():
        if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 1:
            raise BaselineError(f"tolerance.{key} must be a number in [0, 1]")
        tolerance[key] = float(value)
    produced_by = data.get("produced_by", {})
    if not isinstance(produced_by, dict):
        raise BaselineError("produced_by must be a table")
    return Baseline(golden_set, str(status), approved_by, produced_by, tolerance, metrics, items)


def load_baseline(path: str | Path) -> Baseline:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BaselineError(f"not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise BaselineError("a baseline is a JSON object")
    return parse_baseline(data)


def compare(
    baseline: Baseline, report: SuiteReport, *, judge_mean: float | None = None
) -> Comparison:
    """Differences where the current run is worse than the baseline.

    A metric regresses when it drops by more than its tolerance; an item regresses
    when it passed in the baseline and fails now. Entries without a value, and
    items missing on either side, are listed in `not_compared`.
    """
    if baseline.golden_set != report.golden_set:
        raise BaselineError(
            f"baseline is for {baseline.golden_set!r}, the run is for {report.golden_set!r}"
        )
    regressions: list[Regression] = []
    not_compared: list[str] = []
    all_real = baseline.real
    current: dict[str, float | None] = {
        "deterministic_pass_rate": report.deterministic_pass_rate,
        "judge_mean": judge_mean,
    }
    for name in METRICS:
        entry, now = baseline.metrics.get(name), current[name]
        if entry is None or entry.value is None or now is None:
            not_compared.append(f"metrics.{name}")
            continue
        all_real = all_real and entry.real
        if now < float(entry.value) - baseline.tolerance.get(name, 0.0):
            regressions.append(Regression(f"metrics.{name}", float(entry.value), now))
    seen = {i.item_id: i.deterministic_passed for i in report.items}
    for item_id in sorted(set(seen) | set(baseline.items)):
        item_entry = baseline.items.get(item_id)
        if item_entry is None or item_entry.value is None or item_id not in seen:
            not_compared.append(f"items.{item_id}")
            continue
        all_real = all_real and item_entry.real
        if item_entry.value is True and not seen[item_id]:
            regressions.append(Regression(f"items.{item_id}", True, False))
    return Comparison(all_real, tuple(regressions), tuple(not_compared))


def draft_from_report(report: SuiteReport, *, generator: str) -> dict[str, object]:
    """A baseline document from a run, every entry `needs_eval`: a proposal for the
    owner to approve, never a baseline in force."""
    how = "Run the suite on the approved golden items with the real pipeline, review, then set status to real."
    return {
        "schema_version": SCHEMA_VERSION,
        "golden_set": report.golden_set,
        "status": "needs_eval",
        "approved_by": None,
        "produced_by": {"generator": generator},
        "tolerance": {"deterministic_pass_rate": 0.0, "judge_mean": 0.05},
        "metrics": {
            "deterministic_pass_rate": {
                "value": round(report.deterministic_pass_rate, 4),
                "status": "needs_eval",
                "source": f"generated by {generator}; not reviewed",
                "how_to_get_it": how,
                "used_in": ["ci-regression-gate"],
            },
            "judge_mean": {
                "value": None,
                "status": "unmeasured",
                "source": None,
                "how_to_get_it": "Run the model-graded tier with a key, review the scores, then set status to real.",
                "used_in": ["ci-regression-gate"],
            },
        },
        "items": {
            i.item_id: {
                "value": i.deterministic_passed,
                "status": "needs_eval",
                "source": f"generated by {generator}; not reviewed",
                "how_to_get_it": how,
                "used_in": ["ci-regression-gate"],
            }
            for i in report.items
        },
    }
