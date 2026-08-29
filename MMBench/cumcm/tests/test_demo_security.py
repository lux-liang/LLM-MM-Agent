import ast
import importlib.util
import logging
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, Optional
from unittest.mock import patch


REPO_ROOT = Path(__file__).parents[3]
SECURITY_PATH = REPO_ROOT / "demo" / "backend" / "app" / "core" / "llm_security.py"
DOWNLOAD_SECURITY_PATH = REPO_ROOT / "demo" / "backend" / "app" / "core" / "download_security.py"


def _load_security_module():
    spec = importlib.util.spec_from_file_location("llm_security_under_test", SECURITY_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


security = _load_security_module()


def _load_download_security_module():
    spec = importlib.util.spec_from_file_location(
        "download_security_under_test", DOWNLOAD_SECURITY_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


download_security = _load_download_security_module()


def _load_demo_parameter_functions():
    source_path = REPO_ROOT / "demo" / "backend" / "app" / "infra" / "gateways" / "llm.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    wanted = {
        "_is_gpt5_family",
        "_normalize_model",
        "_is_deepseek_reasoning",
        "_prepare_completion_kwargs",
    }
    body = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    namespace = {
        "os": os,
        "logger": logging.getLogger("demo-llm-test"),
        "settings": SimpleNamespace(REASONING_EFFORT="high"),
        "normalize_llm_model": security.normalize_llm_model,
        "Dict": Dict,
        "Optional": Optional,
        "_GPT5_UNSUPPORTED_SAMPLING_PARAMS": frozenset(
            {"temperature", "top_p", "frequency_penalty", "presence_penalty"}
        ),
    }
    exec(compile(ast.Module(body=body, type_ignores=[]), str(source_path), "exec"), namespace)
    return namespace["_prepare_completion_kwargs"]


prepare_completion_kwargs = _load_demo_parameter_functions()


class DemoSecurityTests(unittest.TestCase):
    def test_aliases_and_provider_specific_reasoning_effort(self):
        gpt = prepare_completion_kwargs(
            "gpt5.6sol",
            {"reasoning_effort": "xhigh", "max_tokens": 100, "temperature": 0.2},
            temperature=0.2,
        )
        self.assertEqual(gpt["reasoning_effort"], "xhigh")
        self.assertEqual(gpt["max_completion_tokens"], 100)
        self.assertNotIn("temperature", gpt)

        deepseek = prepare_completion_kwargs(
            "deepseekv4pro",
            {"reasoning_effort": "xhigh", "max_tokens": 100},
            temperature=0.2,
        )
        self.assertEqual(deepseek["reasoning_effort"], "high")
        self.assertEqual(deepseek["extra_body"], {"thinking": {"type": "enabled"}})

    def test_outbound_url_allowlist_and_private_dns_guard(self):
        public_dns = [(2, 1, 6, "", ("104.18.0.1", 443))]
        with patch.object(security.socket, "getaddrinfo", return_value=public_dns):
            self.assertEqual(
                security.safe_llm_base_url("https://api.openai.com/v1", "gpt5.6sol"),
                "https://api.openai.com/v1",
            )
            with self.assertRaisesRegex(ValueError, "allowlisted"):
                security.safe_llm_base_url("https://attacker.example/v1", "gpt-5.6-sol")
        private_dns = [(2, 1, 6, "", ("169.254.169.254", 443))]
        with patch.object(security.socket, "getaddrinfo", return_value=private_dns):
            with self.assertRaisesRegex(ValueError, "non-global"):
                security.safe_llm_base_url("https://api.openai.com/v1", "gpt-5.6-sol")

    def test_server_key_is_only_allowed_for_matching_provider(self):
        self.assertTrue(
            security.server_key_allowed_for_target(
                "https://api.deepseek.com", "deepseekv4pro"
            )
        )
        self.assertFalse(
            security.server_key_allowed_for_target(
                "https://openrouter.ai/api/v1", "gpt-5.6-sol"
            )
        )

    def test_asset_download_guard_and_log_redaction(self):
        public_dns = [(2, 1, 6, "", ("52.1.2.3", 443))]
        signed = "https://files.e2b.dev/result.csv?signature=secret-token"
        with patch.object(
            download_security.socket, "getaddrinfo", return_value=public_dns
        ):
            self.assertEqual(download_security.safe_asset_download_url(signed), signed)
            with self.assertRaisesRegex(ValueError, "allowlisted"):
                download_security.safe_asset_download_url("https://attacker.example/a")
        self.assertEqual(
            download_security.asset_url_for_log(signed),
            "https://files.e2b.dev/result.csv",
        )

    def test_security_guards_exist_on_real_call_and_version_paths(self):
        gateway = (
            REPO_ROOT / "demo" / "backend" / "app" / "infra" / "gateways" / "llm.py"
        ).read_text(encoding="utf-8")
        service = (
            REPO_ROOT / "demo" / "backend" / "app" / "services" / "workflow_service.py"
        ).read_text(encoding="utf-8")
        routes = (
            REPO_ROOT / "demo" / "backend" / "app" / "api" / "routes.py"
        ).read_text(encoding="utf-8")
        sandbox = (
            REPO_ROOT / "demo" / "backend" / "app" / "infra" / "gateways" / "sandbox.py"
        ).read_text(encoding="utf-8")
        asset_manager = (
            REPO_ROOT / "demo" / "backend" / "app" / "infra" / "asset_manager.py"
        ).read_text(encoding="utf-8")
        paper_build = (
            REPO_ROOT
            / "demo"
            / "backend"
            / "app"
            / "paper_engine"
            / "templates"
            / "static_files.py"
        ).read_text(encoding="utf-8")
        self.assertGreaterEqual(gateway.count("safe_llm_base_url("), 1)
        self.assertNotIn("placeholder_api_key_for_fallback", gateway)
        self.assertIn("request_key=model_config.apiKey", gateway)
        self.assertNotIn("if model_config.apiKey: api_key", gateway)
        self.assertIn("model_config=ModelConfig(", routes)
        self.assertIn("safe_llm_base_url(base, model)", sandbox)
        self.assertIn("requires a user-supplied API key", sandbox)
        self.assertIn("safe_asset_download_url(url)", asset_manager)
        self.assertNotIn('verify=False', asset_manager)
        self.assertNotIn('shell=True', paper_build)
        self.assertIn("base_version.project_id != pid", service)
        self.assertIn("target_version.project_id != pid", service)
        self.assertIn("version.project_id != project_id", routes)
        self.assertIn("version.node_id != node_id", routes)


if __name__ == "__main__":
    unittest.main()
