"""Deterministic compliance checks for the 2026 CUMCM submission rules.

This module deliberately does not score modeling quality and does not call a
language model.  It reports only facts that can be checked conservatively from
the extracted paper and explicitly supplied supporting files.  A ``PASS`` is
therefore narrow: it means that a particular machine-checkable condition was
observed, not that the submission is eligible for an award.

Archives are treated as untrusted input.  ZIP/RAR files are only listed; their
members are never extracted or executed.  Directories are accepted as review
inputs, but are not treated as valid submitted archives.
"""

from __future__ import annotations

import json
import re
import stat
import unicodedata
import zipfile
from itertools import islice
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


HERE = Path(__file__).resolve().parent
POLICY_PATH = HERE / "policy_2026.json"

VALID_STATUSES = frozenset({"PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE"})
MAX_FILE_SIZE_BYTES = 20_000_000
MAX_ARCHIVE_MEMBERS = 5_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 500_000_000
MAX_COMPRESSION_RATIO = 1_000
MAX_AI_DETAILS_PAGES = 100
MAX_AI_DETAILS_TEXT_CHARS = 2_000_000
A4_WIDTH_POINTS = 595.28
A4_HEIGHT_POINTS = 841.89
A4_TOLERANCE_POINTS = 5.0

NO_PROGRAM_DECLARATION = "本论文没有用到程序"
NO_SUPPORT_DECLARATION = "本论文没有支撑材料"
NO_AI_DECLARATION = "本参赛队在竞赛过程中未使用任何AI工具。"
AI_DECLARATION_PREFIX = "本参赛队在竞赛过程中使用了AI工具，主要用于"
AI_DECLARATION_SUFFIX = "，详细使用情况见支撑材料。"
AI_DETAILS_FILENAME = "AI 工具使用详情.pdf"


# The policy file is authoritative when present.  These records are only
# defensive metadata fallbacks, so every check can still be safely rendered if
# a policy entry is absent or a caller supplies a deliberately minimal policy.
_FALLBACK_REQUIREMENTS: Dict[str, Dict[str, Any]] = {
    "F_ELECTRONIC_FORMAT": {
        "clause": "电子版论文必须是一个单独的 PDF 或 Word 文件。",
        "source_page": 1,
        "remediation": "提交单个 PDF 或 DOCX 论文文件；建议使用 PDF。",
    },
    "F_PAPER_SIZE": {
        "clause": "电子版论文文件大小不超过 20MB。",
        "source_page": 1,
        "remediation": "在不损害可读性的前提下将论文压缩到 20,000,000 字节以内。",
    },
    "F_A4": {
        "clause": "论文使用白色 A4 纸。",
        "source_page": 1,
        "remediation": "将所有论文页面设置为 A4；DOCX 请另行渲染核对。",
    },
    "F_MARGINS": {
        "clause": "纸质论文四边页边距均至少为 2.5 厘米。",
        "source_page": 1,
        "remediation": "在最终排版或打印件中人工核对四边页边距均不小于 2.5 厘米。",
    },
    "F_PAGINATION": {
        "clause": "从摘要页开始在页脚中部用阿拉伯数字从 1 连续编号。",
        "source_page": 1,
        "remediation": "人工检查摘要页起始页码、页脚位置以及连续编号。",
    },
    "F_SINGLE_PAPER_FILE": {
        "clause": "电子版论文必须是一个单独文件。",
        "source_page": 1,
        "remediation": "把论文正文和附录合并为一个电子文件。",
    },
    "F_PAPER_NOT_ARCHIVED": {
        "clause": "电子版论文文件本身不要压缩。",
        "source_page": 1,
        "remediation": "直接提交 PDF 或 Word 论文，不要把论文放进压缩包。",
    },
    "F_PAPER_ELECTRONIC_CONSISTENCY": {
        "clause": "电子版论文内容和格式（包括附录）必须与纸质版完全一致。",
        "source_page": 1,
        "remediation": "使用纸质最终稿逐页人工比对电子版正文和附录。",
    },
    "F_FIRST_PAGE_ABSTRACT": {
        "clause": "电子版论文的第一页必须为摘要专用页。",
        "source_page": 1,
        "remediation": "将含标题、摘要和关键词的摘要专用页置于电子版第一页。",
    },
    "F_FIRST_PAGE_KEYWORDS": {
        "clause": "摘要专用页应含标题、摘要和关键词。",
        "source_page": 1,
        "remediation": "在第一页摘要专用页增加明确的“关键词”字段。",
    },
    "F_NO_COMMITMENT_OR_NUMBER_PAGE": {
        "clause": "承诺书和编号专用页不要放在电子版论文中。",
        "source_page": 1,
        "remediation": "从电子版论文中移除承诺书和编号专用页。",
    },
    "F_NO_TOC": {
        "clause": "正文不要目录。",
        "source_page": 1,
        "remediation": "删除论文目录页或目录章节。",
    },
    "F_BODY_PAGE_LIMIT": {
        "clause": "摘要后的正文不超过 30 页；附录页数不限。",
        "source_page": 1,
        "remediation": "将摘要后、附录前的正文控制在 30 页以内。",
    },
    "F_APPENDIX_SUPPORT_LIST": {
        "clause": "附录应含支撑材料文件列表；无支撑材料时使用规定声明。",
        "source_page": 2,
        "remediation": "在附录列出支撑材料文件，或原样写明“本论文没有支撑材料”。",
    },
    "F_APPENDIX_PROGRAM": {
        "clause": "附录应含完整可运行程序；未使用程序时使用规定声明。",
        "source_page": 1,
        "remediation": "在附录附程序，或原样写明“本论文没有用到程序”。",
    },
    "F_ANONYMITY": {
        "clause": "论文及附录不得显示参赛者、学校或赛区身份信息。",
        "source_page": 1,
        "remediation": "删除姓名、学号、学校、赛区、联系方式等身份字段和值。",
    },
    "F_REFERENCES": {
        "clause": "引用他人或公开资料必须列出参考文献并在正文标注。",
        "source_page": 1,
        "remediation": "增加“参考文献”章节，并逐项核对正文引用标注。",
    },
    "F_INLINE_CITATIONS": {
        "clause": "正文引用位置应予以标注。",
        "source_page": 1,
        "remediation": "人工核对正文标注与参考文献条目逐项对应。",
    },
    "F_SUPPORT_CONTAINER": {
        "clause": "支撑材料应压缩为一个后缀为 RAR 或 ZIP 的文件。",
        "source_page": 2,
        "remediation": "将全部支撑材料打包为单个 ZIP 或 RAR 文件。",
    },
    "F_SUPPORT_SIZE": {
        "clause": "支撑材料压缩文件大小不超过 20MB。",
        "source_page": 2,
        "remediation": "将支撑材料包控制在 20,000,000 字节以内。",
    },
    "F_SUPPORT_CONTENT_CONSISTENCY": {
        "clause": "支撑材料应完整支撑论文且与论文内容一致，并保持匿名。",
        "source_page": 2,
        "remediation": "人工核对程序、数据和中间结果与论文的一致性，并检查文件内容和属性中的身份信息。",
    },
    "A_DECLARATION_PRESENT": {
        "clause": "论文应在参考文献之前设置“AI 工具使用声明”。",
        "source_page": 1,
        "remediation": "在参考文献之前增加标题为“AI 工具使用声明”的章节。",
    },
    "A_DECLARATION_ORDER": {
        "clause": "AI 工具使用声明应位于参考文献之前。",
        "source_page": 1,
        "remediation": "将完整 AI 使用声明移动到参考文献之前。",
    },
    "A_DECLARATION_TEMPLATE": {
        "clause": "未使用或已使用 AI 时，应分别采用规定声明文本。",
        "source_page": 1,
        "remediation": "按规定模板填写，并替换所有方括号占位内容。",
    },
    "A_DETAILS_FILENAME": {
        "clause": "使用 AI 时，支撑材料须含名为“AI 工具使用详情.pdf”的 PDF。",
        "source_page": 1,
        "remediation": "提供可解析 PDF，并将其精确命名为“AI 工具使用详情.pdf”。",
    },
    "A_DETAILS_PARSEABLE": {
        "clause": "AI 工具使用详情说明应为 PDF 格式。",
        "source_page": 1,
        "remediation": "重新导出可正常打开并可提取文本的 PDF。",
    },
    "A_DETAIL_TOOL": {
        "clause": "详情应包含所用 AI 工具名称、版本或型号。",
        "source_page": 1,
        "remediation": "补充每项 AI 工具的名称以及版本或型号。",
    },
    "A_DETAIL_PURPOSE": {
        "clause": "详情应包含具体使用目的和环节。",
        "source_page": 1,
        "remediation": "逐项补充使用目的和对应竞赛环节。",
    },
    "A_DETAIL_PROCESS": {
        "clause": "详情应包含主要提示方式与使用过程；典型交互示例可选。",
        "source_page": 1,
        "remediation": "说明主要提示方式和使用过程；无需强制附交互截图。",
    },
    "A_DETAIL_REVIEW": {
        "clause": "详情应包含对 AI 输出的采纳、人工修改和核验情况（语言润色除外）。",
        "source_page": 1,
        "remediation": "补充采纳、人工修改与核验情况；仅纯语言润色可免本项详情。",
    },
    "A_TEAM_LED": {
        "clause": "核心建模与分析应由参赛队主导。",
        "source_page": 1,
        "remediation": "由人工评审结合过程记录裁定，自动检查不得代替该判断。",
    },
    "A_TRANSPARENCY": {
        "clause": "使用 AI 工具应遵循公开透明原则。",
        "source_page": 1,
        "remediation": "由人工结合声明、详情和原始过程记录核对披露是否完整、真实。",
    },
    "A_HUMAN_VERIFICATION": {
        "clause": "AI 参与内容应逐项人工审查与核实。",
        "source_page": 1,
        "remediation": "保留并人工核对审查、修改、复算或验证记录。",
    },
    "A_INTENT": {
        "clause": "是否故意隐瞒、虚假声明属于事实与意图判断。",
        "source_page": 1,
        "remediation": "交由人工依据原始过程记录裁定；本工具不作取消资格结论。",
    },
    "A_ENFORCEMENT": {
        "clause": "是否违反 AI 使用规定及具体后果由组委会依情节裁量。",
        "source_page": 1,
        "remediation": "仅移交人工裁量；自动检查不得作出官方处分。",
    },
}

