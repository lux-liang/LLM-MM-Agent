import json
import tempfile
import unittest
from pathlib import Path

from MMBench.cumcm.score_references import aggregate


class RubricTests(unittest.TestCase):
    def test_four_dimensions_sum_to_one_hundred(self):
        path = Path(__file__).parents[1] / "rubric.json"
        rubric = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            sum(item["max_score"] for item in rubric["dimensions"]),
            100,
        )
        self.assertEqual(len(rubric["dimensions"]), 4)

    def test_reference_manifest_keeps_awards_and_copyright_conservative(self):
        path = Path(__file__).parents[1] / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        sources = manifest["sources"]
        self.assertEqual(len({item["id"] for item in sources}), len(sources))
        for item in sources:
            self.assertTrue(item["url"].startswith("https://"))
            self.assertFalse(item["download_allowed"])
            if item["kind"] == "github_index":
                self.assertFalse(item["award_verified"])
            if item["kind"] == "official_candidate_paper_link":
                self.assertFalse(item["award_verified"])

    def test_leaderboard_places_human_review_reports_last(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "unchecked.json").write_text(
                json.dumps(
                    {"paper": "unchecked", "overall_score": 99, "human_review_required": True}
                ),
                encoding="utf-8",
            )
            (root / "checked.json").write_text(
                json.dumps(
                    {"paper": "checked", "overall_score": 80, "human_review_required": False}
                ),
                encoding="utf-8",
            )
            rows = aggregate(root)
        self.assertEqual([row["paper"] for row in rows], ["checked", "unchecked"])

    def test_leaderboard_does_not_rank_failed_compliance_by_score(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            common = {"human_review_required": True, "confidence": 0.9}
            (root / "failed.json").write_text(
                json.dumps(
                    {
                        **common,
                        "paper": "failed",
                        "overall_score": 99,
                        "format_assessment": {"status": "FAIL"},
                        "ai_usage_assessment": {"status": "FAIL"},
                    }
                ),
                encoding="utf-8",
            )
            (root / "unknown.json").write_text(
                json.dumps(
                    {
                        **common,
                        "paper": "unknown",
                        "overall_score": 70,
                        "format_assessment": {"status": "UNKNOWN"},
                        "ai_usage_assessment": {"status": "PASS"},
                    }
                ),
                encoding="utf-8",
            )
            rows = aggregate(root)
        self.assertEqual([row["paper"] for row in rows], ["unknown", "failed"])

    def test_leaderboard_recomputes_readiness_and_redacts_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "unsafe.json").write_text(
                json.dumps(
                    {
                        "paper": "C:\\private\\student\\paper.pdf",
                        "overall_score": 99,
                        "human_review_required": "false",
                        "submission_ready": "false",
                        "policy_profile": {"profile_id": "CUMCM-2026", "status": "PASS"},
                        "format_assessment": {"status": "UNKNOWN"},
                        "ai_usage_assessment": {"status": "PASS"},
                        "compliance_status": "UNKNOWN",
                        "adjudication": {"human_adjudication_required": False},
                    }
                ),
                encoding="utf-8",
            )
            rows = aggregate(root)

        self.assertEqual(rows[0]["paper"], "paper.pdf")
        self.assertEqual(rows[0]["report"], "unsafe.json")
        self.assertFalse(rows[0]["submission_ready"])
        self.assertTrue(rows[0]["human_review_required"])
        self.assertNotIn(directory, json.dumps(rows))

    def test_quality_rank_is_independent_from_readiness_rank(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reports = {
                "ready.json": {
                    "schema_version": "1.1",
                    "paper": "ready.pdf",
                    "overall_score": 70,
                    "dimension_scores": {
                        "assumptions_reasonableness": 17.5,
                        "modeling_creativity": 17.5,
                        "results_correctness": 17.5,
                        "writing_clarity": 17.5,
                    },
                    "quality_review_required": False,
                    "policy_profile": {"profile_id": "CUMCM-2026", "status": "PASS", "checks": [{"status": "PASS"}]},
                    "format_assessment": {"status": "PASS", "checks": [{"status": "PASS"}]},
                    "ai_usage_assessment": {"status": "PASS", "checks": [{"status": "PASS"}]},
                    "input_safety_assessment": {"status": "NOT_APPLICABLE", "checks": [{"status": "NOT_APPLICABLE"}]},
                    "compliance_status": "PASS",
                    "adjudication": {"human_adjudication_required": False},
                },
                "failed.json": {
                    "schema_version": "1.1",
                    "paper": "failed.pdf",
                    "overall_score": 99,
                    "dimension_scores": {
                        "assumptions_reasonableness": 24.75,
                        "modeling_creativity": 24.75,
                        "results_correctness": 24.75,
                        "writing_clarity": 24.75,
                    },
                    "quality_review_required": False,
                    "policy_profile": {"profile_id": "CUMCM-2026", "status": "PASS", "checks": [{"status": "PASS"}]},
                    "format_assessment": {"status": "FAIL", "checks": [{"status": "FAIL"}]},
                    "ai_usage_assessment": {"status": "PASS", "checks": [{"status": "PASS"}]},
                    "input_safety_assessment": {"status": "PASS", "checks": [{"status": "PASS"}]},
                    "compliance_status": "FAIL",
                    "adjudication": {"human_adjudication_required": True},
                },
            }
            for name, report in reports.items():
                (root / name).write_text(json.dumps(report), encoding="utf-8")
            rows = aggregate(root)

        self.assertEqual([row["paper"] for row in rows], ["ready.pdf", "failed.pdf"])
        self.assertTrue(rows[0]["submission_ready"])
        self.assertEqual(rows[0]["readiness_rank"], 1)
        self.assertEqual(rows[0]["quality_rank"], 2)
        self.assertEqual(rows[1]["quality_rank"], 1)

    def test_leaderboard_rejects_contradictory_group_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = {
                "schema_version": "1.1",
                "paper": "contradictory.pdf",
                "overall_score": 80,
                "dimension_scores": {
                    "assumptions_reasonableness": 20,
                    "modeling_creativity": 20,
                    "results_correctness": 20,
                    "writing_clarity": 20,
                },
                "quality_review_required": False,
                "policy_profile": {"profile_id": "CUMCM-2026", "status": "PASS", "checks": [{"status": "PASS"}]},
                "format_assessment": {"status": "PASS", "checks": [{"status": "FAIL"}]},
                "ai_usage_assessment": {"status": "PASS", "checks": [{"status": "PASS"}]},
                "input_safety_assessment": {"status": "PASS", "checks": [{"status": "PASS"}]},
                "compliance_status": "PASS",
                "adjudication": {"human_adjudication_required": False},
            }
            (root / "contradictory.json").write_text(json.dumps(report), encoding="utf-8")
            rows = aggregate(root)

        self.assertEqual(rows[0]["format_compliance"], "FAIL")
        self.assertEqual(rows[0]["compliance_status"], "FAIL")
        self.assertFalse(rows[0]["submission_ready"])

    def test_leaderboard_recomputes_overall_from_four_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = {
                "overall_score": 999,
                "dimension_scores": {
                    "assumptions_reasonableness": 10,
                    "modeling_creativity": 10,
                    "results_correctness": 10,
                    "writing_clarity": 10,
                },
            }
            (root / "forged.json").write_text(json.dumps(report), encoding="utf-8")
            rows = aggregate(root)

        self.assertEqual(rows[0]["overall_score"], 40)
        self.assertEqual(rows[0]["reported_overall_score"], 999)


if __name__ == "__main__":
    unittest.main()
