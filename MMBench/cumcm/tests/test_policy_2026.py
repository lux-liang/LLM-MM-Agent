import json
import re
import unittest
from pathlib import Path

from MMBench.cumcm.compliance_2026 import _POLICY_ALIASES


POLICY_PATH = Path(__file__).parents[1] / "policy_2026.json"

EXPECTED_FORMAT_IDS = {f"F{index:02d}" for index in range(1, 32)}
EXPECTED_FORMAT_GOVERNANCE_IDS = {f"FG{index:02d}" for index in range(1, 4)}
EXPECTED_AI_IDS = {f"AI{index:02d}" for index in range(1, 20)}
EXPECTED_REQUIREMENT_IDS = (
    EXPECTED_FORMAT_IDS | EXPECTED_FORMAT_GOVERNANCE_IDS | EXPECTED_AI_IDS
)

EXPECTED_SOURCES = {
    "cumcm-format-2026": {
        "sha256": "CECE4BB3A900A0435160032B98EA26E03B0F2D7ECA58424B0D023E26085AED26",
        "page_count": 2,
    },
    "cumcm-ai-2026": {
        "sha256": "4CF6F30CDD37D6EF2CDB3439C5DBA4D9F207C12D6AAFE24419E81F3C69ACF59A",
        "page_count": 1,
    },
}

EXPECTED_POLICY_ALIASES = {
    "F_ELECTRONIC_FORMAT": ("F24",),
    "F_SINGLE_PAPER_FILE": ("F23",),
    "F_PAPER_NOT_ARCHIVED": ("F27",),
    "F_PAPER_SIZE": ("F26",),
    "F_A4": ("F01",),
    "F_MARGINS": ("F02",),
    "F_PAGINATION": ("F08",),
    "F_PAPER_ELECTRONIC_CONSISTENCY": ("F22",),
    "F_FIRST_PAGE_ABSTRACT": ("F28",),
    "F_FIRST_PAGE_KEYWORDS": ("F06",),
    "F_NO_COMMITMENT_OR_NUMBER_PAGE": ("F28",),
    "F_NO_TOC": ("F10",),
    "F_BODY_PAGE_LIMIT": ("F11",),
    "F_APPENDIX_SUPPORT_LIST": ("F13", "F30"),
    "F_APPENDIX_PROGRAM": ("F14", "F15"),
    "F_ANONYMITY": ("F16",),
    "F_REFERENCES": ("F17",),
    "F_INLINE_CITATIONS": ("F18",),
    "F_SUPPORT_CONTAINER": ("F30",),
    "F_SUPPORT_SIZE": ("F30",),
    "F_SUPPORT_CONTENT_CONSISTENCY": ("F31",),
    "A_DECLARATION_PRESENT": ("AI07",),
    "A_DECLARATION_ORDER": ("AI07",),
    "A_DECLARATION_TEMPLATE": ("AI08", "AI09"),
    "A_DETAILS_FILENAME": ("AI10",),
    "A_DETAILS_PARSEABLE": ("AI10",),
    "A_DETAIL_TOOL": ("AI11",),
    "A_DETAIL_PURPOSE": ("AI12",),
    "A_DETAIL_PROCESS": ("AI13",),
    "A_DETAIL_REVIEW": ("AI14",),
    "A_TRANSPARENCY": ("AI04",),
    "A_TEAM_LED": ("AI05",),
    "A_HUMAN_VERIFICATION": ("AI06",),
    "A_INTENT": ("AI16",),
    "A_ENFORCEMENT": ("AI15",),
}

