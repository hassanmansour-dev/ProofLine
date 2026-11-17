"""`python -m measured_evals run`: the deterministic tier from the command line (GROW-14).

No model and no key: outputs come from a fake writer (`measured_evals.fake`) or
from a directory of saved outputs (`--outputs DIR`, one `<item-id>.md` per item).
Exit code 0 when every item's deterministic checks pass, 1 otherwise, 2 for a bad
call. With `--baseline`, a regression against a *real* baseline also exits 1; a
placeholder baseline is reported and never enforced.

`python -m measured_evals calibration-sheet` and `calibration-agreement`: the critic
calibration sheet and its agreement metric (`measured_evals.calibration`,
`docs/CALIBRATION.md`). Also no model and no key.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from measured_evals.baseline import compare, draft_from_report, load_baseline
from measured_evals.calibration import (
    CalibrationError,
    agreement,
    agreement_lines,
    build_sheet,
    load_critic_scores,
    parse_filled_sheet,
    read_outputs,
    sheet_csv,
    sheet_markdown,
)
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


def _calibration(args: argparse.Namespace) -> int:
    try:
        critic = load_critic_scores(args.critic_scores)
        if args.command == "calibration-sheet":
            sheet = build_sheet(read_outputs(args.outputs), critic, blind=not args.show_critic)
            args.out_dir.mkdir(parents=True, exist_ok=True)
            for name, text in (
                ("calibration_sheet.csv", sheet_csv(sheet)),
                ("calibration_sheet.md", sheet_markdown(sheet)),
            ):
                target = args.out_dir / name
                if target.exists() and not args.overwrite:
                    raise CalibrationError(
                        f"{target} exists and may hold scores; pass --overwrite to replace it"
                    )
                target.write_text(text, encoding="utf-8")
            lines = [
                f"wrote {len(sheet.rows)} rows to {args.out_dir}/calibration_sheet.csv and .md "
                f"({'blind' if sheet.blind else 'critic scores shown'})"
            ]
        else:
            human = parse_filled_sheet(args.sheet.read_text(encoding="utf-8"), scale=critic.scale)
            lines = agreement_lines(agreement(critic, human, threshold=args.threshold))
    except (CalibrationError, OSError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m measured_evals")
    commands = parser.add_subparsers(dest="command", required=True)
    sheet = commands.add_parser("calibration-sheet", help="blank scoring sheet (CSV + Markdown)")
    sheet.add_argument("--outputs", type=Path, required=True, help="directory of <item-id>.md")
    sheet.add_argument("--critic-scores", type=Path, required=True, help="critic scores JSON")
    sheet.add_argument("--out-dir", type=Path, required=True)
    sheet.add_argument("--show-critic", action="store_true", help="not blind: anchors the scorer")
    sheet.add_argument("--overwrite", action="store_true")
    agree = commands.add_parser("calibration-agreement", help="kappa on pass/fail, once filled")
    agree.add_argument("--sheet", type=Path, required=True, help="the filled CSV")
    agree.add_argument("--critic-scores", type=Path, required=True)
    agree.add_argument("--threshold", type=float, required=True, help="the critic's pass score")
    run = commands.add_parser("run")
    run.add_argument("--golden", type=Path, help="golden set TOML (default: bundled)")
    run.add_argument("--writer", choices=sorted(WRITERS), default="fake")
    run.add_argument("--outputs", type=Path, help="directory of saved outputs, <item-id>.md")
    run.add_argument("--max-words", type=int, default=DEFAULT_MAX_WORDS)
    run.add_argument("--approved-only", action="store_true")
    run.add_argument("--baseline", type=Path, help="baseline JSON to compare against")
    run.add_argument("--write-draft-baseline", type=Path, help="write a needs_eval baseline")
    args = parser.parse_args(argv)
    if args.command != "run":
        return _calibration(args)

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
