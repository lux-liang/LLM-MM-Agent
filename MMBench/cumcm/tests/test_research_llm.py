import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from MMAgent.llm import llm as llm_module


class ResearchLLMRoutingTests(unittest.TestCase):
    def _client_without_sdk(self, model, effort="high"):
        instance = llm_module.LLM.__new__(llm_module.LLM)
        instance.profile = llm_module.resolve_model_profile(model, reasoning_effort=effort)
        instance.model_name = instance.profile.model
        instance.reasoning_effort = instance.profile.reasoning_effort
        return instance

    def test_gpt_and_deepseek_token_parameters(self):
        gpt = self._client_without_sdk("gpt5.6sol", "xhigh")
        gpt_params = gpt._request_params("prompt", "system", max_tokens=100)
        self.assertEqual(gpt_params["max_completion_tokens"], 100)
        self.assertEqual(gpt_params["reasoning_effort"], "xhigh")
        self.assertNotIn("temperature", gpt_params)

        deepseek = self._client_without_sdk("deepseekv4pro", "medium")
        deepseek_params = deepseek._request_params("prompt", "system", max_tokens=100)
        self.assertEqual(deepseek_params["max_tokens"], 100)
        self.assertEqual(deepseek_params["reasoning_effort"], "high")
        self.assertEqual(
            deepseek_params["extra_body"], {"thinking": {"type": "enabled"}}
        )

    def test_official_provider_endpoint_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "DeepSeek models"):
            llm_module.resolve_model_profile(
                "deepseek-v4-pro", api_base="https://api.openai.com/v1"
            )
        with self.assertRaisesRegex(ValueError, "OpenAI models"):
            llm_module.resolve_model_profile(
                "gpt-5.6-sol", api_base="https://api.deepseek.com"
            )

    def test_switching_provider_reselects_base_url_and_key(self):
        instance = self._client_without_sdk("gpt5.6sol")
        instance.api_key = "old-openai-key"
        instance.api_base = instance.profile.base_url
        fake_sdk = SimpleNamespace(OpenAI=lambda **kwargs: kwargs)
        with patch.object(llm_module, "openai", fake_sdk), patch.dict(
            os.environ,
            {"DEEPSEEK_API_KEY": "deepseek-key", "DEEPSEEK_BASE_URL": "https://api.deepseek.com"},
            clear=False,
        ):
            instance.reset(model_name="deepseekv4pro")

        self.assertEqual(instance.profile.provider, "deepseek")
        self.assertEqual(instance.api_base, "https://api.deepseek.com")
        self.assertEqual(instance.api_key, "deepseek-key")

    def test_gpt_none_effort_is_sent_explicitly(self):
        gpt = self._client_without_sdk("gpt5.6sol", "none")
        params = gpt._request_params("prompt", "system")
        self.assertEqual(params["reasoning_effort"], "none")

    def test_deepseek_none_uses_thinking_switch_without_invalid_effort(self):
        deepseek = self._client_without_sdk("deepseekv4pro", "none")
        params = deepseek._request_params("prompt", "system")
        self.assertNotIn("reasoning_effort", params)
        self.assertEqual(params["extra_body"], {"thinking": {"type": "disabled"}})


if __name__ == "__main__":
    unittest.main()
