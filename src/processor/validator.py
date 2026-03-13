"""
AI 输出校验与 JSON 解析模块

- safe_parse_ai_output: 三级 JSON 解析 fallback
- validate_ai_output: 二次验证（长度/复制/幻觉检测）
"""

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── 幻觉标志词 ───────────────────────────────────────────
HALLUCINATION_MARKERS_ZH = ["我认为", "可能是", "大概", "也许", "我觉得", "我猜"]
HALLUCINATION_MARKERS_EN = [
    "i think",
    "probably",
    "maybe",
    "perhaps",
    "i believe",
    "i guess",
    "it seems",
]

MIN_SUMMARY_LENGTH = 15

# ── 默认降级结构 ──────────────────────────────────────────
DEFAULT_RESULT: dict[str, Any] = {
    "valid": True,
    "category": "uncategorized",
    "tags": [],
    "importance": 2,
    "headline": "",
    "summary": "解析失败，使用默认结构。",
}


def _try_parse_json(text: str) -> dict | None:
    """尝试解析 JSON，自动修复常见格式问题。"""
    # 去除 BOM 和不可见字符
    text = text.strip().lstrip("\ufeff")

    # 尝试直接解析
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # 修复: 移除末尾多余逗号 (trailing comma)
    fixed = re.sub(r",\s*([}\]])", r"\1", text)
    try:
        result = json.loads(fixed)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # 修复: 单引号 → 双引号
    fixed2 = text.replace("'", '"')
    try:
        result = json.loads(fixed2)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    return None


def safe_parse_ai_output(text: str | None) -> dict[str, Any]:
    """
    多级 JSON 解析 fallback:

    1. 直接解析（含自动修复）
    2. 提取 ```json``` 代码块后解析
    3. 提取 { ... } 花括号块后解析
    4. 返回默认结构

    Parameters
    ----------
    text : str | None  AI 返回的原始文本

    Returns
    -------
    dict  解析后的字典
    """
    if not text:
        logger.warning("AI 返回为空，使用默认结构。")
        return DEFAULT_RESULT.copy()

    # Level 1: 直接解析（含修复）
    result = _try_parse_json(text.strip())
    if result:
        return result

    # Level 2: 提取 ```json ... ``` 块
    pattern = r"```(?:json)?[\s\n]*(.*?)[\s\n]*```"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        result = _try_parse_json(match.group(1).strip())
        if result:
            logger.debug("从 ```json``` 块中成功解析 JSON。")
            return result

    # Level 3: 提取第一个 { ... } 块（贪婪匹配最外层花括号）
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        candidate = text[brace_start : brace_end + 1]
        result = _try_parse_json(candidate)
        if result:
            logger.debug("从花括号块中成功解析 JSON。")
            return result

    # Level 4: 返回默认结构
    logger.warning("JSON 解析全部失败，使用默认结构。原始文本: %.200s…", text)
    return DEFAULT_RESULT.copy()


def _check_hallucination(text: str, lang: str) -> bool:
    """检测文本中是否包含幻觉标志词。"""
    lower = text.lower()
    markers = HALLUCINATION_MARKERS_ZH if lang == "zh" else HALLUCINATION_MARKERS_EN
    return any(marker in lower for marker in markers)


def validate_ai_output(
    result: dict[str, Any],
    source_lang: str = "zh",
) -> dict[str, Any]:
    """
    AI 说 valid=true 时的二次验证:

    a. summary 长度 ≥ 15 字
    b. headline ≠ summary（防偷懒复制）
    c. 检测幻觉标志词 → 降低 importance

    Parameters
    ----------
    result : dict  AI 解析后的输出
    source_lang : str  源语言

    Returns
    -------
    dict  校验后的结果（可能修改了 importance 或 valid）
    """
    # 非有效内容无需校验
    if not result.get("valid", False):
        return result

    validated = result.copy()
    summary = validated.get("summary", "")
    headline = validated.get("headline", "")

    # a. summary 长度检查
    if len(summary.strip()) < MIN_SUMMARY_LENGTH:
        logger.warning("⚠️  摘要过短 (%d 字): %.30s…", len(summary), summary)
        validated["summary"] = summary if summary else "摘要内容不足。"
        validated["importance"] = min(validated.get("importance", 2), 2)

    # b. headline ≠ summary（防偷懒复制）
    if headline.strip() and headline.strip() == summary.strip():
        logger.warning("⚠️  标题与摘要完全相同，疑似偷懒复制。")
        validated["importance"] = min(validated.get("importance", 2), 2)

    # c. 幻觉检测
    combined = f"{headline} {summary}"
    if _check_hallucination(combined, source_lang):
        logger.warning("⚠️  检测到幻觉标志词，降低 importance。")
        current = validated.get("importance", 3)
        validated["importance"] = max(1, current - 1)

    # 确保字段完整性
    validated.setdefault("category", "uncategorized")
    validated.setdefault("tags", [])
    validated.setdefault("importance", 2)
    validated.setdefault("headline", "")
    validated.setdefault("summary", "")

    return validated
