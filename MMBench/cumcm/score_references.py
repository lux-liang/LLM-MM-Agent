"""Aggregate local paper reports produced by evaluate_paper.py.

The command never decides an official prize. It creates a reproducible
learning leaderboard and marks reports needing human review.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


def _iter_reports(paths: Iterable[Path]):
    for path in paths:
        if path.suffix.lower() != ".json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and "overall_score" in data:
            yield path, data


def aggregate(directory: Path) -> List[Dict[str, Any]]:
    def finite_number(value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return number if math.isfinite(number) else 0.0

    rows = []
    for path, report in _iter_reports(sorted(directory.rglob("*.json"))):
        dimensions = report.get("dimension_scores", {})
        rows.append(
            {
                "paper": report.get("paper") or path.stem,
                "report": str(path),
                "overall_score": finite_number(report.get("overall_score", 0)),
                "format_score": finite_number(report.get("format_score", 0)),
                "assumptions_reasonableness": finite_number(dimensions.get("assumptions_reasonableness", 0)),
                "modeling_creativity": finite_number(dimensions.get("modeling_creativity", 0)),
                "results_correctness": finite_number(dimensions.get("results_correctness", 0)),
                "writing_clarity": finite_number(dimensions.get("writing_clarity", 0)),
                "confidence": finite_number(report.get("confidence", 0)),
                "human_review_required": bool(report.get("human_review_required", True)),
            }
        )
    # Unreviewed high scores must never outrank reports that passed all gates.
    return sorted(
        rows,
        key=lambda row: (row["human_review_required"], -row["overall_score"]),
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate CUMCM learning reports")
    parser.add_argument("--reports-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    rows = aggregate(args.reports_dir.expanduser().resolve())
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        fields = list(rows[0]) if rows else [
            "paper", "report", "overall_score", "format_score",
            "assumptions_reasonableness", "modeling_creativity",
            "results_correctness", "writing_clarity", "confidence",
            "human_review_required"
        ]
        with args.output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    print(json.dumps({"count": len(rows), "rows": rows}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
