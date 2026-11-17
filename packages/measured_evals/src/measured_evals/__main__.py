"""`python -m measured_evals run`: the deterministic tier from the command line (GROW-14).

No model and no key: outputs come from a fake writer (`measured_evals.fake`) or
from a directory of saved outputs (`--outputs DIR`, one `<item-id>.md` per item).
Exit code 0 when every item's deterministic checks pass, 1 otherwise, 2 for a bad
call. With `--baseline`, a regression against a *real* baseline also exits 1; a
placeholder baseline is reported and never enforced.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from measured_evals.baseline import compare, draft_from_report, load_baseline
from measured_evals.fake import CARELESS, GROUNDED, careless_writer, grounded_writer
from measured_evals.golden import GoldenItem, load_golden_set
from measured_evals.suites import DEFAULT_MAX_WORDS, SuiteReport, run_set

WRITERS: dict[str, tuple[str, Callable[[GoldenItem], str]]] = {
    "fake": (GROUNDED, grounded_writer),
    "fake-careless": (CARELESS, careless_writer),
}


def _saved(directory: Path) -> Callable[[GoldenItem], str]:
    def read(item: GoldenItem) -> str:
        return (directory / f"{item.id}.md").read_text(encoding="utf-8")

    return read


def _lines(report: SuiteReport, generator: str) -> list[str]:
    out = [f"golden set: {report.golden_set} · generator: {generator}"]
    for item in report.items:
        out.append(f"{'PASS' if item.deterministic_passed else 'FAIL'}  {item.item_id}")
        out += [f"      {f.group}.{f.kind}: {f.detail}" for f in item.failures]
    passed = sum(1 for i in report.items if i.deterministic_passed)
    out.append(
        f"deterministic tier: {passed}/{len(report.items)} items passed · "
        f"{report.pending_judge_assertions} judge assertion(s) not run (model-graded tier)"
    )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m measured_evals")
    run = parser.add_subparsers(dest="command", required=True).add_parser("run")
    run.add_argument("--golden", type=Path, help="golden set TOML (default: bundled draft)")
    run.add_argument("--writer", choices=sorted(WRITERS), default="fake")
    run.add_argument("--outputs", type=Path, help="directory of saved outputs, <item-id>.md")
    run.add_argument("--max-words", type=int, default=DEFAULT_MAX_WORDS)
    run.add_argument("--approved-only", action="store_true")
    run.add_argument("--baseline", type=Path, help="baseline JSON to compare against")
    run.add_argument("--write-draft-baseline", type=Path, help="write a needs_eval baseline")
    args = parser.parse_args(argv)

    golden = load_golden_set(args.golden)
    if args.outputs is not None:
        generator, generate = f"saved:{args.outputs.name}", _saved(args.outputs)
    else:
        generator, generate = WRITERS[args.writer]
    report = run_set(golden, generate, max_words=args.max_words, approved_only=args.approved_only)
    lines = _lines(report, generator)
    if args.approved_only and not report.items:
        lines.append("no approved items: nothing was measured (every item is still needs_eval)")
    failed = bool(report.failed_ids)
    if args.baseline is not None:
        comparison = compare(load_baseline(args.baseline), report)
        label = "ENFORCED" if comparison.enforced else "NOT ENFORCED (baseline is a placeholder)"
        lines.append(f"baseline: {len(comparison.regressions)} regression(s) · {label}")
        lines += [f"      {r.what}: {r.baseline} -> {r.current}" for r in comparison.regressions]
        failed = failed or comparison.failed
    if args.write_draft_baseline is not None:
        document = draft_from_report(report, generator=generator)
        args.write_draft_baseline.write_text(json.dumps(document, indent=2) + "\n", "utf-8")
        lines.append(f"wrote a needs_eval draft baseline to {args.write_draft_baseline}")
    sys.stdout.write("\n".join(lines) + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
