import inspect
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from MMBench.cumcm.compliance_2026 import (
    MAX_FILE_SIZE_BYTES,
    assess_2026_compliance,
    load_policy,
)


TEST_POLICY = {
    "profile_id": "CUMCM-2026-test",
    "sources": [],
    # One entry makes the profile itself load-complete while all real checks
    # exercise the module's missing-requirement metadata fallback.
    "requirements": [{"id": "TEST_SENTINEL"}],
}


def _paper(
    *,
    fmt="pdf",
    reliable=True,
    first_page=None,
    body=None,
    appendix=None,
    size=100_000,
):
    first_page = first_page or "测试题目\n摘要：本文建立并检验模型。\n关键词：建模；检验"
    body = body or (
        "一、问题分析\n正文内容。\n"
        "AI 工具使用声明\n"
        "本参赛队在竞赛过程中未使用任何 AI 工具。\n"
        "参考文献\n[1] 测试资料。"
    )
    appendix = appendix or "附录\n本论文没有支撑材料\n本论文没有用到程序"
    page_data = [first_page, body, appendix]
    pages = [
        {
            "page": index,
            "text": text,
            "width_points": 595.28 if fmt == "pdf" else None,
            "height_points": 841.89 if fmt == "pdf" else None,
        }
        for index, text in enumerate(page_data, start=1)
    ]
    return {
        "path": f"C:/private/student/submission.{fmt}",
        "file_name": f"submission.{fmt}",
        "format": fmt,
        "file_size_bytes": size,
        "page_count": len(pages),
        "page_numbers_reliable": reliable,
        "pages": pages,
        "text": "\n\n".join(page_data),
        "extraction_warnings": [],
    }


def _find(report, section, check_id):
    return next(
        item
        for item in report[section]["checks"]
        if item.get("check_id") == check_id or item.get("id") == check_id
    )


def _all_statuses(value):
    result = []
    if isinstance(value, dict):
        if "status" in value:
            result.append(value["status"])
        for item in value.values():
            result.extend(_all_statuses(item))
    elif isinstance(value, list):
        for item in value:
            result.extend(_all_statuses(item))
    return result


def _all_keys(value):
    result = set()
    if isinstance(value, dict):
        result.update(value)
        for item in value.values():
            result.update(_all_keys(item))
    elif isinstance(value, list):
        for item in value:
            result.update(_all_keys(item))
    return result