_POLICY_ALIASES: Dict[str, Tuple[str, ...]] = {
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


def load_policy(path: Optional[Path | str] = None) -> Dict[str, Any]:
    """Load the 2026 policy without raising on a missing/malformed file.

    A safe placeholder is returned if loading fails.  The assessment will mark
    the policy profile ``UNKNOWN`` while still emitting checks using the local
    metadata fallbacks above.
    """

    selected = Path(path) if path is not None else POLICY_PATH
    try:
        value = json.loads(selected.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("policy root is not an object")
        requirements = value.get("requirements", [])
        sources = value.get("sources", [])
        if not isinstance(requirements, list) or not isinstance(sources, list):
            raise ValueError("policy sources/requirements are not arrays")
        result = dict(value)
        result.setdefault("profile_id", "CUMCM-2026")
        result["_policy_loaded"] = True
        result["_policy_issue"] = ""
        return result
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        # Never include the selected absolute path or exception payload in the
        # returned policy/report.  They can contain local usernames or paths.
        return {
            "profile_id": "CUMCM-2026",
            "sources": [],
            "requirements": [],
            "_policy_loaded": False,
            "_policy_issue": type(exc).__name__,
        }


def _compact(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", "", text)


def _plain(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).replace("\r\n", "\n")


def _basename(value: Any) -> str:
    raw = str(value or "").replace("\\", "/").rstrip("/")
    return PurePosixPath(raw).name or "unnamed"


def _safe_observed(value: Any) -> Any:
    """Recursively make report observations path-safe and JSON-friendly."""

    if isinstance(value, Mapping):
        cleaned: Dict[str, Any] = {}
        for key, item in value.items():
            label = str(key)
            if label.lower() in {"path", "file", "filename", "file_name"}:
                cleaned[label] = _basename(item)
            else:
                cleaned[label] = _safe_observed(item)
        return cleaned
    if isinstance(value, (list, tuple, set)):
        return [_safe_observed(item) for item in value]
    if isinstance(value, Path):
        return value.name
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _aggregate_status(checks: Iterable[Mapping[str, Any]]) -> str:
    statuses = {str(item.get("status", "UNKNOWN")) for item in checks}
    if "FAIL" in statuses:
        return "FAIL"
    if "UNKNOWN" in statuses:
        return "UNKNOWN"
    if "PASS" in statuses:
        return "PASS"
    return "NOT_APPLICABLE"


def _requirements_by_id(policy: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    result: Dict[str, Mapping[str, Any]] = {}
    for item in policy.get("requirements", []):
        if isinstance(item, Mapping) and str(item.get("id", "")).strip():
            result[str(item["id"])] = item
    return result


def _find_requirement(
    requirement_id: str,
    policy: Mapping[str, Any],
    aliases: Sequence[str] = (),
) -> Mapping[str, Any]:
    indexed = _requirements_by_id(policy)
    expanded_aliases = (*aliases, *_POLICY_ALIASES.get(requirement_id, ()))
    for candidate in (requirement_id, *expanded_aliases):
        if candidate in indexed:
            return indexed[candidate]
    # Be compatible with policy files that use F01/A01 identifiers but carry
    # a stable semantic key alongside them.
    keys = {requirement_id, *expanded_aliases}
    for item in indexed.values():
        if str(item.get("key", "")) in keys or str(item.get("check_id", "")) in keys:
            return item
    return {}


def _check(
    requirement_id: str,
    status: str,
    observed: Any,
    policy: Mapping[str, Any],
    *,
    aliases: Sequence[str] = (),
) -> Dict[str, Any]:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid compliance status: {status}")
    requirement = _find_requirement(requirement_id, policy, aliases)
    fallback = _FALLBACK_REQUIREMENTS.get(requirement_id, {})
    return {
        "id": str(requirement.get("id") or requirement_id),
        "check_id": requirement_id,
        "clause": str(requirement.get("clause") or fallback.get("clause") or "规则元数据缺失；需人工核对原始政策。"),
        "source_page": requirement.get("source_page", fallback.get("source_page")),
        "status": status,
        "observed": _safe_observed(observed),
        "remediation": str(requirement.get("remediation") or fallback.get("remediation") or "人工核对原始政策并补充证据。"),
    }


def _document_pages(document: Mapping[str, Any]) -> List[Dict[str, Any]]:
    pages: List[Dict[str, Any]] = []
    for index, item in enumerate(document.get("pages") or [], start=1):
        if not isinstance(item, Mapping):
            continue
        try:
            page_number = int(item.get("page", index))
        except (TypeError, ValueError):
            page_number = index
        pages.append(
            {
                "page": page_number,
                "text": _plain(item.get("text", "")),
                "width_points": item.get("width_points"),
                "height_points": item.get("height_points"),
            }
        )
    return pages


def _document_text(document: Mapping[str, Any], pages: Sequence[Mapping[str, Any]]) -> str:
    value = _plain(document.get("text", ""))
    if value.strip():
        return value
    return "\n".join(_plain(page.get("text", "")) for page in pages)


def _document_format(document: Mapping[str, Any]) -> str:
    value = str(document.get("format") or "").lower().lstrip(".")
    if value:
        return value
    return Path(str(document.get("path") or "")).suffix.lower().lstrip(".")


def _has_incomplete_extraction(document: Mapping[str, Any]) -> bool:
    warnings = " ".join(str(item).lower() for item in document.get("extraction_warnings", []))
    return any(marker in warnings for marker in ("truncat", "no extractable", "ocr", "incomplete"))


def _line_heading(text: str, heading: str) -> bool:
    pattern = rf"(?m)^\s*{re.escape(heading)}\s*[:：]?\s*$"
    return bool(re.search(pattern, _plain(text), flags=re.IGNORECASE))


def _find_page(pages: Sequence[Mapping[str, Any]], pattern: str) -> Optional[int]:
    compiled = re.compile(pattern, flags=re.IGNORECASE | re.MULTILINE)
    for item in pages:
        if compiled.search(_plain(item.get("text", ""))):
            return int(item.get("page", 0))
    return None


def _appendix_text(text: str) -> str:
    match = re.search(r"(?m)^\s*附\s*录(?:\s*[A-Z0-9一二三四五六七八九十]*)?\s*[:：]?\s*$", text)
    return text[match.start() :] if match else ""


def _pdf_a4_status(pages: Sequence[Mapping[str, Any]]) -> Tuple[str, Dict[str, Any]]:
    if not pages:
        return "UNKNOWN", {"reason": "没有可检查页面"}
    missing = 0
    non_a4: List[int] = []
    for item in pages:
        try:
            width = float(item.get("width_points"))
            height = float(item.get("height_points"))
        except (TypeError, ValueError):
            missing += 1
            continue
        short, long = sorted((width, height))
        if not (
            abs(short - A4_WIDTH_POINTS) <= A4_TOLERANCE_POINTS
            and abs(long - A4_HEIGHT_POINTS) <= A4_TOLERANCE_POINTS
        ):
            non_a4.append(int(item.get("page", 0)))
    if non_a4:
        return "FAIL", {"non_a4_pages": non_a4[:20], "pages_checked": len(pages)}
    if missing:
        return "UNKNOWN", {"pages_missing_dimensions": missing, "pages_checked": len(pages)}
    return "PASS", {"pages_checked": len(pages), "tolerance_points": A4_TOLERANCE_POINTS}


def _sensitive_labels(text: str) -> List[str]:
    patterns = {
        "participant_name": r"(?:参赛队员|队员|作者|姓名)\s*[:：]\s*\S+",
        "student_id": r"(?:学号|学生证号)\s*[:：]\s*[A-Za-z0-9_-]+",
        "school": r"(?:学校|校名|所在院校|学院)\s*[:：]\s*\S+",
        "competition_region": r"(?:赛区|所在赛区)\s*[:：]\s*\S+",
        "contact": r"(?:联系电话|手机|电子邮箱|邮箱|E-?mail)\s*[:：]\s*\S+",
        "advisor": r"(?:指导教师|指导老师)\s*[:：]\s*\S+",
    }
    return [label for label, pattern in patterns.items() if re.search(pattern, text, re.IGNORECASE)]


def _coerce_support_paths(value: Any) -> List[Path]:
    if value is None:
        return []
    if isinstance(value, (str, Path)):
        return [Path(value)]
    if isinstance(value, Mapping):
        for key in ("path", "archive", "file"):
            if value.get(key):
                return [Path(str(value[key]))]
        return []
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        result: List[Path] = []
        for item in value:
            result.extend(_coerce_support_paths(item))
        return result
    return []


def _unsafe_member_reason(name: str) -> Optional[str]:
    normalized = name.replace("\\", "/")
    if not normalized or "\x00" in normalized:
        return "empty_or_nul_name"
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        return "absolute_path"
    parts = PurePosixPath(normalized).parts
    if ".." in parts:
        return "parent_traversal"
    return None


def _archive_summary(path: Path) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "file": path.name,
        "kind": "directory" if path.is_dir() else path.suffix.lower().lstrip("."),
        "exists": path.exists(),
        "size_bytes": None,
        "member_count": None,
        "member_basenames": [],
        "required_ai_details_filename_found": False,
        "container_recognized": False,
        "member_listing_complete": False,
        "unsafe_member_basenames": [],
        "unsafe_reasons": [],
        "encrypted_members": 0,
        "listing_status": "UNKNOWN",
    }
    if not path.exists():
        summary["reason"] = "input_not_found"
        return summary
    if path.is_dir():
        # A directory is a useful manual-review input, not a compliant delivery
        # container.  Only list immediate basenames and never follow symlinks.
        try:
            entries = list(islice(path.iterdir(), MAX_ARCHIVE_MEMBERS + 1))
            names = [item.name for item in entries[:MAX_ARCHIVE_MEMBERS] if not item.is_symlink()]
            summary["member_count"] = len(entries)
            summary["member_basenames"] = names[:100]
            summary["required_ai_details_filename_found"] = AI_DETAILS_FILENAME in names
            summary["listing_status"] = "UNKNOWN"
            summary["reason"] = "directory_is_review_input_only"
            if len(entries) > MAX_ARCHIVE_MEMBERS:
                summary["unsafe_reasons"].append("too_many_members")
        except OSError:
            summary["reason"] = "directory_listing_failed"
        return summary
    try:
        summary["size_bytes"] = path.stat().st_size
    except OSError:
        summary["reason"] = "stat_failed"
        return summary
    if int(summary["size_bytes"]) > MAX_FILE_SIZE_BYTES:
        summary["reason"] = "archive_exceeds_safe_parse_limit"
        return summary

    suffix = path.suffix.lower()
    if suffix == ".zip":
        try:
            with zipfile.ZipFile(path, "r") as archive:
                infos = archive.infolist()
                summary["container_recognized"] = True
                summary["member_listing_complete"] = len(infos) <= MAX_ARCHIVE_MEMBERS
                if len(infos) > MAX_ARCHIVE_MEMBERS:
                    summary["unsafe_reasons"].append("too_many_members")
                total_uncompressed = 0
                total_compressed = 0
                names: List[str] = []
                unsafe_names: List[str] = []
                for info in infos[: MAX_ARCHIVE_MEMBERS + 1]:
                    name = str(info.filename)
                    base = _basename(name)
                    names.append(base)
                    if base == AI_DETAILS_FILENAME:
                        summary["required_ai_details_filename_found"] = True
                    reason = _unsafe_member_reason(name)
                    unix_mode = (info.external_attr >> 16) & 0xFFFF
                    if stat.S_ISLNK(unix_mode):
                        reason = reason or "symbolic_link"
                    if reason:
                        unsafe_names.append(base)
                        summary["unsafe_reasons"].append(reason)
                    if info.flag_bits & 0x1:
                        summary["encrypted_members"] += 1
                    total_uncompressed += max(0, int(info.file_size))
                    total_compressed += max(0, int(info.compress_size))
                summary["member_count"] = len(infos)
                summary["member_basenames"] = names[:100]
                summary["unsafe_member_basenames"] = unsafe_names[:20]
                summary["total_uncompressed_bytes"] = total_uncompressed
                ratio = total_uncompressed / max(1, total_compressed)
                summary["compression_ratio"] = round(ratio, 2)
                if total_uncompressed > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                    summary["unsafe_reasons"].append("uncompressed_size_limit")
                if ratio > MAX_COMPRESSION_RATIO:
                    summary["unsafe_reasons"].append("compression_ratio_limit")
                summary["unsafe_reasons"] = sorted(set(summary["unsafe_reasons"]))
                summary["listing_status"] = "FAIL" if summary["unsafe_reasons"] else ("UNKNOWN" if summary["encrypted_members"] else "PASS")
        except (OSError, zipfile.BadZipFile, RuntimeError, ValueError):
            summary["reason"] = "zip_listing_failed"
            summary["listing_status"] = "FAIL"
        return summary

    if suffix == ".rar":
        try:
            import rarfile  # type: ignore
        except ImportError:
            summary["reason"] = "rar_listing_dependency_unavailable"
            return summary
        try:
            with rarfile.RarFile(path) as archive:
                infos = archive.infolist()
                summary["container_recognized"] = True
                summary["member_listing_complete"] = len(infos) <= MAX_ARCHIVE_MEMBERS
                if len(infos) > MAX_ARCHIVE_MEMBERS:
                    summary["unsafe_reasons"].append("too_many_members")
                names = []
                unsafe_names = []
                total_uncompressed = 0
                total_compressed = 0
                for info in infos[: MAX_ARCHIVE_MEMBERS + 1]:
                    name = str(getattr(info, "filename", ""))
                    base = _basename(name)
                    names.append(base)
                    if base == AI_DETAILS_FILENAME:
                        summary["required_ai_details_filename_found"] = True
                    reason = _unsafe_member_reason(name)
                    is_symlink = getattr(info, "is_symlink", None)
                    if callable(is_symlink) and is_symlink():
                        reason = reason or "symbolic_link"
                    if reason:
                        unsafe_names.append(base)
                        summary["unsafe_reasons"].append(reason)
                    total_uncompressed += max(0, int(getattr(info, "file_size", 0)))
                    total_compressed += max(0, int(getattr(info, "compress_size", 0)))
                summary["member_count"] = len(infos)
                summary["member_basenames"] = names[:100]
                summary["unsafe_member_basenames"] = unsafe_names[:20]
                ratio = total_uncompressed / max(1, total_compressed)
                summary["total_uncompressed_bytes"] = total_uncompressed
                summary["compression_ratio"] = round(ratio, 2)
                if total_uncompressed > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                    summary["unsafe_reasons"].append("uncompressed_size_limit")
                if ratio > MAX_COMPRESSION_RATIO:
                    summary["unsafe_reasons"].append("compression_ratio_limit")
                summary["unsafe_reasons"] = sorted(set(summary["unsafe_reasons"]))
                summary["listing_status"] = "FAIL" if summary["unsafe_reasons"] else "PASS"
        except Exception as exc:  # rarfile exposes backend-specific exception classes
            summary["reason"] = f"rar_listing_failed:{type(exc).__name__}"
            summary["listing_status"] = "FAIL"
        return summary

    summary["reason"] = "unsupported_container"
    return summary


def _details_document(value: Any) -> Dict[str, Any]:
    if value is None:
        return {"provided": False, "file": "", "format": "", "text": "", "parseable": False, "limit_reason": ""}
    if isinstance(value, Mapping):
        pages = value.get("pages") or []
        text = _plain(value.get("text", ""))
        if not text and isinstance(pages, Sequence):
            text = "\n".join(
                _plain(item.get("text", ""))
                for item in pages[:MAX_AI_DETAILS_PAGES]
                if isinstance(item, Mapping)
            )
        file_name = _basename(value.get("file_name") or value.get("path") or "")
        file_format = str(value.get("format") or Path(file_name).suffix.lstrip(".")).lower()
        too_many_pages = isinstance(pages, Sequence) and len(pages) > MAX_AI_DETAILS_PAGES
        too_much_text = len(text) > MAX_AI_DETAILS_TEXT_CHARS
        text = text[:MAX_AI_DETAILS_TEXT_CHARS]
        limit_reason = "review_copy_limit_exceeded" if too_many_pages or too_much_text else ""
        return {
            "provided": True,
            "file": file_name,
            "format": file_format,
            "text": text,
            "parseable": file_format == "pdf" and bool(_compact(text)) and not limit_reason,
            "limit_reason": limit_reason,
        }
    path = Path(str(value))
    result = {
        "provided": True,
        "file": path.name,
        "format": path.suffix.lower().lstrip("."),
        "text": "",
        "parseable": False,
        "limit_reason": "",
    }
    if path.suffix.lower() != ".pdf" or not path.is_file():
        return result
    try:
        if path.stat().st_size > MAX_FILE_SIZE_BYTES:
            result["limit_reason"] = "review_copy_file_size_limit"
            return result
        from pypdf import PdfReader

        reader = PdfReader(str(path), strict=False)
        page_count = len(reader.pages)
        limited = page_count > MAX_AI_DETAILS_PAGES
        parts: List[str] = []
        char_count = 0
        for index in range(min(page_count, MAX_AI_DETAILS_PAGES)):
            page_text = reader.pages[index].extract_text() or ""
            remaining = MAX_AI_DETAILS_TEXT_CHARS - char_count
            if len(page_text) > remaining:
                parts.append(page_text[: max(0, remaining)])
                limited = True
                break
            parts.append(page_text)
            char_count += len(page_text)
        text = "\n".join(parts)
        result["text"] = text
        result["limit_reason"] = "review_copy_limit_exceeded" if limited else ""
        result["parseable"] = bool(page_count) and bool(_compact(text)) and not limited
    except Exception:
        # Parsing untrusted PDFs must not expose parser messages or local paths.
        pass
    return result


def _declared_ai_mode(text: str) -> Dict[str, Any]:
    compact = _compact(text)
    unused = _compact(NO_AI_DECLARATION) in compact
    pattern = re.compile(
        re.escape(_compact(AI_DECLARATION_PREFIX))
        + r"(.{1,300}?)"
        + re.escape(_compact(AI_DECLARATION_SUFFIX)),
        flags=re.IGNORECASE,
    )
    match = pattern.search(compact)
    purpose = match.group(1) if match else ""
    placeholder = bool(
        purpose
        and (
            "【" in purpose
            or "】" in purpose
            or "[" in purpose
            or "]" in purpose
            or any(marker in purpose for marker in ("简要用途", "请填写", "例如", "如语言润色"))
        )
    )
    if unused and match:
        mode = "CONFLICTING"
    elif unused:
        mode = "NOT_USED"
    elif match:
        mode = "USED"
    else:
        mode = "UNKNOWN"
    language_only = mode == "USED" and purpose in {"语言润色", "文字润色", "语言表达润色"}
    return {
        "mode": mode,
        "purpose": purpose,
        "placeholder": placeholder,
        "language_polishing_only_claimed": language_only,
    }


def _detail_presence(text: str) -> Dict[str, bool]:
    plain = _plain(text)

    def has_value(label_pattern: str) -> bool:
        pattern = re.compile(
            rf"(?:{label_pattern})[ \t]*[:：][ \t]*([^\r\n；;]+)",
            re.IGNORECASE,
        )
        for match in pattern.finditer(plain):
            value = _compact(match.group(1)).strip("。,.，:：-—_")
            if not value:
                continue
            if any(
                marker in value.lower()
                for marker in ("待填写", "请填写", "todo", "tbd", "xxx", "【", "】", "[", "]")
            ):
                continue
            if re.match(
                r"^(?:版本|型号|使用目的|环节|提示方式|提示词|使用过程|采纳|人工修改|核验|主要问题)[:：]",
                value,
            ):
                continue
            return True
        return False

    combined_tool = has_value(r"(?:AI[ \t]*)?工具名称[、,/和及 ]*版本(?:或型号)?")
    tool_name = combined_tool or has_value(r"(?:AI[ \t]*)?工具名称")
    tool_version = combined_tool or has_value(r"版本(?:或型号)?|型号")
    combined_purpose = has_value(r"(?:具体)?使用目的[、,/和及 ]*环节")
    purpose = combined_purpose or has_value(r"(?:具体)?使用目的")
    phase = combined_purpose or has_value(r"(?:使用)?环节")
    combined_process = has_value(
        r"(?:主要)?提示(?:方式|词|方法)[、,/和及 ]*(?:使用|操作|交互)?过程(?:说明)?"
    )
    prompt = combined_process or has_value(r"(?:主要)?提示(?:方式|词|方法)")
    process = combined_process or has_value(r"(?:使用|操作|交互)过程(?:说明)?")
    combined_review = has_value(
        r"(?:对[ \t]*AI[ \t]*输出的?)?采纳[、,/和及 ]*人工(?:修改|修订)[、,/和及 ]*(?:核验|核实|验证)(?:的主要情况)?"
    )
    adoption = combined_review or has_value(r"(?:AI[ \t]*输出)?采纳")
    manual_edit = combined_review or has_value(r"人工(?:修改|修订)")
    verification = combined_review or has_value(r"(?:人工)?(?:核验|核实|验证)")
    main_issue = has_value(r"主要问题|关键问题")
    return {
        "tool_name_and_version": tool_name and tool_version,
        "purpose_and_phase": purpose and phase,
        "prompt_and_process": prompt and process,
        "adoption_edit_verification": adoption and manual_edit and verification,
        "main_issue": main_issue,
    }


def _policy_profile(policy: Mapping[str, Any]) -> Dict[str, Any]:
    loaded = bool(policy.get("_policy_loaded", True))
    requirements = policy.get("requirements", [])
    sources = []
    for item in policy.get("sources", []):
        if not isinstance(item, Mapping):
            continue
        sources.append(
            {
                "id": str(item.get("id", "")),
                "title": str(item.get("title", "")),
                "source_page_count": item.get("page_count"),
                "sha256": str(item.get("sha256", "")),
                "effective_date": item.get("effective_date"),
            }
        )
    status = "PASS" if loaded and isinstance(requirements, list) and bool(requirements) else "UNKNOWN"
    check = {
        "id": "POLICY_PROFILE",
        "clause": "使用 2026 年规则配置进行确定性检查。",
        "source_page": None,
        "status": status,
        "observed": {
            "profile_id": str(policy.get("profile_id") or "CUMCM-2026"),
            "source_count": len(sources),
            "requirement_count": len(requirements) if isinstance(requirements, list) else 0,
            "load_issue": str(policy.get("_policy_issue", "")),
        },
        "remediation": "如状态未知，请提供有效 policy_2026.json 并人工核对附件哈希与生效日期。",
    }
    return {
        "profile_id": str(policy.get("profile_id") or "CUMCM-2026"),
        "status": status,
        "sources": sources,
        "checks": [check],
    }


def assess_2026_compliance(
    document: Mapping[str, Any],
    *,
    supporting_materials: Any = None,
    ai_details: Any = None,
    policy: Optional[Mapping[str, Any] | Path | str] = None,
) -> Dict[str, Any]:
    """Assess deterministic 2026 format and AI-use compliance.

    The return value contains no modeling-quality score.  ``UNKNOWN`` denotes a
    condition requiring rendering, semantic review, or a factual/intent finding
    by a human.  The function never extracts archives or executes submitted
    content.
    """

    if not isinstance(document, Mapping):
        raise TypeError("document must be a mapping produced by read_document")
    if policy is None:
        active_policy: Mapping[str, Any] = load_policy()
    elif isinstance(policy, (str, Path)):
        active_policy = load_policy(policy)
    elif isinstance(policy, Mapping):
        active_policy = policy
    else:
        raise TypeError("policy must be a mapping or path")

    pages = _document_pages(document)
    text = _document_text(document, pages)
    compact = _compact(text)
    file_format = _document_format(document)
    file_name = _basename(document.get("file_name") or document.get("path") or "paper")
    incomplete = _has_incomplete_extraction(document)
    appendix = _appendix_text(text)
    appendix_compact = _compact(appendix)
    support_paths = _coerce_support_paths(supporting_materials)
    support_summaries = [_archive_summary(path) for path in support_paths]
    support_present = bool(support_paths)

    format_checks: List[Dict[str, Any]] = []
    format_checks.append(
        _check(
            "F_ELECTRONIC_FORMAT",
            "PASS" if file_format in {"pdf", "doc", "docx"} else "FAIL",
            {"file": file_name, "format": file_format or "unknown"},
            active_policy,
        )
    )
    format_checks.append(
        _check(
            "F_SINGLE_PAPER_FILE",
            "PASS",
            {"paper_inputs_received": 1, "file": file_name},
            active_policy,
        )
    )
    format_checks.append(
        _check(
            "F_PAPER_NOT_ARCHIVED",
            "FAIL" if file_format in {"zip", "rar"} else "PASS",
            {"direct_paper_format": file_format or "unknown"},
            active_policy,
        )
    )

    size = document.get("file_size_bytes")
    if isinstance(size, bool):
        size = None
    try:
        size_number = int(size) if size is not None else None
    except (TypeError, ValueError, OverflowError):
        size_number = None
    size_status = "UNKNOWN" if size_number is None or size_number < 0 else ("PASS" if size_number <= MAX_FILE_SIZE_BYTES else "FAIL")
    format_checks.append(
        _check(
            "F_PAPER_SIZE",
            size_status,
            {"file": file_name, "size_bytes": size_number, "maximum_bytes": MAX_FILE_SIZE_BYTES},
            active_policy,
        )
    )

    if file_format == "pdf":
        electronic_a4_status, a4_observed = _pdf_a4_status(pages)
        # A non-A4 page box is useful failure evidence, but an A4 page box
        # cannot prove that the paper copy uses white A4 paper or was printed
        # without scaling.
        a4_status = "FAIL" if electronic_a4_status == "FAIL" else "UNKNOWN"
        a4_observed["electronic_page_box_status"] = electronic_a4_status
    elif file_format in {"doc", "docx"}:
        a4_status, a4_observed = "UNKNOWN", {"reason": "Word提取结果不含可靠纸张尺寸"}
    else:
        a4_status, a4_observed = "NOT_APPLICABLE", {"reason": "非合规论文格式"}
    format_checks.append(_check("F_A4", a4_status, a4_observed, active_policy))
    format_checks.append(
        _check(
            "F_MARGINS",
            "UNKNOWN",
            {
                "reason": "页面文本与媒体框不能可靠证明正文到四边的打印页边距",
                "requires_rendered_or_paper_review": True,
            },
            active_policy,
        )
    )

    first_page_text = _plain(pages[0].get("text", "")) if pages else ""
    abstract_present = bool(re.search(r"(?:^|[^参])摘要[:：]?", first_page_text, re.MULTILINE))
    keyword_present = bool(re.search(r"关\s*键\s*词\s*[:：]", first_page_text))
    first_lines = [line.strip() for line in first_page_text.splitlines() if line.strip()]
    title_present = bool(
        first_lines
        and not re.match(r"^(?:摘\s*要|关\s*键\s*词)\s*[:：]?", first_lines[0])
    )
    first_page_status = "PASS" if abstract_present else ("UNKNOWN" if not pages or incomplete else "FAIL")
    abstract_components_present = title_present and abstract_present and keyword_present
    keyword_status = "PASS" if abstract_components_present else ("UNKNOWN" if not pages or incomplete else "FAIL")
    format_checks.append(
        _check(
            "F_FIRST_PAGE_ABSTRACT",
            first_page_status,
            {"first_page_available": bool(pages), "abstract_label_found": abstract_present},
            active_policy,
        )
    )
    format_checks.append(
        _check(
            "F_FIRST_PAGE_KEYWORDS",
            keyword_status,
            {
                "first_page_available": bool(pages),
                "title_line_found": title_present,
                "abstract_label_found": abstract_present,
                "keywords_label_found": keyword_present,
            },
            active_policy,
        )
    )

    forbidden_pages = []
    if re.search(r"(?m)^\s*(?:竞赛)?承\s*诺\s*书\s*$", text):
        forbidden_pages.append("commitment_page")
    if re.search(r"(?m)^\s*编\s*号\s*专\s*用\s*页\s*$", text):
        forbidden_pages.append("numbering_page")
    format_checks.append(
        _check(
            "F_NO_COMMITMENT_OR_NUMBER_PAGE",
            "FAIL" if forbidden_pages else "UNKNOWN",
            {
                "forbidden_page_types": forbidden_pages,
                "absence_in_extract_does_not_cover_images_or_unextractable_text": True,
            },
            active_policy,
        )
    )

    toc_found = _line_heading(text, "目录")
    format_checks.append(
        _check(
            "F_NO_TOC",
            "FAIL" if toc_found else "UNKNOWN",
            {
                "table_of_contents_heading_found": toc_found,
                "absence_requires_rendered_review": not toc_found,
            },
            active_policy,
        )
    )

    abstract_page = _find_page(pages, r"摘要\s*[:：]?")
    appendix_page = _find_page(pages, r"(?m)^\s*附\s*录(?:\s*[A-Z0-9一二三四五六七八九十]*)?\s*[:：]?\s*$")
    reliable_pages = bool(document.get("page_numbers_reliable")) and bool(pages)
    if reliable_pages and abstract_page is not None:
        last_page = max(int(item.get("page", 0)) for item in pages)
        body_end = (appendix_page - 1) if appendix_page and appendix_page > abstract_page else last_page
        body_page_count = max(0, body_end - abstract_page)
        page_limit_status = "PASS" if body_page_count <= 30 else "FAIL"
    else:
        body_page_count = None
        page_limit_status = "UNKNOWN"
    format_checks.append(
        _check(
            "F_BODY_PAGE_LIMIT",
            page_limit_status,
            {
                "page_mapping_reliable": reliable_pages,
                "abstract_page": abstract_page,
                "appendix_page": appendix_page,
                "body_pages_after_abstract_before_appendix": body_page_count,
                "maximum_pages": 30,
            },
            active_policy,
        )
    )
    format_checks.append(
        _check(
            "F_PAGINATION",
            "UNKNOWN",
            {
                "page_mapping_reliable": reliable_pages,
                "reason": "文本提取不能可靠证明页码位于页脚中部且从摘要页起连续编号",
            },
            active_policy,
        )
    )
    format_checks.append(
        _check(
            "F_PAPER_ELECTRONIC_CONSISTENCY",
            "UNKNOWN",
            {
                "paper_copy_supplied_for_comparison": False,
                "reason": "仅收到电子文档，无法逐页比对纸质最终稿",
            },
            active_policy,
        )
    )

    no_support = _compact(NO_SUPPORT_DECLARATION) in appendix_compact
    support_list = bool(
        re.search(r"支撑材料(?:的)?文件(?:列表|清单)|支撑材料文件(?:的)?(?:目录)?清单", appendix_compact)
    )
    if support_present:
        support_list_status = "PASS" if support_list and not no_support else "FAIL"
    else:
        support_list_status = "PASS" if no_support else ("UNKNOWN" if incomplete else "FAIL")
    format_checks.append(
        _check(
            "F_APPENDIX_SUPPORT_LIST",
            support_list_status,
            {
                "appendix_found": bool(appendix),
                "supporting_materials_supplied": support_present,
                "support_list_found": support_list,
                "exact_no_support_declaration_found": no_support,
            },
            active_policy,
            aliases=("F13",) if support_present else ("F30",),
        )
    )

    no_program = _compact(NO_PROGRAM_DECLARATION) in appendix_compact
    program_markers = bool(
        re.search(r"(?:源程序|程序代码|代码清单|完整程序)", appendix_compact)
        or re.search(r"(?m)^\s*(?:def|class|function|import|from)\s+\w+", appendix)
    )
    if no_program and program_markers:
        program_status = "FAIL"
    elif no_program:
        program_status = "PASS"
    elif program_markers:
        # A heading or code-like text proves only presence.  This checker never
        # executes submitted code, so completeness and runnability stay unknown.
        program_status = "UNKNOWN"
    else:
        program_status = "UNKNOWN" if incomplete else "FAIL"
    format_checks.append(
        _check(
            "F_APPENDIX_PROGRAM",
            program_status,
            {
                "appendix_found": bool(appendix),
                "program_content_marker_found": program_markers,
                "exact_no_program_declaration_found": no_program,
            },
            active_policy,
            aliases=("F15",) if no_program else ("F14",),
        )
    )

    sensitive = _sensitive_labels(text)
    format_checks.append(
        _check(
            "F_ANONYMITY",
            "FAIL" if sensitive else "UNKNOWN",
            {
                "sensitive_label_types": sensitive,
                "matched_values_redacted": True,
                "absence_requires_visual_and_metadata_review": not sensitive,
            },
            active_policy,
        )
    )

    references_found = _line_heading(text, "参考文献")
    format_checks.append(
        _check(
            "F_REFERENCES",
            "UNKNOWN",
            {
                "references_heading_found": references_found,
                "external_material_use_requires_semantic_review": True,
                "bibliography_format_requires_review": True,
            },
            active_policy,
        )
    )
    format_checks.append(
        _check(
            "F_INLINE_CITATIONS",
            "UNKNOWN",
            {
                "references_heading_found": references_found,
                "reason": "正文引注与文献表的语义对应关系需要人工逐项核对",
            },
            active_policy,
        )
    )

    if not support_present and no_support:
        container_status = size_pack_status = safe_pack_status = "NOT_APPLICABLE"
        container_observed: Any = {"reason": "固定无支撑材料声明已找到"}
        size_observed: Any = {"reason": "固定无支撑材料声明已找到"}
        safe_observed: Any = {"reason": "固定无支撑材料声明已找到"}
    elif not support_present:
        container_status = size_pack_status = safe_pack_status = "UNKNOWN"
        container_observed = size_observed = safe_observed = {"reason": "未提供支撑材料输入，且未找到固定无支撑声明"}
    else:
        kinds = [item.get("kind") for item in support_summaries]
        if len(support_summaries) != 1:
            container_status = "FAIL"
        else:
            supplied = support_summaries[0]
            if not supplied.get("exists"):
                container_status = "UNKNOWN"
            elif kinds[0] not in {"zip", "rar"}:
                container_status = "FAIL"
            elif supplied.get("container_recognized"):
                container_status = "PASS"
            elif supplied.get("reason") == "zip_listing_failed":
                container_status = "FAIL"
            else:
                container_status = "UNKNOWN"
        container_observed = {"files": [item.get("file") for item in support_summaries], "kinds": kinds, "single_archive_required": True}
        size_values = [item.get("size_bytes") for item in support_summaries]
        if any(value is None for value in size_values):
            size_pack_status = "UNKNOWN"
        else:
            size_pack_status = "PASS" if len(size_values) == 1 and int(size_values[0]) <= MAX_FILE_SIZE_BYTES else "FAIL"
        size_observed = {"files": [{"file": item.get("file"), "size_bytes": item.get("size_bytes")} for item in support_summaries], "maximum_bytes": MAX_FILE_SIZE_BYTES}
        listing_statuses = [str(item.get("listing_status", "UNKNOWN")) for item in support_summaries]
        safe_pack_status = "FAIL" if "FAIL" in listing_statuses else ("UNKNOWN" if "UNKNOWN" in listing_statuses else "PASS")
        safe_observed = {
            "files": [
                {
                    "file": item.get("file"),
                    "member_count": item.get("member_count"),
                    "unsafe_member_basenames": item.get("unsafe_member_basenames", []),
                    "unsafe_reasons": item.get("unsafe_reasons", []),
                    "encrypted_members": item.get("encrypted_members", 0),
                    "listing_status": item.get("listing_status"),
                }
                for item in support_summaries
            ],
            "members_were_extracted": False,
        }
    format_checks.append(_check("F_SUPPORT_CONTAINER", container_status, container_observed, active_policy))
    format_checks.append(_check("F_SUPPORT_SIZE", size_pack_status, size_observed, active_policy))
    input_safety_assessment = {
        "status": safe_pack_status,
        "checks": [
            {
                "id": "S_SUPPORT_ARCHIVE",
                "check_id": "S_SUPPORT_ARCHIVE",
                "clause": "本地安全边界：只列出压缩包成员，不解压、不执行，并拒绝危险路径或异常膨胀。",
                "source_page": None,
                "status": safe_pack_status,
                "observed": _safe_observed(safe_observed),
                "remediation": "重建压缩包，移除路径穿越、符号链接、加密或异常膨胀成员后再检查。",
                "policy_scope": False,
            }
        ],
        "note": "这是评测工具的输入安全状态，不是竞赛组委会条款或处分结论。",
    }
    format_checks.append(
        _check(
            "F_SUPPORT_CONTENT_CONSISTENCY",
            "UNKNOWN" if support_present else "NOT_APPLICABLE",
            {
                "supporting_materials_supplied": support_present,
                "archives_extracted_or_executed": False,
                "reason": "压缩包仅安全列名，内容一致性、完整性及非文本身份信息需人工核对",
            },
            active_policy,
        )
    )

    declaration = _declared_ai_mode(text)
    ai_checks: List[Dict[str, Any]] = []
    declaration_heading = bool(re.search(r"AI\s*工具使用声明", text, re.IGNORECASE))
    ai_checks.append(
        _check(
            "A_DECLARATION_PRESENT",
            "PASS" if declaration_heading else ("UNKNOWN" if incomplete else "FAIL"),
            {"heading_found": declaration_heading},
            active_policy,
        )
    )

    ai_position = compact.lower().find(_compact("AI工具使用声明").lower())
    refs_position = compact.find(_compact("参考文献"))
    if ai_position < 0:
        order_status = "FAIL" if not incomplete else "UNKNOWN"
    elif refs_position < 0:
        order_status = "UNKNOWN"
    else:
        order_status = "PASS" if ai_position < refs_position else "FAIL"
    ai_checks.append(
        _check(
            "A_DECLARATION_ORDER",
            order_status,
            {"declaration_before_references": ai_position >= 0 and refs_position >= 0 and ai_position < refs_position},
            active_policy,
        )
    )

    if declaration["mode"] in {"NOT_USED", "USED"} and not declaration["placeholder"]:
        template_status = "PASS"
    elif declaration["mode"] in {"CONFLICTING", "UNKNOWN"} or declaration["placeholder"]:
        template_status = "FAIL" if not incomplete else "UNKNOWN"
    else:
        template_status = "UNKNOWN"
    ai_checks.append(
        _check(
            "A_DECLARATION_TEMPLATE",
            template_status,
            {
                "declared_mode": declaration["mode"],
                "placeholder_found": declaration["placeholder"],
                "purpose_length": len(str(declaration["purpose"])),
            },
            active_policy,
            aliases=("AI09",) if declaration["mode"] == "USED" else ("AI08",),
        )
    )

    details = _details_document(ai_details)
    archive_summaries = [
        summary for summary in support_summaries if summary.get("kind") in {"zip", "rar"}
    ]
    exact_in_archive = any(
        summary.get("kind") in {"zip", "rar"}
        and summary.get("required_ai_details_filename_found") is True
        for summary in archive_summaries
    )
    exact_absence_confirmed = bool(archive_summaries) and all(
        summary.get("member_listing_complete") is True
        for summary in archive_summaries
    )
    details_exact_name = details["provided"] and details["file"] == AI_DETAILS_FILENAME
    used = declaration["mode"] == "USED"
    unused = declaration["mode"] == "NOT_USED"

    if unused:
        filename_status = parse_status = "NOT_APPLICABLE"
    elif used:
        # The formal package itself must contain the exact filename.  A
        # separately supplied --ai-details file is only a review copy and can
        # never prove that the submitted archive contains it.
        if exact_in_archive:
            filename_status = "PASS"
        elif not support_present or exact_absence_confirmed:
            filename_status = "FAIL"
        else:
            filename_status = "UNKNOWN"
        if not exact_in_archive or not details["provided"] or not details_exact_name:
            parse_status = "UNKNOWN"
        else:
            # A local parse failure or safety limit is lack of evidence, not
            # proof that the archived PDF violates the contest rule.
            parse_status = "PASS" if details["parseable"] else "UNKNOWN"
    else:
        filename_status = parse_status = "UNKNOWN"
    ai_checks.append(
        _check(
            "A_DETAILS_FILENAME",
            filename_status,
            {
                "expected_file": AI_DETAILS_FILENAME,
                "provided_file": details["file"] or None,
                "provided_file_exact_name": bool(details_exact_name),
                "exact_member_found_in_archive": exact_in_archive,
            },
            active_policy,
        )
    )
    ai_checks.append(
        _check(
            "A_DETAILS_PARSEABLE",
            parse_status,
            {
                "format": details["format"] or None,
                "parseable_pdf_text": details["parseable"],
                "limit_reason": details.get("limit_reason") or None,
            },
            active_policy,
        )
    )

    review_copy_matches_archive_evidence = bool(
        exact_in_archive and details_exact_name and details["parseable"]
    )
    detail_flags = _detail_presence(details["text"]) if review_copy_matches_archive_evidence else {
        "tool_name_and_version": False,
        "purpose_and_phase": False,
        "prompt_and_process": False,
        "adoption_edit_verification": False,
        "main_issue": False,
    }
    detail_specs = (
        ("A_DETAIL_TOOL", "tool_name_and_version"),
        ("A_DETAIL_PURPOSE", "purpose_and_phase"),
        ("A_DETAIL_PROCESS", "prompt_and_process"),
    )
    for requirement_id, flag in detail_specs:
        if unused:
            status_value = "NOT_APPLICABLE"
        elif not used or not review_copy_matches_archive_evidence:
            status_value = "UNKNOWN"
        else:
            status_value = "PASS" if detail_flags[flag] else "FAIL"
        ai_checks.append(_check(requirement_id, status_value, {"required_fields_found": detail_flags[flag]}, active_policy))

    if unused:
        review_detail_status = "NOT_APPLICABLE"
    elif declaration["language_polishing_only_claimed"]:
        review_detail_status = "NOT_APPLICABLE"
    elif not used or not review_copy_matches_archive_evidence:
        review_detail_status = "UNKNOWN"
    else:
        if not detail_flags["adoption_edit_verification"]:
            review_detail_status = "FAIL"
        elif detail_flags["main_issue"]:
            review_detail_status = "PASS"
        else:
            review_detail_status = "UNKNOWN"
    ai_checks.append(
        _check(
            "A_DETAIL_REVIEW",
            review_detail_status,
            {
                "required_fields_found": detail_flags["adoption_edit_verification"],
                "main_issue_explained": detail_flags["main_issue"],
                "language_polishing_only_claimed": declaration["language_polishing_only_claimed"],
            },
            active_policy,
        )
    )

    # These are substantive factual judgments.  Even a detailed declaration is
    # evidence for a human, not proof.  Pure language polishing only exempts the
    # fourth *detail-field* requirement; it never proves actual human review.
    manual_status = "NOT_APPLICABLE" if unused else "UNKNOWN"
    ai_checks.append(
        _check(
            "A_TRANSPARENCY",
            manual_status,
            {
                "declared_mode": declaration["mode"],
                "formal_disclosure_checks_completed": True,
                "truthfulness_requires_human_adjudication": not unused,
            },
            active_policy,
        )
    )
    ai_checks.append(
        _check(
            "A_TEAM_LED",
            manual_status,
            {"declared_mode": declaration["mode"], "automatic_finding_made": False},
            active_policy,
        )
    )
    ai_checks.append(
        _check(
            "A_HUMAN_VERIFICATION",
            manual_status,
            {
                "declared_mode": declaration["mode"],
                "details_claim_review_fields": detail_flags["adoption_edit_verification"],
                "requires_human_adjudication": not unused,
            },
            active_policy,
        )
    )
    intent_needed = not unused or (unused and (details["provided"] or exact_in_archive))
    ai_checks.append(
        _check(
            "A_INTENT",
            "UNKNOWN" if intent_needed else "NOT_APPLICABLE",
            {"potential_inconsistency": intent_needed, "automatic_disqualification_finding_made": False},
            active_policy,
        )
    )

    ai_failures_before_enforcement = any(item.get("status") == "FAIL" for item in ai_checks)
    ai_checks.append(
        _check(
            "A_ENFORCEMENT",
            "UNKNOWN" if ai_failures_before_enforcement else "NOT_APPLICABLE",
            {
                "formal_failure_candidate_found": ai_failures_before_enforcement,
                "official_consequence_determined": False,
                "requires_human_adjudication": ai_failures_before_enforcement,
            },
            active_policy,
        )
    )

    # An AI-used declaration makes a support archive mandatory regardless of a
    # general no-support statement.  Represent this as a deterministic filename
    # failure above; never infer concealment or award eligibility.
    if used and not support_present:
        for item in ai_checks:
            if item.get("check_id") == "A_DETAILS_FILENAME":
                item["status"] = "FAIL"
                item["observed"]["supporting_archive_supplied"] = False
    ai_failure_candidate = any(
        item.get("status") == "FAIL" and item.get("check_id") != "A_ENFORCEMENT"
        for item in ai_checks
    )
    for item in ai_checks:
        if item.get("check_id") == "A_ENFORCEMENT":
            item["status"] = "UNKNOWN" if ai_failure_candidate else "NOT_APPLICABLE"
            item["observed"]["formal_failure_candidate_found"] = ai_failure_candidate
            item["observed"]["requires_human_adjudication"] = ai_failure_candidate

    format_assessment = {
        "status": _aggregate_status(format_checks),
        "paper": file_name,
        "checks": format_checks,
    }
    ai_assessment = {
        "status": _aggregate_status(ai_checks),
        "declared_usage": declaration["mode"],
        "checks": ai_checks,
    }
    profile = _policy_profile(active_policy)
    overall_status = _aggregate_status(
        [
            {"status": profile["status"]},
            {"status": format_assessment["status"]},
            {"status": ai_assessment["status"]},
        ]
    )
    rule_violation_candidate = any(
        item.get("status") == "FAIL" for item in (*format_checks, *ai_checks)
    )
    human_adjudication_required = overall_status in {"FAIL", "UNKNOWN"}
    if input_safety_assessment["status"] in {"FAIL", "UNKNOWN"}:
        human_adjudication_required = True
    return {
        "schema_version": "2026.1",
        "overall_status": overall_status,
        "policy_profile": profile,
        "format_assessment": format_assessment,
        "ai_usage_assessment": ai_assessment,
        "input_safety_assessment": input_safety_assessment,
        "adjudication": {
            "status": "UNKNOWN" if human_adjudication_required else "NOT_APPLICABLE",
            "rule_violation_candidate": rule_violation_candidate,
            "disqualification_candidate": False,
            "human_adjudication_required": human_adjudication_required,
        },
        "submission_ready": (
            overall_status == "PASS"
            and input_safety_assessment["status"] in {"PASS", "NOT_APPLICABLE"}
            and not human_adjudication_required
        ),
        "disclaimer": "仅为确定性合规预检；不评价建模质量，不预测奖项，不自动作取消资格结论。",
    }


__all__ = ["assess_2026_compliance", "load_policy"]
