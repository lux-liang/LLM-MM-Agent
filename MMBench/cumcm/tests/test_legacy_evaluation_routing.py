import unittest
from unittest.mock import patch

from MMBench.evaluation.model import (
    build_completion_params,
    resolve_provider_config,
)


class LegacyEvaluationRoutingTests(unittest.TestCase):
    def test_reasoning_models_use_provider_specific_parameters(self):
        messages = [{"role": "user", "content": "score"}]
        gpt = build_completion_params(
            messages,
            "gpt5.6sol",
            max_tokens=321,
            temperature=0.7,
            reasoning_effort="none",
        )
        self.assertEqual(gpt["model"], "gpt-5.6-sol")
        self.assertEqual(gpt["max_completion_tokens"], 321)
        self.assertEqual(gpt["reasoning_effort"], "none")
        self.assertNotIn("temperature", gpt)
        self.assertNotIn("max_tokens", gpt)

        deepseek = build_completion_params(
            messages,
            "deepseekv4pro",
            max_tokens=321,
            temperature=0.7,
            reasoning_effort="none",
        )
        self.assertEqual(deepseek["model"], "deepseek-v4-pro")
        self.assertEqual(deepseek["max_tokens"], 321)
        self.assertNotIn("reasoning_effort", deepseek)
        self.assertEqual(
            deepseek["extra_body"], {"thinking": {"type": "disabled"}}
        )
        self.assertNotIn("temperature", deepseek)

    def test_provider_switch_selects_matching_key_and_endpoint(self):
        with patch.dict(
            "os.environ",
            {
                "OPENAI_API_KEY": "openai-test",
                "DEEPSEEK_API_KEY": "deepseek-test",
            },
            clear=True,
        ):
            model, provider, base, key = resolve_provider_config("deepseekv4pro")
            self.assertEqual((model, provider), ("deepseek-v4-pro", "deepseek"))
            self.assertEqual(base, "https://api.deepseek.com")
            self.assertEqual(key, "deepseek-test")

            model, provider, base, key = resolve_provider_config("gpt5.6sol")
            self.assertEqual((model, provider), ("gpt-5.6-sol", "openai"))
            self.assertEqual(base, "https://api.openai.com/v1")
            self.assertEqual(key, "openai-test")


if __name__ == "__main__":
    unittest.main()
