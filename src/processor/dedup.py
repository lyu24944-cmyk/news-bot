"""
去重模块 - 指纹去重 + 模糊标题匹配

- make_fingerprint: 标题标准化 → SHA256[:16]
- is_seen / mark_seen: 基于 Redis 的精确去重
- is_similar_title: difflib 模糊匹配，窗口时间从源配置读取
- 延迟 ACK：仅下游处理成功后才调 mark_seen
"""

import difflib
import hashlib
import json
import logging
import re
from typing import Any

from storage.redis_client import get_redis_client

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.75
DEFAULT_TTL_DAYS = 30
RECENT_TITLES_MAX = 50  # 最多保存最近 N 条标题用于模糊比对


def make_fingerprint(title: str) -> str:
    """
    标题标准化 → SHA256 前 16 位十六进制。

    标准化步骤:
    1. 去除所有空白
    2. 转小写
    3. SHA256 哈希
    """
    normalized = re.sub(r"\s+", "", title).lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


async def is_seen(fingerprint: str) -> bool:
    """查询 Redis 中是否已存在该指纹。"""
    client = get_redis_client()
    if not client.enabled:
        return False
    result = await client.get(f"seen:{fingerprint}")
    return result is not None


async def mark_seen(fingerprint: str, ttl_days: int = DEFAULT_TTL_DAYS) -> None:
    """
    将指纹标记为已见（延迟 ACK 模式下，下游处理成功后才调用）。

    Parameters
    ----------
    fingerprint : str  文章指纹
    ttl_days : int  过期天数，默认 30 天
    """
    client = get_redis_client()
    if not client.enabled:
        return
    ttl_seconds = ttl_days * 86400
    await client.set(f"seen:{fingerprint}", "1", ex=ttl_seconds, nx=True)
    logger.debug("已标记指纹: %s (TTL=%d天)", fingerprint, ttl_days)


async def store_title_for_fuzzy(
    source_name: str,
    title: str,
    fuzzy_window_hours: int,
) -> None:
    """将标题存入 Redis 列表，用于后续模糊匹配。"""
    client = get_redis_client()
    if not client.enabled:
        return
    key = f"titles:{source_name}"
    entry = json.dumps({"title": title}, ensure_ascii=False)
    await client.lpush(key, entry)
    # 设置列表过期时间为 fuzzy_window_hours
    await client.expire(key, fuzzy_window_hours * 3600)


async def is_similar_title(
    new_title: str,
    source_config: dict[str, Any],
) -> bool:
    """
    从 Redis 读取近 N 小时标题，用 difflib.SequenceMatcher 做模糊匹配。

    Parameters
    ----------
    new_title : str  新文章标题
    source_config : dict  源配置，需含 name 和 fuzzy_window_hours

    Returns
    -------
    bool  是否与近期标题相似（阈值 0.75）
    """
    client = get_redis_client()
    if not client.enabled:
        return False

    source_name = source_config["name"]
    key = f"titles:{source_name}"

    # 取最近的标题列表
    raw_list = await client.lrange(key, 0, RECENT_TITLES_MAX - 1)
    if not raw_list:
        return False

    normalized_new = re.sub(r"\s+", "", new_title).lower()

    for raw in raw_list:
        try:
            entry = json.loads(raw)
            existing_title = entry.get("title", "")
        except (json.JSONDecodeError, TypeError):
            continue

        normalized_existing = re.sub(r"\s+", "", existing_title).lower()
        ratio = difflib.SequenceMatcher(
            None, normalized_new, normalized_existing
        ).ratio()

        if ratio >= SIMILARITY_THRESHOLD:
            logger.info(
                "模糊匹配命中: '%.30s…' ≈ '%.30s…' (相似度=%.2f)",
                new_title,
                existing_title,
                ratio,
            )
            return True

    return False


async def dedup_article(
    article: dict[str, str],
    source_config: dict[str, Any],
) -> tuple[bool, str, str]:
    """
    对单篇文章执行去重检查。

    Returns
    -------
    (should_skip, fingerprint, reason)
    - should_skip: True 表示应跳过
    - fingerprint: 文章指纹
    - reason: 跳过原因（空字符串表示未跳过）
    """
    title = article.get("title", "")
    fp = make_fingerprint(title)

    # 1. 精确指纹去重
    if await is_seen(fp):
        return True, fp, f"指纹已存在: {fp}"

    # 2. 模糊标题匹配
    if await is_similar_title(title, source_config):
        return True, fp, f"标题与近期文章相似"

    return False, fp, ""