class Compliance2026Tests(unittest.TestCase):
    def test_no_ai_submission_passes_observable_checks_but_keeps_manual_unknowns(self):
        report = assess_2026_compliance(_paper(), policy=TEST_POLICY)

        self.assertEqual(report["format_assessment"]["status"], "UNKNOWN")
        self.assertEqual(report["ai_usage_assessment"]["status"], "PASS")
        self.assertEqual(report["overall_status"], "UNKNOWN")
        self.assertEqual(report["ai_usage_assessment"]["declared_usage"], "NOT_USED")
        self.assertEqual(
            _find(report, "format_assessment", "F_ELECTRONIC_FORMAT")["status"],
            "PASS",
        )
        self.assertEqual(
            _find(report, "format_assessment", "F_MARGINS")["status"],
            "UNKNOWN",
        )
        self.assertEqual(
            _find(report, "format_assessment", "F_PAGINATION")["status"],
            "UNKNOWN",
        )
        self.assertEqual(
            _find(report, "format_assessment", "F_PAPER_ELECTRONIC_CONSISTENCY")["status"],
            "UNKNOWN",
        )
        self.assertFalse(report["submission_ready"])
        self.assertTrue(report["adjudication"]["human_adjudication_required"])
        self.assertFalse(report["adjudication"]["disqualification_candidate"])
        self.assertNotIn("C:/private", json.dumps(report, ensure_ascii=False))
        self.assertEqual(report["format_assessment"]["paper"], "submission.pdf")
        self.assertTrue(
            set(_all_statuses(report))
            <= {"PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE"}
        )

    def test_clear_violations_fail_without_model_call(self):
        document = _paper(
            fmt="md",
            reliable=False,
            first_page="承诺书\n姓名：张三\n目录",
            body="编号专用页\n正文",
            appendix="附录",
            size=MAX_FILE_SIZE_BYTES + 1,
        )
        report = assess_2026_compliance(document, policy=TEST_POLICY)

        self.assertEqual(report["overall_status"], "FAIL")
        self.assertTrue(report["adjudication"]["rule_violation_candidate"])
        self.assertFalse(report["adjudication"]["disqualification_candidate"])
        self.assertTrue(report["adjudication"]["human_adjudication_required"])
        self.assertFalse(report["submission_ready"])
        self.assertEqual(
            _find(report, "format_assessment", "F_ELECTRONIC_FORMAT")["status"],
            "FAIL",
        )
        self.assertEqual(
            _find(report, "format_assessment", "F_PAPER_SIZE")["status"],
            "FAIL",
        )
        self.assertEqual(
            _find(report, "format_assessment", "F_ANONYMITY")["status"],
            "FAIL",
        )
        self.assertEqual(
            _find(report, "ai_usage_assessment", "A_DECLARATION_PRESENT")["status"],
            "FAIL",
        )

    def test_unrendered_docx_is_unknown_for_layout_and_page_limit(self):
        document = _paper(fmt="docx", reliable=False)
        report = assess_2026_compliance(document, policy=TEST_POLICY)

        self.assertEqual(report["format_assessment"]["status"], "UNKNOWN")
        self.assertEqual(report["overall_status"], "UNKNOWN")
        self.assertEqual(
            _find(report, "format_assessment", "F_A4")["status"], "UNKNOWN"
        )
        self.assertEqual(
            _find(report, "format_assessment", "F_BODY_PAGE_LIMIT")["status"],
            "UNKNOWN",
        )

    def test_unextractable_first_page_does_not_create_false_violations(self):
        document = _paper()
        document["pages"][0]["text"] = ""
        document["pages"][1]["text"] = "正文内容，无可提取的声明页。"
        document["text"] = "正文内容，无可提取的声明页。\n附录\n本论文没有支撑材料\n本论文没有用到程序"
        document["extraction_warnings"] = [
            "one or more PDF pages had no extractable text; extraction is incomplete"
        ]

        report = assess_2026_compliance(document, policy=TEST_POLICY)

        for section, check_id in (
            ("format_assessment", "F_FIRST_PAGE_ABSTRACT"),
            ("format_assessment", "F_FIRST_PAGE_KEYWORDS"),
            ("ai_usage_assessment", "A_DECLARATION_PRESENT"),
            ("ai_usage_assessment", "A_DECLARATION_TEMPLATE"),
        ):
            self.assertEqual(_find(report, section, check_id)["status"], "UNKNOWN")
        self.assertFalse(report["adjudication"]["rule_violation_candidate"])

    def test_zip_parent_traversal_is_rejected_without_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "support.zip"
            with zipfile.ZipFile(archive, "w") as value:
                value.writestr("../outside/evil.py", "print('never execute')")
                value.writestr("result.csv", "x,y\n1,2\n")
            document = _paper(
                appendix=(
                    "附录\n支撑材料文件列表\nsupport.zip\n"
                    "程序代码\ndef solve():\n    return 1"
                )
            )
            report = assess_2026_compliance(
                document,
                supporting_materials=archive,
                policy=TEST_POLICY,
            )

        check = _find(report, "input_safety_assessment", "S_SUPPORT_ARCHIVE")
        self.assertEqual(check["status"], "FAIL")
        self.assertIn("parent_traversal", json.dumps(check, ensure_ascii=False))
        self.assertNotIn("../outside", json.dumps(report, ensure_ascii=False))
        self.assertEqual(report["overall_status"], "UNKNOWN")
        self.assertFalse(report["adjudication"]["rule_violation_candidate"])
        self.assertFalse(report["submission_ready"])

    def test_ai_details_fields_and_language_polishing_exception(self):
        used_body = (
            "一、问题分析\n正文内容。\n"
            "AI 工具使用声明\n"
            "本参赛队在竞赛过程中使用了 AI 工具，主要用于语言润色，"
            "详细使用情况见支撑材料。\n"
            "参考文献\n[1] 测试资料。"
        )
        details = {
            "path": "D:/secret/AI 工具使用详情.pdf",
            "file_name": "AI 工具使用详情.pdf",
            "format": "pdf",
            "pages": [
                {
                    "page": 1,
                    "text": (
                        "AI工具名称：示例工具\n版本或型号：2026\n"
                        "具体使用目的：语言润色\n使用环节：成文\n"
                        "主要提示方式：逐段润色\n使用过程：人工逐段提交"
                    ),
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "support.zip"
            with zipfile.ZipFile(archive, "w") as value:
                value.writestr("AI 工具使用详情.pdf", b"listed only")
            document = _paper(
                body=used_body,
                appendix="附录\n支撑材料文件列表\nAI 工具使用详情.pdf\n本论文没有用到程序",
            )
            report = assess_2026_compliance(
                document,
                supporting_materials=archive,
                ai_details=details,
                policy=TEST_POLICY,
            )

        self.assertEqual(report["ai_usage_assessment"]["declared_usage"], "USED")
        for check_id in (
            "A_DETAILS_FILENAME",
            "A_DETAILS_PARSEABLE",
            "A_DETAIL_TOOL",
            "A_DETAIL_PURPOSE",
            "A_DETAIL_PROCESS",
        ):
            self.assertEqual(
                _find(report, "ai_usage_assessment", check_id)["status"], "PASS"
            )
        self.assertEqual(
            _find(report, "ai_usage_assessment", "A_DETAIL_REVIEW")["status"],
            "NOT_APPLICABLE",
        )
        self.assertEqual(
            _find(report, "ai_usage_assessment", "A_HUMAN_VERIFICATION")["status"],
            "UNKNOWN",
        )
        self.assertEqual(
            _find(report, "ai_usage_assessment", "A_TEAM_LED")["status"],
            "UNKNOWN",
        )
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("D:/secret", serialized)

    def test_ai_details_filename_requires_the_official_space(self):
        used_body = (
            "正文\nAI 工具使用声明\n"
            "本参赛队在竞赛过程中使用了 AI 工具，主要用于代码调试，"
            "详细使用情况见支撑材料。\n参考文献\n[1] 测试资料。"
        )
        details = {
            "file_name": "AI工具使用详情.pdf",
            "format": "pdf",
            "text": "AI工具名称：X；版本：1；使用目的：调试；使用环节：代码；提示方式：问答；使用过程：交互；采纳：无；人工修改：有；核验：有。",
        }
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "support.zip"
            with zipfile.ZipFile(archive, "w") as value:
                value.writestr("AI工具使用详情.pdf", b"wrong name")
            report = assess_2026_compliance(
                _paper(
                    body=used_body,
                    appendix="附录\n支撑材料文件列表\nAI工具使用详情.pdf\n本论文没有用到程序",
                ),
                supporting_materials=archive,
                ai_details=details,
                policy=TEST_POLICY,
            )
        self.assertEqual(
            _find(report, "ai_usage_assessment", "A_DETAILS_FILENAME")["status"],
            "FAIL",
        )

    def test_ai_details_gate_fails_when_file_is_outside_support_archive(self):
        used_body = (
            "正文\nAI 工具使用声明\n"
            "本参赛队在竞赛过程中使用了 AI 工具，主要用于语言润色，"
            "详细使用情况见支撑材料。\n参考文献\n[1] 测试资料。"
        )
        details = {
            "file_name": "AI 工具使用详情.pdf",
            "format": "pdf",
            "text": "AI工具名称：X；版本：1；使用目的：润色；使用环节：写作；提示方式：问答；使用过程：交互。",
        }
        report = assess_2026_compliance(
            _paper(body=used_body), ai_details=details, policy=TEST_POLICY
        )
        filename_check = _find(
            report, "ai_usage_assessment", "A_DETAILS_FILENAME"
        )
        self.assertEqual(filename_check["status"], "FAIL")
        self.assertFalse(filename_check["observed"]["supporting_archive_supplied"])

    def test_external_ai_details_cannot_replace_missing_archive_member(self):
        used_body = (
            "正文\nAI 工具使用声明\n"
            "本参赛队在竞赛过程中使用了 AI 工具，主要用于代码调试，"
            "详细使用情况见支撑材料。\n参考文献\n[1] 测试资料。"
        )
        details = {
            "file_name": "AI 工具使用详情.pdf",
            "format": "pdf",
            "text": "AI工具名称：X；版本：1；使用目的：调试；使用环节：代码；提示方式：问答；使用过程：交互。",
        }
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "support.zip"
            with zipfile.ZipFile(archive, "w") as value:
                value.writestr("results.csv", "x,y\n1,2\n")
            report = assess_2026_compliance(
                _paper(
                    body=used_body,
                    appendix="附录\n支撑材料文件列表\nresults.csv\n本论文没有用到程序",
                ),
                supporting_materials=archive,
                ai_details=details,
                policy=TEST_POLICY,
            )

        self.assertEqual(
            _find(report, "ai_usage_assessment", "A_DETAILS_FILENAME")["status"],
            "FAIL",
        )
        self.assertEqual(
            _find(report, "ai_usage_assessment", "A_DETAIL_TOOL")["status"],
            "UNKNOWN",
        )

    def test_ai_details_member_detection_is_not_limited_to_reported_names(self):
        used_body = (
            "正文\nAI 工具使用声明\n"
            "本参赛队在竞赛过程中使用了 AI 工具，主要用于代码调试，"
            "详细使用情况见支撑材料。\n参考文献\n[1] 测试资料。"
        )
        details = {
            "file_name": "AI 工具使用详情.pdf",
            "format": "pdf",
            "text": "AI工具名称：X；版本：1；使用目的：调试；使用环节：代码；提示方式：问答；使用过程：交互；采纳：部分；人工修改：有；核验：有。",
        }
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "support.zip"
            with zipfile.ZipFile(archive, "w") as value:
                for index in range(101):
                    value.writestr(f"item-{index:03d}.txt", "ok")
                value.writestr("AI 工具使用详情.pdf", b"listed only")
            report = assess_2026_compliance(
                _paper(
                    body=used_body,
                    appendix="附录\n支撑材料文件列表\nAI 工具使用详情.pdf\n本论文没有用到程序",
                ),
                supporting_materials=archive,
                ai_details=details,
                policy=TEST_POLICY,
            )

        self.assertEqual(
            _find(report, "ai_usage_assessment", "A_DETAILS_FILENAME")["status"],
            "PASS",
        )

    def test_empty_ai_details_template_fields_do_not_pass(self):
        used_body = (
            "正文\nAI 工具使用声明\n"
            "本参赛队在竞赛过程中使用了 AI 工具，主要用于代码调试，"
            "详细使用情况见支撑材料。\n参考文献\n[1] 测试资料。"
        )
        details = {
            "file_name": "AI 工具使用详情.pdf",
            "format": "pdf",
            "text": (
                "AI工具名称：\n版本或型号：\n使用目的：\n使用环节：\n"
                "提示方式：\n使用过程：\n采纳：\n人工修改：\n核验：\n主要问题："
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "support.zip"
            with zipfile.ZipFile(archive, "w") as value:
                value.writestr("AI 工具使用详情.pdf", b"listed only")
            report = assess_2026_compliance(
                _paper(
                    body=used_body,
                    appendix="附录\n支撑材料文件列表\nAI 工具使用详情.pdf\n本论文没有用到程序",
                ),
                supporting_materials=archive,
                ai_details=details,
                policy=TEST_POLICY,
            )

        for check_id in (
            "A_DETAIL_TOOL",
            "A_DETAIL_PURPOSE",
            "A_DETAIL_PROCESS",
            "A_DETAIL_REVIEW",
        ):
            self.assertEqual(
                _find(report, "ai_usage_assessment", check_id)["status"],
                "FAIL",
            )

    def test_placeholder_in_used_template_is_not_accepted(self):
        document = _paper(
            body=(
                "正文\nAI 工具使用声明\n"
                "本参赛队在竞赛过程中使用了 AI 工具，主要用于"
                "【简要用途，如语言润色、代码调试等】，详细使用情况见支撑材料。\n"
                "参考文献\n[1] 测试资料。"
            )
        )
        report = assess_2026_compliance(document, policy=TEST_POLICY)
        check = _find(report, "ai_usage_assessment", "A_DECLARATION_TEMPLATE")
        self.assertEqual(check["status"], "FAIL")
        self.assertTrue(check["observed"]["placeholder_found"])

    def test_twenty_mb_boundary_is_inclusive(self):
        at_limit = assess_2026_compliance(
            _paper(size=MAX_FILE_SIZE_BYTES), policy=TEST_POLICY
        )
        over_limit = assess_2026_compliance(
            _paper(size=MAX_FILE_SIZE_BYTES + 1), policy=TEST_POLICY
        )
        binary_twenty_mib = assess_2026_compliance(
            _paper(size=20 * 1024 * 1024), policy=TEST_POLICY
        )
        self.assertEqual(
            _find(at_limit, "format_assessment", "F_PAPER_SIZE")["status"],
            "PASS",
        )
        self.assertEqual(
            _find(over_limit, "format_assessment", "F_PAPER_SIZE")["status"],
            "FAIL",
        )
        self.assertEqual(
            _find(binary_twenty_mib, "format_assessment", "F_PAPER_SIZE")["status"],
            "FAIL",
        )

    def test_policy_failure_is_safe_and_does_not_expose_local_path(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "private" / "missing-policy.json"
            policy = load_policy(missing)
            report = assess_2026_compliance(_paper(), policy=policy)
        self.assertEqual(report["policy_profile"]["status"], "UNKNOWN")
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn(str(missing.parent), serialized)

    def test_quality_scores_are_outside_the_interface_and_report(self):
        signature = inspect.signature(assess_2026_compliance)
        self.assertNotIn("score", signature.parameters)
        report = assess_2026_compliance(_paper(), policy=TEST_POLICY)
        keys = _all_keys(report)
        self.assertFalse({"score", "format_score", "overall_score"} & keys)
        with self.assertRaises(TypeError):
            assess_2026_compliance(_paper(), policy=TEST_POLICY, score=100)


if __name__ == "__main__":
    unittest.main()
