"""Aggregate local paper reports produced by evaluate_paper.py.

The command never decides an official prize. It creates a reproducible
learning leaderboard and marks reports needing human review.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Sequence


VALID_COMPLIANCE_STATUSES = frozenset(
    {"PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE"}
)
DIMENSION_IDS = (
    "assumptions_reasonableness",
    "modeling_creativity",
    "results_correctness",
    "writing_clarity",
)


def _status(value: Any) -> str:
    normalized = str(value or "UNKNOWN").upper()
    return normalized if normalized in VALID_COMPLIANCE_STATUSES else "UNKNOWN"


def _safe_basename(value: Any, fallback: str) -> str:
    name = PurePosixPath(str(value or "").replace("\\", "/")).name.strip()
    return name or fallback


def _safe_profile_id(value: Any) -> str:
    profile_id = str(value or "")
    if re.fullmatch(r"[A-Za-z0-9._-]{1,80}", profile_id):
        return profile_id
    return "legacy/unversioned"


def _overall_compliance_status(*statuses: str) -> str:
    values = set(statuses)
    if "FAIL" in values:
        return "FAIL"
    if "UNKNOWN" in values or "NOT_APPLICABLE" in values:
        return "UNKNOWN"
    return "PASS" if values == {"PASS"} else "UNKNOWN"


def _checks_status(group: Dict[str, Any]) -> str:
    checks = group.get("checks")
    if not isinstance(checks, list) or not checks:
        return "UNKNOWN"
    statuses = [
        _status(item.get("status")) if isinstance(item, dict) else "UNKNOWN"
        for item in checks
    ]
    if "FAIL" in statuses:
        return "FAIL"
    if "UNKNOWN" in statuses:
        return "UNKNOWN"
    if "PASS" in statuses:
        return "PASS"
    return "NOT_APPLICABLE"


def _verified_group_status(group: Dict[str, Any]) -> str:
    declared = _status(group.get("status"))
    checked = _checks_status(group)
    if "FAIL" in {declared, checked}:
        return "FAIL"
    if declared == "PASS" and checked in {"PASS", "NOT_APPLICABLE"}:
        return "PASS"
    if declared == "NOT_APPLICABLE" and checked == "NOT_APPLICABLE":
        return "NOT_APPLICABLE"
    return "UNKNOWN"


def _iter_reports(paths: Iterable[Path], root: Optional[Path] = None):
    for path in paths:
        if path.suffix.lower() != ".json":
            continue
        try:
            if path.is_symlink():
                continue
            resolved = path.resolve(strict=True)
            if root is not None:
                resolved.relative_to(root)
            if resolved.stat().st_size > 20_000_000:
                continue
            data = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
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

    root = directory.resolve()
    if not root.is_dir():
        raise NotADirectoryError(directory)
    rows = []
    for path, report in _iter_reports(sorted(root.rglob("*.json")), root):
        dimensions = report.get("dimension_scores", {})
        format_assessment = report.get("format_assessment", {})
        ai_assessment = report.get("ai_usage_assessment", {})
        policy_profile = report.get("policy_profile", {})
        input_safety = report.get("input_safety_assessment", {})
        if not isinstance(dimensions, dict):
            dimensions = {}
        if not isinstance(format_assessment, dict):
            format_assessment = {}
        if not isinstance(ai_assessment, dict):
            ai_assessment = {}
        if not isinstance(policy_profile, dict):
            policy_profile = {}
        if not isinstance(input_safety, dict):
            input_safety = {}
        policy_status = _verified_group_status(policy_profile)
        format_status = _verified_group_status(format_assessment)
        ai_status = _verified_group_status(ai_assessment)
        input_safety_status = _verified_group_status(input_safety)
        derived_status = _overall_compliance_status(
            policy_status, format_status, ai_status
        )
        compliance_status = derived_status
        if "compliance_status" in report:
            compliance_status = _overall_compliance_status(
                derived_status, _status(report.get("compliance_status"))
            )
        adjudication = report.get("adjudication", {})
        if not isinstance(adjudication, dict):
            adjudication = {}
        compliance_review_required = not (
            policy_status == "PASS"
            and format_status == "PASS"
            and ai_status == "PASS"
            and compliance_status == "PASS"
            and adjudication.get("human_adjudication_required") is False
        )
        report_schema_valid = bool(
            re.fullmatch(r"1\.\d+", str(report.get("schema_version", "")))
        )
        profile_id = _safe_profile_id(policy_profile.get("profile_id"))
        reported_human_review = report.get("human_review_required") is not False
        quality_value = report.get("quality_review_required")
        quality_review_required = (
            quality_value if isinstance(quality_value, bool) else reported_human_review
        )
        dimension_scores: Dict[str, float] = {}
        quality_report_valid = True
        for dimension in DIMENSION_IDS:
            raw_value = dimensions.get(dimension)
            try:
                if isinstance(raw_value, bool):
                    raise ValueError("boolean is not a score")
                value = float(raw_value)
            except (TypeError, ValueError):
                value = 0.0
                quality_report_valid = False
            if not math.isfinite(value) or value < 0 or value > 25:
                value = 0.0
                quality_report_valid = False
            dimension_scores[dimension] = round(value, 2)
        quality_review_required = quality_review_required or not quality_report_valid
        overall_score = round(sum(dimension_scores.values()), 2)
        compliance_review_required = compliance_review_required or not (
            report_schema_valid
            and profile_id == "CUMCM-2026"
            and input_safety_status in {"PASS", "NOT_APPLICABLE"}
        )
        submission_ready = not compliance_review_required
        human_review_required = quality_review_required or compliance_review_required
        try:
            report_name = path.relative_to(root).as_posix()
        except ValueError:
            report_name = path.name
        rows.append(
            {
                "paper": _safe_basename(report.get("paper"), path.stem),
                "report": report_name,
                "overall_score": overall_score,
                "reported_overall_score": finite_number(report.get("overall_score", 0)),
                "format_score": max(0.0, min(10.0, finite_number(report.get("format_score", 0)))),
                "assumptions_reasonableness": dimension_scores["assumptions_reasonableness"],
                "modeling_creativity": dimension_scores["modeling_creativity"],
                "results_correctness": dimension_scores["results_correctness"],
                "writing_clarity": dimension_scores["writing_clarity"],
                "confidence": finite_number(report.get("confidence", 0)),
                "quality_review_required": quality_review_required,
                "compliance_review_required": compliance_review_required,
                "human_review_required": human_review_required,
                "report_schema_valid": report_schema_valid,
                "quality_report_valid": quality_report_valid,
                "policy_profile": profile_id,
                "policy_profile_status": policy_status,
                "format_compliance": format_status,
                "ai_usage_compliance": ai_status,
                "input_safety": input_safety_status,
                "compliance_status": compliance_status,
                "submission_ready": submission_ready,
            }
        )
    status_rank = {"PASS": 0, "NOT_APPLICABLE": 1, "UNKNOWN": 1, "FAIL": 2}

    def compliance_rank(row: Dict[str, Any]) -> int:
        input_safety_rank = (
            0
            if row["input_safety"] in {"PASS", "NOT_APPLICABLE"}
            else status_rank.get(str(row["input_safety"]).upper(), 1)
        )
        return max(
            status_rank.get(str(row["compliance_status"]).upper(), 1),
            status_rank.get(str(row["format_compliance"]).upper(), 1),
            status_rank.get(str(row["ai_usage_compliance"]).upper(), 1),
            input_safety_rank,
        )

    quality_order = sorted(
        range(len(rows)),
        key=lambda index: (-rows[index]["overall_score"], rows[index]["paper"]),
    )
    for rank, index in enumerate(quality_order, start=1):
        rows[index]["quality_rank"] = rank

    # The returned view is readiness-first, while quality_rank remains a
    # separate score-only ranking so compliance never mutates quality.
    ranked = sorted(
        rows,
        key=lambda row: (
            not row["submission_ready"],
            compliance_rank(row),
            row["compliance_review_required"],
            row["quality_review_required"],
            -row["overall_score"],
        ),
    )
    for rank, row in enumerate(ranked, start=1):
        row["readiness_rank"] = rank
    return ranked


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate CUMCM learning reports")
    parser.add_argument("--reports-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        rows = aggregate(args.reports_dir.expanduser().resolve())
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            fields = list(rows[0]) if rows else [
                "paper", "report", "overall_score", "reported_overall_score", "format_score",
                "assumptions_reasonableness", "modeling_creativity",
                "results_correctness", "writing_clarity", "confidence",
                "quality_review_required", "compliance_review_required",
                "human_review_required", "report_schema_valid", "quality_report_valid",
                "policy_profile", "policy_profile_status", "format_compliance",
                "ai_usage_compliance", "input_safety", "compliance_status",
                "submission_ready", "quality_rank", "readiness_rank"
            ]
            with args.output.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
    except Exception as exc:
        print(f"aggregation failed: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "count": len(rows),
                "view": "submission_readiness",
                "ranking_note": "readiness_rank is compliance-first; quality_rank is score-only",
                "disclaimer": "Learning scores and compliance prechecks are not official contest grades or prize decisions.",
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
