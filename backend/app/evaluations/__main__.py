"""
CLI entry point for the AtlasCore evaluation framework.

Usage:
    python -m app.evaluations.run
    python -m app.evaluations.run --category A_grounding
    python -m app.evaluations.run --output eval_results.json
    python -m app.evaluations.run --verbose

Runs all evaluation cases (or a filtered subset) using DeterministicTestAnswerProvider.
Outputs a human-readable summary to stdout and optionally a JSON artifact file.

No paid API calls are required for the default baseline.
Real LLM providers can be enabled via ANSWER_PROVIDER env var, but are NOT
required for the CI evaluation baseline.

IMPORTANT: These evaluate deterministic SYSTEM BEHAVIOUR — pipeline correctness,
abstention accuracy, citation integrity, security properties. Not "model accuracy".
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from app.evaluations.cases import ALL_CASES
from app.evaluations.runner import EvaluationRunner
from app.evaluations.schemas import EvaluationSummary


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.evaluations.run",
        description=(
            "AtlasCore evaluation framework — measures deterministic system behaviour.\n"
            "Uses DeterministicTestAnswerProvider by default (no API key needed)."
        ),
    )
    parser.add_argument(
        "--category",
        metavar="CAT",
        help=(
            "Filter to a single category (e.g. A_grounding, B_abstention). "
            "Omit to run all 16 categories."
        ),
    )
    parser.add_argument(
        "--output",
        metavar="FILE",
        help="Write JSON results artifact to this file.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show per-case pass/fail details.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Exit immediately after the first failure.",
    )
    parser.add_argument(
        "--tags",
        metavar="TAG",
        nargs="+",
        help="Only run cases with all of these tags.",
    )
    return parser.parse_args()


def _format_summary(summary: EvaluationSummary, verbose: bool) -> str:
    lines: list[str] = []
    lines.append("")
    lines.append("=" * 60)
    lines.append("AtlasCore Evaluation Summary")
    lines.append("=" * 60)
    lines.append(f"Run ID:     {summary.run_id}")
    lines.append(f"Provider:   {summary.provider_id}")
    lines.append(f"Model:      {summary.model_id}")
    lines.append(f"Duration:   {summary.duration_s:.2f}s")
    lines.append("")
    lines.append(f"Total:      {summary.total}")
    lines.append(f"Passed:     {summary.passed}")
    lines.append(f"Failed:     {summary.failed}")
    lines.append(f"Errored:    {summary.errored}")
    pass_pct = summary.pass_rate * 100
    lines.append(f"Pass rate:  {pass_pct:.1f}%")
    lines.append("")
    lines.append("By Category:")
    for cat, (cat_passed, cat_total) in sorted(summary.by_category.items()):
        icon = "✓" if cat_passed == cat_total else "✗"
        lines.append(f"  {icon}  {cat:<35}  {cat_passed}/{cat_total}")

    if verbose:
        lines.append("")
        lines.append("Per-Case Results:")
        for result in summary.results:
            icon = "✓" if result.passed else "✗"
            detail = f" — {result.error}" if result.error else ""
            lines.append(
                f"  {icon}  [{result.category.value}] {result.case_name}"
                f"  ({result.actual_status}, band={result.actual_band},"
                f" {result.duration_ms:.1f}ms){detail}"
            )

    lines.append("=" * 60)
    if summary.pass_rate == 1.0:
        lines.append("ALL CASES PASSED")
    else:
        lines.append(f"FAILURES: {summary.failed} case(s) failed")
    lines.append("=" * 60)
    return "\n".join(lines)


def _summary_to_dict(summary: EvaluationSummary) -> dict[str, object]:
    return {
        "run_id": summary.run_id,
        "provider_id": summary.provider_id,
        "model_id": summary.model_id,
        "duration_s": round(summary.duration_s, 3),
        "total": summary.total,
        "passed": summary.passed,
        "failed": summary.failed,
        "errored": summary.errored,
        "pass_rate": round(summary.pass_rate, 4),
        "by_category": {
            cat: {"passed": p, "total": t} for cat, (p, t) in sorted(summary.by_category.items())
        },
        "results": [
            {
                "case_name": r.case_name,
                "category": r.category.value,
                "passed": r.passed,
                "actual_status": r.actual_status,
                "actual_band": r.actual_band,
                "error": r.error,
                "details": r.details,
                "duration_ms": round(r.duration_ms, 2),
            }
            for r in summary.results
        ],
    }


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    args = _parse_args()

    # Filter cases.
    cases = ALL_CASES
    if args.category:
        cases = [c for c in cases if c.category.value == args.category]
        if not cases:
            print(f"No cases found for category: {args.category!r}", file=sys.stderr)
            print("Available categories:", file=sys.stderr)
            from app.evaluations.schemas import EvaluationCategory

            for cat in EvaluationCategory:
                print(f"  {cat.value}", file=sys.stderr)
            return 2

    if args.tags:
        tag_set = set(args.tags)
        cases = [c for c in cases if tag_set.issubset(set(c.tags))]
        if not cases:
            print(f"No cases matched tags: {args.tags!r}", file=sys.stderr)
            return 2

    print(f"Running {len(cases)} evaluation case(s)…")

    runner = EvaluationRunner()

    if args.fail_fast:
        # Run one at a time, stop on first failure.
        from app.evaluations.runner import _run_case
        from app.evaluations.schemas import EvaluationResult

        results: list[EvaluationResult] = []
        for case in cases:
            result = _run_case(case, runner._provider)
            results.append(result)
            if not result.passed:
                print(f"FAIL [{case.name}]: {result.error}")
                break
        # Build a partial summary from results so far.
        import uuid as _uuid

        total = len(results)
        passed = sum(1 for r in results if r.passed)
        by_category: dict[str, tuple[int, int]] = {}
        for r in results:
            cat_key: str = r.category.value
            cp, ct = by_category.get(cat_key, (0, 0))
            by_category[cat_key] = (cp + (1 if r.passed else 0), ct + 1)
        from app.evaluations.schemas import EvaluationSummary

        summary = EvaluationSummary(
            total=total,
            passed=passed,
            failed=total - passed,
            errored=0,
            pass_rate=passed / total if total > 0 else 0.0,
            by_category=by_category,
            results=results,
            run_id=str(_uuid.uuid4()),
            duration_s=0.0,
            provider_id=runner._provider.provider_id,
            model_id=runner._provider.model_id,
        )
    else:
        summary = runner.run(cases)

    print(_format_summary(summary, verbose=args.verbose))

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(json.dumps(_summary_to_dict(summary), indent=2))
        print(f"\nJSON artifact written to: {output_path}")

    return 0 if summary.pass_rate == 1.0 else 1


if __name__ == "__main__":
    sys.exit(main())
