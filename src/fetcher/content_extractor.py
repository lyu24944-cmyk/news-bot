"""
全文提取与智能截断模块

- trafilatura 提取全文
- smart_truncate: 按段落分割、过滤噪音、智能拼接
- 分级策略: RSS summary ≥ 200字直接用 → trafilatura → fallback
- 中英文截断阈值不同 (zh: 1500字, en: 1000词)
"""

import logging
import re

import trafilatura

logger = logging.getLogger(__name__)

# ── 截断阈值 ──────────────────────────────────────────────
MAX_CHARS_ZH = 1500  # 中文按字符
MAX_WORDS_EN = 1000  # 英文按词数
SUMMARY_MIN_LENGTH = 200  # RSS summary 长度阈值

# ── 噪音关键词 ──────────────────────────────────────────
NOISE_KEYWORDS_ZH = ["责任编辑", "版权声明", "相关阅读", "©", "转载请注明", "本文来源"]
NOISE_KEYWORDS_EN = [
    "editor:",
    "copyright",
    "all rights reserved",
    "©",
    "related articles",
    "read more",
    "subscribe",
]
MIN_PARAGRAPH_LENGTH = 10  # 过短段落视为噪音


def _is_noise_paragraph(paragraph: str, lang: str) -> bool:
    """判断段落是否为噪音。"""
    stripped = paragraph.strip()
    if len(stripped) < MIN_PARAGRAPH_LENGTH:
        return True

    keywords = NOISE_KEYWORDS_ZH if lang == "zh" else NOISE_KEYWORDS_EN
    lower = stripped.lower()
    return any(kw in lower for kw in keywords)


def _count_content_length(text: str, lang: str) -> int:
    """根据语言计算内容长度（中文=字符数，英文=词数）。"""
    if lang == "zh":
        return len(text)
    return len(text.split())


def _get_max_length(lang: str) -> int:
    """获取对应语言的截断阈值。"""
    return MAX_CHARS_ZH if lang == "zh" else MAX_WORDS_EN


def smart_truncate(text: str, lang: str = "en", max_length: int | None = None) -> str:
    """
    智能截断文本。

    流程:
    a. 按段落分割
    b. 过滤噪音段落
    c. 总长度 ≤ max_length → 直接返回
    d. 否则取前 70% 段落 + 后 30% 段落，中间加 [...]

    Parameters
    ----------
    text : str  待截断文本
    lang : str  语言 (zh/en)
    max_length : int | None  自定义截断阈值

    Returns
    -------
    str  截断后的文本
    """
    if not text:
        return ""

    if max_length is None:
        max_length = _get_max_length(lang)

    # 1. 按段落分割（双换行或单换行）
    paragraphs = re.split(r"\n{2,}|\n", text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    # 2. 过滤噪音段落
    clean_paragraphs = [p for p in paragraphs if not _is_noise_paragraph(p, lang)]
    if not clean_paragraphs:
        clean_paragraphs = paragraphs  # fallback: 全是噪音时保留原文

    # 3. 合并后检查长度
    full_text = "\n\n".join(clean_paragraphs)
    if _count_content_length(full_text, lang) <= max_length:
        return full_text

    # 4. 智能截断: 前 70% + [...] + 后 30%
    total = len(clean_paragraphs)
    front_count = max(1, int(total * 0.7))
    back_count = max(1, int(total * 0.3))

    # 避免 front + back 超过 total
    if front_count + back_count >= total:
        front_count = total - back_count if back_count < total else total
        back_count = 0

    front = clean_paragraphs[:front_count]
    back = clean_paragraphs[-back_count:] if back_count > 0 else []

    parts = front + ["[...]"] + back if back else front + ["[...]"]
    return "\n\n".join(parts)


def extract_full_text(url: str) -> str | None:
    """
    使用 trafilatura 从 URL 提取全文。

    Parameters
    ----------
    url : str  文章 URL

    Returns
    -------
    str | None  提取的全文（失败返回 None）
    """
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            logger.warning("trafilatura 下载失败: %s", url)
            return None

        text = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=False,
            no_fallback=False,
        )

        if text and len(text.strip()) > 50:
            logger.debug("trafilatura 提取成功: %s (%d 字符)", url, len(text))
            return text.strip()

        logger.warning("trafilatura 提取内容过短: %s", url)
        return None

    except Exception as exc:
        logger.warning("trafilatura 提取异常 [%s]: %s", url, exc)
        return None


def get_best_content(
    article: dict,
    lang: str = "en",
) -> str:
    """
    分级策略获取最佳内容。

    优先级:
    1. RSS summary/raw_content ≥ 200 字 → 直接用
    2. trafilatura 全文提取
    3. fallback 到 summary / title

    Parameters
    ----------
    article : dict  文章字典（需含 raw_content, link, title）
    lang : str  语言

    Returns
    -------
    str  最佳内容（已智能截断）
    """
    raw_content = article.get("raw_content", "")
    link = article.get("link", "")
    title = article.get("title", "")

    # 1. RSS 内容足够长 → 直接用
    if raw_content and _count_content_length(raw_content, lang) >= SUMMARY_MIN_LENGTH:
        logger.debug("RSS 内容足够长，直接使用 (%d)", _count_content_length(raw_content, lang))
        return smart_truncate(raw_content, lang)

    # 2. 尝试 trafilatura 全文提取
    if link:
        full_text = extract_full_text(link)
        if full_text:
            return smart_truncate(full_text, lang)

    # 3. Fallback
    fallback = raw_content if raw_content else title
    logger.debug("使用 fallback 内容: %.50s…", fallback)
    return smart_truncate(fallback, lang)
