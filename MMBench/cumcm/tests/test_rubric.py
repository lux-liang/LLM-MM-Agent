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


if __name__ == "__main__":
    unittest.main()