# These terms make the alias contract semantic rather than merely checking that
# each target ID happens to exist.  For aliases with conditional alternatives,
# every target has its own expected policy wording.
ALIAS_SEMANTIC_TERMS = {
    "F_ELECTRONIC_FORMAT": {"F24": ("PDF", "Word")},
    "F_SINGLE_PAPER_FILE": {"F23": ("独立文件",)},
    "F_PAPER_NOT_ARCHIVED": {"F27": ("文件本身", "不压缩")},
    "F_PAPER_SIZE": {"F26": ("文件大小", "20 MB")},
    "F_A4": {"F01": ("A4",)},
    "F_MARGINS": {"F02": ("页边距", "2.5 厘米")},
    "F_PAGINATION": {"F08": ("页码", "页脚中部")},
    "F_PAPER_ELECTRONIC_CONSISTENCY": {"F22": ("纸质版", "完全一致")},
    "F_FIRST_PAGE_ABSTRACT": {"F28": ("第一页", "摘要专用页")},
    "F_FIRST_PAGE_KEYWORDS": {"F06": ("摘要专用页", "关键词")},
    "F_NO_COMMITMENT_OR_NUMBER_PAGE": {"F28": ("不含承诺书", "编号专用页")},
    "F_NO_TOC": {"F10": ("不设置目录",)},
    "F_BODY_PAGE_LIMIT": {"F11": ("正文", "30 页")},
    "F_APPENDIX_SUPPORT_LIST": {
        "F13": ("附录", "文件列表"),
        "F30": ("文件列表", "附录"),
    },
    "F_APPENDIX_PROGRAM": {
        "F14": ("附录", "源程序代码"),
        "F15": ("附录", "未使用程序"),
    },
    "F_ANONYMITY": {"F16": ("身份", "学校", "赛区")},
    "F_REFERENCES": {"F17": ("参考文献",)},
    "F_INLINE_CITATIONS": {"F18": ("引用位置", "标注")},
    "F_SUPPORT_CONTAINER": {"F30": ("RAR", "ZIP")},
    "F_SUPPORT_SIZE": {"F30": ("20 MB",)},
    "F_SUPPORT_CONTENT_CONSISTENCY": {"F31": ("支撑材料", "一致")},
    "A_DECLARATION_PRESENT": {"AI07": ("AI 工具使用声明",)},
    "A_DECLARATION_ORDER": {"AI07": ("参考文献之前",)},
    "A_DECLARATION_TEMPLATE": {
        "AI08": ("未使用 AI", "官方模板"),
        "AI09": ("使用 AI", "如实说明"),
    },
    "A_DETAILS_FILENAME": {"AI10": ("AI 工具使用详情.pdf",)},
    "A_DETAILS_PARSEABLE": {"AI10": ("PDF", "详情文件")},
    "A_DETAIL_TOOL": {"AI11": ("工具名称", "版本或型号")},
    "A_DETAIL_PURPOSE": {"AI12": ("使用目的", "环节")},
    "A_DETAIL_PROCESS": {"AI13": ("提示方式", "使用过程")},
    "A_DETAIL_REVIEW": {"AI14": ("人工修改", "核验")},
    "A_TRANSPARENCY": {"AI04": ("公开透明",)},
    "A_TEAM_LED": {"AI05": ("核心建模", "参赛队")},
    "A_HUMAN_VERIFICATION": {"AI06": ("人工审查", "核实")},
    "A_INTENT": {"AI16": ("故意隐瞒", "虚假声明")},
    "A_ENFORCEMENT": {"AI15": ("组委会", "处理")},
}


