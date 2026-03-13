"""
新闻摘要格式化器

格式化新闻列表为 Telegram 消息 (Markdown 格式)。
- 顶部标题 + 日期
- 每条新闻：编号 + headline + 评分 + 要点 + 摘要 + 标签
- 外语源显示原文标题
- 附带原文链接
"""

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _importance_stars(score: int) -> str:
    """将重要性评分转为星号。"""
    return "⭐" * min(max(score, 1), 5)


def _format_tags(tags: list[str]) -> str:
    """格式化标签为 #tag 形式。"""
    if not tags:
        return ""
    return " ".join(f"#{t}" for t in tags[:5])


def format_digest(
    news_items: list[dict[str, Any]],
    user_lang: str = "zh",
    source_langs: list[str] | None = None,
) -> str:
    """
    格式化新闻摘要为 Telegram Markdown 消息。

    Parameters
    ----------
    news_items : list[dict]  新闻列表（需含 ai 字段）
    user_lang : str  用户语言偏好
    source_langs : list[str] | None  各新闻源的原始语言（未提供则从新闻中读取）

    Returns
    -------
    str  格式化后的 Markdown 文本
    """
    if not news_items:
        return "📭 暂无匹配的新闻推送。"

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M UTC")

    lines: list[str] = []

    # ── 标题 ──
    lines.append(f"📰 *今日新闻摘要* | {date_str}")
    lines.append(f"⏰ 推送时间: {time_str}")
    lines.append(f"📊 共 {len(news_items)} 条新闻")
    lines.append("")
    lines.append("━" * 30)

    for i, news in enumerate(news_items, 1):
        ai = news.get("ai", {})
        source_lang = news.get("source_lang", "en")
        link = news.get("link", "")

        headline = ai.get("headline", news.get("title", "(无标题)"))
        importance = ai.get("importance", 1)
        category = ai.get("category", "—")
        summary = ai.get("summary", "")
        tags = ai.get("tags", [])

        lines.append("")
        lines.append(f"*{i}. {headline}*")
        lines.append(f"   {_importance_stars(importance)} 重要性: {importance}/5 | 📂 {category}")

        # 摘要
        if summary:
            lines.append(f"   📝 {summary}")

        # 标签
        tag_str = _format_tags(tags)
        if tag_str:
            lines.append(f"   🏷️ {tag_str}")

        # 外语源 → 显示原文标题
        if source_lang != user_lang:
            original = ai.get("original_title", news.get("title", ""))
            if original:
                lines.append(f"   📎 原文标题: _{original}_")

        # 原文链接
        if link:
            lines.append(f"   🔗 [阅读原文]({link})")

        lines.append("")
        lines.append("─" * 30)

    # ── 底部 ──
    lines.append("")
    lines.append("_由 News-Bot 🤖 自动推送_")

    return "\n".join(lines)


def format_alert(message: str) -> str:
    """格式化管理员告警消息。"""
    now = datetime.now(timezone.utc)
    return (
        f"🚨 *News-Bot 告警*\n"
        f"⏰ {now.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        f"{message}"
    )
