"""
News-Bot 入口 - 集成去重、全文提取、AI 分类摘要

流程: 加载配置 → 异步抓取 → 指纹去重 → 全文提取 → AI 处理 → 校验 → 存入 Redis → 打印

用法:
    python main_fetch.py
"""

import asyncio
import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

# 确保 src 包可被导入
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fetcher.rss_fetcher import fetch_all_feeds
from fetcher.content_cleaner import normalize_whitespace, strip_html_tags, unescape_html
from fetcher.content_extractor import get_best_content
from processor.dedup import dedup_article, mark_seen, store_title_for_fuzzy
from processor.ai_engine import process_article_with_ai, make_fallback_result
from processor.validator import safe_parse_ai_output, validate_ai_output
from storage.redis_client import get_redis_client
from monitor.heartbeat import write_heartbeat

# ── 日志配置 ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── 配置 ──────────────────────────────────────────────────
CONFIG_PATH = PROJECT_ROOT / "config" / "feeds.yaml"
NEWS_TTL_SECONDS = 48 * 3600  # 48 小时
AI_CONCURRENCY = 3


def load_feeds(path: Path) -> list[dict]:
    """从 YAML 文件加载 RSS 源配置。"""
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    feeds = data.get("feeds", [])
    logger.info("已加载 %d 个 RSS 源配置。", len(feeds))
    return feeds


async def process_single_article(
    article: dict,
    source_config: dict,
    ai_semaphore: asyncio.Semaphore,
) -> dict | None:
    """
    处理单篇文章完整流程：去重 → 全文提取 → AI 处理 → 校验 → 存储。

    延迟 ACK：只有全部处理成功后才标记已见。
    """
    title = article.get("title", "(无标题)")
    lang = article.get("source_lang", "en")
    source_name = source_config["name"]

    # ── 1. 去重检查 ──
    should_skip, fingerprint, reason = await dedup_article(article, source_config)
    if should_skip:
        logger.info("⏭️  跳过 [%s]: %s — %s", source_name, title[:40], reason)
        return None

    # ── 2. 全文提取 + 智能截断 ──
    best_content = get_best_content(article, lang)
    cleaned_content = normalize_whitespace(strip_html_tags(unescape_html(best_content)))
    clean_title = normalize_whitespace(strip_html_tags(unescape_html(title)))

    enriched_article = {
        "title": clean_title,
        "link": article.get("link", ""),
        "cleaned_content": cleaned_content,
        "source_lang": lang,
    }

    # ── 3. AI 分类与摘要 ──
    ai_raw = await process_article_with_ai(enriched_article, ai_semaphore)

    if ai_raw:
        ai_result = safe_parse_ai_output(ai_raw)
    else:
        # 所有 Provider 都失败 → 降级结果
        ai_result = make_fallback_result(enriched_article)

    # ── 4. 校验 AI 输出 ──
    ai_result = validate_ai_output(ai_result, source_lang=lang)

    # 合并结果
    final = {
        **enriched_article,
        "ai": ai_result,
        "source_name": source_name,
        "fingerprint": fingerprint,
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }

    # ── 5. 存入 Redis（news:{date}:{uuid}，TTL 48h）──
    redis = get_redis_client()
    if redis.enabled:
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        news_key = f"news:{date_str}:{uuid.uuid4().hex[:8]}"
        await redis.set(news_key, json.dumps(final, ensure_ascii=False), ex=NEWS_TTL_SECONDS)
        logger.debug("已存入 Redis: %s", news_key)

    # ── 6. 延迟 ACK — 全部成功后才标记 ──
    await mark_seen(fingerprint)
    fuzzy_hours = source_config.get("fuzzy_window_hours", 6)
    await store_title_for_fuzzy(source_name, title, fuzzy_hours)

    return final


