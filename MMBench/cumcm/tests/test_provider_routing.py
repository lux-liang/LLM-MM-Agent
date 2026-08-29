import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from MMBench.cumcm import evaluate_paper


def _response(payload='{"dimension_scores": {}}'):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=payload))],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2, total_tokens=3),
    )


class ProviderRoutingTests(unittest.TestCase):
    def test_official_provider_endpoint_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "DeepSeek models"):
            evaluate_paper._profile(
                "deepseek-v4-pro",
                api_key="test-key",
                base_url="https://api.openai.com/v1",
            )
        with self.assertRaisesRegex(ValueError, "OpenAI models"):
            evaluate_paper._profile(
                "gpt-5.6-sol",
                api_key="test-key",
                base_url="https://api.deepseek.com",
            )

    def test_gpt_uses_reasoning_completion_parameters(self):
        fake_openai = Mock()
        fake_openai.return_value.chat.completions.create.return_value = _response()
        with patch.object(evaluate_paper, "OpenAI", fake_openai):
            evaluate_paper.call_model(
                "score this",
                "gpt5.6sol",
                api_key="test-openai-key",
                reasoning_effort="xhigh",
                max_output_tokens=321,
            )
        params = fake_openai.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual(params["model"], "gpt-5.6-sol")
        self.assertEqual(params["max_completion_tokens"], 321)
        self.assertEqual(params["reasoning_effort"], "xhigh")
        self.assertNotIn("max_tokens", params)
        self.assertNotIn("temperature", params)

    def test_gpt_none_is_explicit_not_provider_default(self):
        fake_openai = Mock()
        fake_openai.return_value.chat.completions.create.return_value = _response()
        with patch.object(evaluate_paper, "OpenAI", fake_openai):
            evaluate_paper.call_model(
                "score this",
                "gpt-5.6-sol",
                api_key="test-openai-key",
                reasoning_effort="none",
            )
        params = fake_openai.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual(params["reasoning_effort"], "none")

    def test_deepseek_maps_effort_and_thinking_mode(self):
        fake_openai = Mock()
        fake_openai.return_value.chat.completions.create.return_value = _response()
        with patch.object(evaluate_paper, "OpenAI", fake_openai):
            evaluate_paper.call_model(
                "score this",
                "deepseekv4pro",
                api_key="test-deepseek-key",
                reasoning_effort="xhigh",
                max_output_tokens=654,
            )
        params = fake_openai.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual(params["model"], "deepseek-v4-pro")
        self.assertEqual(params["max_tokens"], 654)
        self.assertEqual(params["reasoning_effort"], "high")
        self.assertEqual(params["extra_body"], {"thinking": {"type": "enabled"}})
        self.assertNotIn("max_completion_tokens", params)

    def test_ensemble_uses_provider_specific_credentials(self):
        calls = []
        raw = {
            "dimension_scores": {
                key: {"score": 20, "feedback": "ok"}
                for key in evaluate_paper.DIMENSION_IDS
            },
            "format_score": 8,
            "evidence": [
                {"dimension": "assumptions_reasonableness", "page": 1, "quote": "本文假设合理明确"},
                {"dimension": "results_correctness", "page": 1, "quote": "本文误差分析完整"},
            ],
            "confidence": 0.9,
        }

        def fake_call(prompt, model, **kwargs):
            calls.append((model, kwargs["api_key"], kwargs["base_url"]))
            return raw

        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory) / "paper.md"
            paper.write_text("本文假设合理明确。本文误差分析完整。", encoding="utf-8")
            with patch.object(evaluate_paper, "call_model", side_effect=fake_call):
                result = evaluate_paper.evaluate_document(
                    paper,
                    ensemble=True,
                    openai_api_key="openai-key",
                    deepseek_api_key="deepseek-key",
                    openai_base_url="https://openai.example/v1",
                    deepseek_base_url="https://deepseek.example",
                )

        self.assertEqual(
            calls,
            [
                ("gpt-5.6-sol", "openai-key", "https://openai.example/v1"),
                ("deepseek-v4-pro", "deepseek-key", "https://deepseek.example"),
            ],
        )
        self.assertTrue(result["model"].startswith("ensemble:"))

    def test_ensemble_recomputes_score_band(self):
        def report(score):
            return {
                "dimension_scores": {key: score for key in evaluate_paper.DIMENSION_IDS},
                "overall_score": score * 4,
                "score_band": "strong learning exemplar",
                "format_score": 8,
                "confidence": 0.9,
                "evidence": [],
                "human_review_reasons": [],
            }

        merged = evaluate_paper._merge_reports(
            [report(23), report(15)], ["gpt-5.6-sol", "deepseek-v4-pro"]
        )
        self.assertEqual(merged["overall_score"], 76)
        self.assertEqual(merged["score_band"], "good structure with targeted weaknesses")


if __name__ == "__main__":
    unittest.main()