class Policy2026ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        cls.requirements = cls.policy["requirements"]
        cls.requirements_by_id = {item["id"]: item for item in cls.requirements}
        cls.sources_by_id = {item["id"]: item for item in cls.policy["sources"]}

    def test_profile_and_official_source_fingerprints_are_pinned(self):
        self.assertEqual(self.policy["schema_version"], "1.0")
        self.assertEqual(self.policy["profile_id"], "CUMCM-2026")
        self.assertEqual(self.policy["competition_year"], 2026)
        self.assertEqual(set(self.sources_by_id), set(EXPECTED_SOURCES))
        self.assertEqual(len(self.sources_by_id), len(self.policy["sources"]))

        for source_id, expected in EXPECTED_SOURCES.items():
            source = self.sources_by_id[source_id]
            self.assertEqual(source["sha256"], expected["sha256"])
            self.assertRegex(source["sha256"], r"^[0-9A-F]{64}$")
            self.assertEqual(source["page_count"], expected["page_count"])
            self.assertTrue(source["source_authoritative"])
            self.assertFalse(source["repository_copy"])
            self.assertFalse(source["download_allowed"])
            self.assertTrue(source["official_page_url"].startswith("https://www.mcm.edu.cn/"))
            self.assertTrue(source["attachment_url"].startswith("https://www.mcm.edu.cn/"))

    def test_requirement_ids_are_unique_and_complete(self):
        requirement_ids = [item["id"] for item in self.requirements]
        self.assertEqual(len(requirement_ids), 53)
        self.assertEqual(len(requirement_ids), len(set(requirement_ids)))
        self.assertEqual(set(requirement_ids), EXPECTED_REQUIREMENT_IDS)
        self.assertEqual(
            self.policy["requirement_counts"],
            {
                "format_checks": 31,
                "format_governance": 3,
                "ai_rules": 19,
                "total": 53,
            },
        )

    def test_requirement_records_have_valid_fields_enums_and_source_pages(self):
        required_fields = {
            "id",
            "category",
            "summary",
            "severity",
            "check_mode",
            "applies_when",
            "source_id",
            "source_page",
            "clause",
            "remediation",
            "source_clause",
        }
        allowed_severities = {"mandatory", "advisory", "manual"}
        allowed_check_modes = {"deterministic", "model_assisted", "manual"}

        for requirement in self.requirements:
            with self.subTest(requirement=requirement["id"]):
                self.assertEqual(set(requirement), required_fields)
                self.assertIn(requirement["severity"], allowed_severities)
                self.assertIn(requirement["check_mode"], allowed_check_modes)
                for field in required_fields - {"source_page"}:
                    self.assertIsInstance(requirement[field], str)
                    self.assertTrue(requirement[field].strip())

                source = self.sources_by_id[requirement["source_id"]]
                self.assertIsInstance(requirement["source_page"], int)
                self.assertNotIsInstance(requirement["source_page"], bool)
                self.assertGreaterEqual(requirement["source_page"], 1)
                self.assertLessEqual(requirement["source_page"], source["page_count"])

                expected_source = (
                    "cumcm-ai-2026"
                    if requirement["id"].startswith("AI")
                    else "cumcm-format-2026"
                )
                self.assertEqual(requirement["source_id"], expected_source)

    def test_ai10_contains_the_exact_required_details_filename(self):
        ai10 = self.requirements_by_id["AI10"]
        exact_filename = "AI 工具使用详情.pdf"
        self.assertIn(exact_filename, ai10["summary"])
        self.assertIn(exact_filename, ai10["clause"])
        self.assertNotIn("AI工具使用详情.pdf", ai10["summary"])
        self.assertEqual(ai10["severity"], "mandatory")
        self.assertEqual(ai10["check_mode"], "deterministic")
        self.assertEqual(ai10["applies_when"], "ai_used")

    def test_policy_has_guardrails_instead_of_scoring_or_award_thresholds(self):
        forbidden_keys = {
            "score",
            "max_score",
            "min_score",
            "points",
            "deduction",
            "point_deduction",
            "penalty_points",
            "award_threshold",
            "prize_threshold",
            "分值",
            "扣分",
            "奖项阈值",
        }

        def all_keys(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    yield str(key)
                    yield from all_keys(child)
            elif isinstance(value, list):
                for child in value:
                    yield from all_keys(child)

        self.assertTrue(forbidden_keys.isdisjoint(set(all_keys(self.policy))))
        self.assertTrue(self.policy["decision_policy"]["quality_score_independent"])
        self.assertFalse(self.policy["decision_policy"]["automatic_prize_prediction"])
        self.assertIn("不设置或推断官方分值、扣分值、奖项阈值", self.policy["scoring_note"])

        requirement_text = "\n".join(
            str(value)
            for requirement in self.requirements
            for value in requirement.values()
        )
        self.assertIsNone(re.search(r"扣\s*\d+(?:\.\d+)?\s*分", requirement_text))
        self.assertIsNone(
            re.search(r"(?:一等|二等|三等|一等奖|二等奖|三等奖).{0,20}\d+\s*分", requirement_text)
        )

    def test_compliance_aliases_resolve_to_intended_policy_semantics(self):
        self.assertEqual(_POLICY_ALIASES, EXPECTED_POLICY_ALIASES)
        self.assertEqual(set(_POLICY_ALIASES), set(ALIAS_SEMANTIC_TERMS))

        for check_id, aliases in _POLICY_ALIASES.items():
            with self.subTest(check_id=check_id):
                self.assertTrue(aliases)
                self.assertEqual(len(aliases), len(set(aliases)))
                self.assertEqual(set(aliases), set(ALIAS_SEMANTIC_TERMS[check_id]))
                for alias in aliases:
                    requirement = self.requirements_by_id[alias]
                    searchable_text = "\n".join(
                        (requirement["summary"], requirement["clause"])
                    )
                    for term in ALIAS_SEMANTIC_TERMS[check_id][alias]:
                        self.assertIn(term, searchable_text)


if __name__ == "__main__":
    unittest.main()