async def process_source(
    source_name: str,
    articles: list[dict],
    source_config: dict,
    ai_semaphore: asyncio.Semaphore,
) -> list[dict]:
    """处理单个源的所有文章（单篇异常不影响其他文章）。"""
    tasks = [
        process_single_article(article, source_config, ai_semaphore)
        for article in articles
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    processed = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error("❌ [%s] 第 %d 篇处理异常: %s", source_name, i + 1, result)
        elif result is not None:
            processed.append(result)

    return processed


def print_results(all_results: dict[str, list[dict]], stats: dict) -> None:
    """格式化打印含 AI 结果的输出。"""
    total = 0
    for source_name, articles in all_results.items():
        print(f"\n{'═' * 76}")
        print(f"📰 {source_name}  ({len(articles)} 篇)")
        print(f"{'═' * 76}")

        if not articles:
            print("   ⚠️  该源无可用文章（可能已全部去重）。")
            continue

        for i, art in enumerate(articles, 1):
            ai = art.get("ai", {})
            lang_tag = "🇨🇳" if art["source_lang"] == "zh" else "🇬🇧"
            valid_tag = "✅" if ai.get("valid") else "❌"
            importance = ai.get("importance", "?")
            category = ai.get("category", "—")

            print(f"\n  {lang_tag} [{i}] {valid_tag} {art['title']}")
            print(f"      🔗 {art['link']}")
            print(f"      📂 分类: {category}  ⭐ 重要性: {importance}")

            tags = ai.get("tags", [])
            if tags:
                print(f"      🏷️  标签: {', '.join(tags)}")

            headline = ai.get("headline", "")
            if headline:
                print(f"      📌 标题: {headline}")

            # AI 生成的摘要
            summary = ai.get("summary", "")
            if summary:
                display_summary = summary[:300]
                if len(summary) > 300:
                    display_summary += "…"
                print(f"      📝 摘要: {display_summary}")

            # 英文源的原始标题
            original_title = ai.get("original_title")
            if original_title:
                print(f"      🔤 原标题: {original_title}")

            # 无效原因
            if not ai.get("valid"):
                reason = ai.get("reason", "未知")
                print(f"      ⚠️  无效原因: {reason}")

        total += len(articles)

    print(f"\n{'─' * 76}")
    print(f"📊 统计:")
    print(f"   原始文章: {stats['total_raw']} 篇")
    print(f"   去重跳过: {stats['deduped']} 篇")
    print(f"   AI 处理: {stats['ai_processed']} 篇 (降级: {stats['ai_fallback']} 篇)")
    print(f"   处理异常: {stats['errors']} 篇")
    print(f"   最终输出: {total} 篇（来自 {len(all_results)} 个源）")
    print(f"{'─' * 76}\n")


async def main() -> None:
    """主流程：加载配置 → 异步抓取 → 去重 → 全文提取 → AI → 校验 → 存储 → 打印。"""
    logger.info("News-Bot 启动（含去重 & 全文提取 & AI 引擎）…")

    # 1. 加载配置
    feeds = load_feeds(CONFIG_PATH)
    if not feeds:
        logger.error("未找到 RSS 源配置，请检查 %s", CONFIG_PATH)
        return

    feed_map = {cfg["name"]: cfg for cfg in feeds}

    # 2. 异步抓取（Semaphore(3)）
    raw_results = await fetch_all_feeds(feeds, concurrency=3)

    # 3. AI 并发控制
    ai_semaphore = asyncio.Semaphore(AI_CONCURRENCY)

    # 4. 去重 + 全文提取 + AI + 校验 + 存储
    stats = {"total_raw": 0, "deduped": 0, "ai_processed": 0, "ai_fallback": 0, "errors": 0}
    final_results: dict[str, list[dict]] = {}

    for source_name, articles in raw_results.items():
        stats["total_raw"] += len(articles)
        source_config = feed_map.get(source_name, {"name": source_name, "fuzzy_window_hours": 6})

        processed = await process_source(source_name, articles, source_config, ai_semaphore)

        deduped_count = len(articles) - len(processed)
        stats["deduped"] += max(0, deduped_count)

        # 统计 AI 处理情况
        for art in processed:
            ai = art.get("ai", {})
            if ai.get("category") == "uncategorized" and ai.get("summary", "").startswith("AI 服务"):
                stats["ai_fallback"] += 1
            else:
                stats["ai_processed"] += 1

        final_results[source_name] = processed

    # 5. 打印结果
    print_results(final_results, stats)

    # 6. 写入抓取心跳
    await write_heartbeat("fetch")
    logger.info("💓 heartbeat:fetch 已写入。")


if __name__ == "__main__":
    asyncio.run(main())
