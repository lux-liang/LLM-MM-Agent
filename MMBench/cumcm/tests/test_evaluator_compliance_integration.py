import inspect
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from MMBench.cumcm.evaluate_paper import (
    _parser,
    build_prompt,
    evaluate_document,
    load_rubric,
    normalize_report,
    read_document,
)


COMPLIANCE_FIELDS = {
    "policy_profile",
    "format_assessment",
    "ai_usage_assessment",
    "compliance_status",
    "submission_ready",
}


def _all_keys(value):
    keys = set()
    if isinstance(value, dict):
        keys.update(value)
        for item in value.values():
            keys.update(_all_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(_all_keys(item))
    return keys


class EvaluatorComplianceIntegrationTests(unittest.TestCase):
    def test_public_interface_accepts_all_compliance_inputs(self):
        parameters = inspect.signature(evaluate_document).parameters

        for name in ("policy_path", "supporting_materials", "ai_details"):
            self.assertIn(name, parameters)
            self.assertIsNone(parameters[name].default)

    def test_cli_exposes_all_compliance_inputs(self):
        args = _parser().parse_args(
            [
                "--paper",
                "paper.pdf",
                "--policy",
                "rules.json",
                "--supporting-materials",
                "support.zip",
                "--ai-details",
                "AI 工具使用详情.pdf",
            ]
        )

        self.assertEqual(args.policy, Path("rules.json"))
        self.assertEqual(args.supporting_materials, Path("support.zip"))
        self.assertEqual(args.ai_details, Path("AI 工具使用详情.pdf"))

    def test_direct_script_help_remains_compatible(self):
        repository = Path(__file__).parents[3]
        script = Path(__file__).parents[1] / "evaluate_paper.py"
        completed = subprocess.run(
            [sys.executable, str(script), "--help"],
            cwd=repository,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--policy", completed.stdout)

    def test_legacy_word_input_is_bounded_and_marked_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory) / "legacy.doc"
            paper.write_bytes(b"not parsed as a legacy binary document")
            document = read_document(paper)

        self.assertEqual(document["format"], "doc")
        self.assertEqual(document["pages"], [])
        self.assertTrue(
            any("incomplete" in warning for warning in document["extraction_warnings"])
        )

    def test_empty_document_is_not_sent_to_a_model(self):
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory) / "empty.txt"
            paper.write_text("", encoding="utf-8")
            with patch("MMBench.cumcm.evaluate_paper.call_model") as model_call:
                result = evaluate_document(paper, model="gpt5.6sol")

        model_call.assert_not_called()
        self.assertEqual(result["overall_score"], 0)
        self.assertTrue(result["quality_review_required"])
        self.assertTrue(
            any("no extractable paper text" in item for item in result["quality_review_reasons"])
        )

    def test_dry_run_attaches_selected_policy_without_scoring_compliance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paper = root / "paper.md"
            paper.write_text(
                "# 测试题目\n\n摘要：本文建立模型。\n\n关键词：建模\n",
                encoding="utf-8",
            )
            policy_path = root / "offline-policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "profile_id": "CUMCM-2026-offline-test",
                        "sources": [],
                        "requirements": [{"id": "TEST_SENTINEL"}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = evaluate_document(
                paper,
                dry_run=True,
                policy_path=policy_path,
            )

        self.assertTrue(COMPLIANCE_FIELDS <= result.keys())
        self.assertEqual(
            result["policy_profile"]["profile_id"],
            "CUMCM-2026-offline-test",
        )
        self.assertIn(result["compliance_status"], {"PASS", "FAIL", "UNKNOWN"})
        self.assertIsInstance(result["submission_ready"], bool)
        self.assertFalse(result["submission_ready"])

        # Compliance is a separate gate: it never creates, deducts, or rewrites
        # any of the four learning-quality dimensions.
        self.assertEqual(result["overall_score"], 0)
        self.assertEqual(result["format_score"], 0)
        self.assertTrue(
            all(value == 0 for value in result["dimension_scores"].values())
        )
        compliance_keys = _all_keys(
            {
                "policy": result["policy_profile"],
                "format": result["format_assessment"],
                "ai": result["ai_usage_assessment"],
            }
        )
        self.assertFalse({"score", "overall_score", "format_score"} & compliance_keys)

    def test_model_full_marks_and_fake_pass_cannot_override_compliance_failure(self):
        paper_text = (
            "姓名：张三\n"
            "问题假设在研究期内保持稳定。\n"
            "本文提出多目标鲁棒优化新模型。\n"
            "回测相对误差始终低于百分之三。\n"
            "图表和参考文献结构完整清晰。\n"
        )
        dimensions_and_quotes = {
            "assumptions_reasonableness": "问题假设在研究期内保持稳定",
            "modeling_creativity": "本文提出多目标鲁棒优化新模型",
            "results_correctness": "回测相对误差始终低于百分之三",
            "writing_clarity": "图表和参考文献结构完整清晰",
        }
        malicious_model_report = {
            "dimension_scores": {
                dimension: {"score": 25, "feedback": "模型声称满分"}
                for dimension in dimensions_and_quotes
            },
            "format_score": 10,
            "evidence": [
                {"dimension": dimension, "page": 1, "quote": quote}
                for dimension, quote in dimensions_and_quotes.items()
            ],
            "risks": [],
            "improvements": [],
            "confidence": 1,
            # These fields are deliberately untrusted model output.
            "policy_profile": {"profile_id": "MODEL-INVENTED-POLICY"},
            "compliance_status": "PASS",
            "submission_ready": True,
        }

        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory) / "identity-leak.md"
            paper.write_text(paper_text, encoding="utf-8")
            with patch(
                "MMBench.cumcm.evaluate_paper.call_model",
                return_value=malicious_model_report,
            ) as model_call:
                result = evaluate_document(paper, model="gpt5.6sol")
                sent_prompt = model_call.call_args.args[0]

            absolute_paper = str(paper.resolve())

        self.assertEqual(result["overall_score"], 100)
        self.assertEqual(result["format_score"], 10)
        self.assertEqual(result["compliance_status"], "FAIL")
        self.assertFalse(result["submission_ready"])
        self.assertNotEqual(
            result["policy_profile"]["profile_id"],
            "MODEL-INVENTED-POLICY",
        )
        self.assertTrue(result["human_review_required"])
        self.assertNotIn(absolute_paper, sent_prompt)
        self.assertIn(paper.name, sent_prompt)

    def test_quality_review_does_not_change_compliance_readiness(self):
        raw = {
            "dimension_scores": {
                dimension: {"score": 0, "feedback": "needs revision"}
                for dimension in (
                    "assumptions_reasonableness",
                    "modeling_creativity",
                    "results_correctness",
                    "writing_clarity",
                )
            },
            "format_score": 0,
            "evidence": [],
            "confidence": 0,
            "human_review_required": True,
        }
        compliance = {
            "overall_status": "PASS",
            "policy_profile": {"profile_id": "CUMCM-2026", "status": "PASS"},
            "format_assessment": {"status": "PASS", "checks": []},
            "ai_usage_assessment": {"status": "PASS", "checks": []},
            "input_safety_assessment": {
                "status": "NOT_APPLICABLE",
                "checks": [
                    {"check_id": "S_SUPPORT_ARCHIVE", "status": "NOT_APPLICABLE"}
                ],
            },
            "adjudication": {
                "rule_violation_candidate": False,
                "disqualification_candidate": False,
                "human_adjudication_required": False,
            },
        }

        result = normalize_report(
            raw,
            "gpt-5.6-sol",
            "paper.pdf",
            document_meta={"format": "pdf", "compliance": compliance},
        )

        self.assertTrue(result["quality_review_required"])
        self.assertFalse(result["compliance_review_required"])
        self.assertTrue(result["submission_ready"])
        self.assertTrue(result["human_review_required"])

    def test_prompt_uses_basename_and_never_exposes_absolute_paper_path(self):
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory) / "private-paper.md"
            paper.write_text("摘要：测试。", encoding="utf-8")
            document = read_document(paper)
            prompt = build_prompt(document, load_rubric(), [], [])

        self.assertIn(paper.name, prompt)
        self.assertNotIn(str(paper.resolve()), prompt)
        self.assertNotIn(str(paper.resolve().parent), prompt)


if __name__ == "__main__":
    unittest.main()
