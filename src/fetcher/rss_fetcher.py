"""
RSS Fetcher - 异步 RSS 抓取模块

使用 aiohttp + feedparser 异步抓取 RSS 源，
主 URL 失败时自动切换 backup_url，每个源最多取最新 5 篇。
"""

import asyncio
import logging
from typing import Any

import aiohttp
import feedparser

logger = logging.getLogger(__name__)

MAX_ARTICLES_PER_FEED = 5
REQUEST_TIMEOUT = 15  # 秒


async def fetch_feed_xml(
    session: aiohttp.ClientSession,
    url: str,
    timeout: int = REQUEST_TIMEOUT,
) -> str:
    """异步获取 RSS XML 内容。"""
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
        resp.raise_for_status()
        return await resp.text()


async def fetch_single_feed(
    session: aiohttp.ClientSession,
    feed_cfg: dict[str, Any],
    semaphore: asyncio.Semaphore,
) -> list[dict[str, str]]:
    """
    抓取单个 RSS 源，返回结构化文章列表。

    Parameters
    ----------
    session : aiohttp.ClientSession
    feed_cfg : dict  feeds.yaml 中单个源的配置
    semaphore : asyncio.Semaphore  并发控制

    Returns
    -------
    list[dict] 每个字典包含 title, link, raw_content, source_lang
    """
    name = feed_cfg["name"]
    primary_url = feed_cfg["url"]
    backup_url = feed_cfg.get("backup_url")
    lang = feed_cfg.get("lang", "en")

    async with semaphore:
        xml_text: str | None = None

        # 1. 尝试主 URL
        try:
            logger.info("[%s] 正在抓取主 URL: %s", name, primary_url)
            xml_text = await fetch_feed_xml(session, primary_url)
        except Exception as exc:
            logger.warning("[%s] 主 URL 失败 (%s)，尝试备用…", name, exc)

        # 2. 主 URL 失败 → 尝试 backup_url
        if xml_text is None and backup_url:
            try:
                logger.info("[%s] 正在抓取备用 URL: %s", name, backup_url)
                xml_text = await fetch_feed_xml(session, backup_url)
            except Exception as exc:
                logger.error("[%s] 备用 URL 也失败 (%s)，跳过该源。", name, exc)
                return []

        if xml_text is None:
            logger.error("[%s] 无可用 URL，跳过。", name)
            return []

        # 3. 用 feedparser 解析
        parsed = feedparser.parse(xml_text)
        if parsed.bozo and not parsed.entries:
            logger.warning("[%s] feedparser 解析异常: %s", name, parsed.bozo_exception)
            return []

        # 4. 取最新 N 篇
        entries = parsed.entries[:MAX_ARTICLES_PER_FEED]
        articles: list[dict[str, str]] = []

        for entry in entries:
            # 优先 content:encoded → summary → title
            raw_content = ""
            if entry.get("content"):
                raw_content = entry["content"][0].get("value", "")
            if not raw_content:
                raw_content = entry.get("summary", "")
            if not raw_content:
                raw_content = entry.get("title", "")

            articles.append(
                {
                    "title": entry.get("title", "(无标题)"),
                    "link": entry.get("link", ""),
                    "raw_content": raw_content,
                    "source_lang": lang,
                }
            )

        logger.info("[%s] 成功获取 %d 篇文章。", name, len(articles))
        return articles


async def fetch_all_feeds(
    feeds: list[dict[str, Any]],
    concurrency: int = 3,
) -> dict[str, list[dict[str, str]]]:
    """
    异步抓取所有 RSS 源。

    Parameters
    ----------
    feeds : list[dict]  来自 feeds.yaml 的源列表
    concurrency : int  最大并发数

    Returns
    -------
    dict  {source_name: [articles]}
    """
    semaphore = asyncio.Semaphore(concurrency)
    results: dict[str, list[dict[str, str]]] = {}

    async with aiohttp.ClientSession(
        headers={"User-Agent": "news-bot/1.0 (RSS Fetcher)"}
    ) as session:
        tasks = [
            fetch_single_feed(session, cfg, semaphore) for cfg in feeds
        ]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)

        for cfg, result in zip(feeds, gathered):
            name = cfg["name"]
            if isinstance(result, Exception):
                logger.error("[%s] 抓取异常: %s", name, result)
                results[name] = []
            else:
                results[name] = result

    return results
