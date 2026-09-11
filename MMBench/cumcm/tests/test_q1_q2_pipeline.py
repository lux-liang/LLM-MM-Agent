import json
import importlib.util
import tempfile
import unittest
from pathlib import Path

from MMBench.cumcm.q1_q2_pipeline import (
    audit_q1_q2_latex,
    build_q1_q2_editor_prompt,
    load_q1_q2_results,
    render_q1_q2_figures,
)


FIXTURES = Path(__file__).parent / "fixtures"
RESULTS = FIXTURES / "q1_q2_results.json"
Q1Q2_TEX = FIXTURES / "b_q1_q2.tex"
VALIDATION_TEX = FIXTURES / "b_q1_q2_validation.tex"
PLOTTING_AVAILABLE = importlib.util.find_spec("matplotlib") is not None


class Q1Q2PipelineTests(unittest.TestCase):
    def test_loader_rejects_missing_or_nonfinite_claim_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text(json.dumps({"metadata": {"error_deg": float("nan")}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_q1_q2_results(path)

    def test_prompt_contains_main_auxiliary_boundary_and_evidence(self):
        results = load_q1_q2_results(RESULTS)
        prompt = build_q1_q2_editor_prompt(results)
        self.assertIn("主模型", prompt)
        self.assertIn("辅模型", prompt)
        self.assertIn("不能把有限外层搜索写成连续全局最优", prompt)
        self.assertIn("56.098800", prompt)
        self.assertIn("13.8761", prompt)

    def test_latex_audit_requires_cross_referenced_claims(self):
        results = load_q1_q2_results(RESULTS)
        report = audit_q1_q2_latex([Q1Q2_TEX, VALIDATION_TEX], results)
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["missing"], [])
        self.assertGreaterEqual(report["matched"], 8)
        self.assertTrue(all("\\" not in name and ":" not in name for name in report["files"]))

    @unittest.skipUnless(PLOTTING_AVAILABLE, "matplotlib is an optional plotting dependency")
    def test_renderer_emits_three_vector_figures_and_manifest(self):
        results = load_q1_q2_results(RESULTS)
        with tempfile.TemporaryDirectory() as tmp:
            manifest = render_q1_q2_figures(results, Path(tmp))
            self.assertEqual(manifest["figure_count"], 3)
            for entry in manifest["figures"]:
                pdf = Path(tmp) / entry["pdf"]
                png = Path(tmp) / entry["png"]
                self.assertTrue(pdf.is_file())
                self.assertTrue(png.is_file())
                self.assertGreater(pdf.stat().st_size, 1000)
                self.assertGreater(png.stat().st_size, 1000)
                self.assertEqual(pdf.read_bytes()[:4], b"%PDF")

    @unittest.skipUnless(PLOTTING_AVAILABLE, "matplotlib is an optional plotting dependency")
    def test_renderer_is_byte_stable_for_same_evidence(self):
        results = load_q1_q2_results(RESULTS)
        with tempfile.TemporaryDirectory() as tmp:
            first_dir = Path(tmp) / "first"
            second_dir = Path(tmp) / "second"
            first = render_q1_q2_figures(results, first_dir)
            second = render_q1_q2_figures(results, second_dir)
            first_hashes = [(item["pdf_sha256"], item["png_sha256"]) for item in first["figures"]]
            second_hashes = [(item["pdf_sha256"], item["png_sha256"]) for item in second["figures"]]
            self.assertEqual(first_hashes, second_hashes)


if __name__ == "__main__":
    unittest.main()
