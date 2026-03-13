"""
推送调度器 - 用户过滤与新闻匹配

- get_all_users(): 从 Redis 读取所有 user:*:prefs
- filter_news_for_user(): 多维过滤（时间/分类/重要性/已推送）
- 结果按 importance 降序，取 Top 8
"""

import json
import logging
from typing import Any

from storage.redis_client import get_redis_client

logger = logging.getLogger(__name__)

MAX_NEWS_PER_PUSH = 8


async def get_all_users() -> list[dict[str, Any]]:
    """
    从 Redis 读取所有用户偏好。

    用户偏好存储格式: user:{chat_id}:prefs → JSON
    {
        "chat_id": "123456",
        "lang": "zh",
        "categories": ["科技", "财经", "国际"],
        "min_importance": 3,
        "push_times_utc": [1, 9, 17],
        "timezone": "Asia/Shanghai"
    }

    Returns
    -------
    list[dict]  用户偏好列表
    """
    redis = get_redis_client()
    if not redis.enabled:
        logger.warning("Redis 未启用，返回空用户列表。")
        return []

    # 扫描 user:*:prefs 键
    # Upstash REST API 不直接支持 SCAN，使用预存索引
    raw = await redis.get("users:index")
    if not raw:
        logger.info("未找到用户索引 (users:index)，无用户可推送。")
        return []

    try:
        chat_ids = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.error("users:index 解析失败。")
        return []

    users = []
    for chat_id in chat_ids:
        prefs_raw = await redis.get(f"user:{chat_id}:prefs")
        if prefs_raw:
            try:
                prefs = json.loads(prefs_raw)
                prefs.setdefault("chat_id", str(chat_id))
                users.append(prefs)
            except (json.JSONDecodeError, TypeError):
                logger.warning("用户 %s 偏好解析失败，跳过。", chat_id)
                continue

    logger.info("加载了 %d 个用户配置。", len(users))
    return users


async def _is_already_pushed(chat_id: str, news_id: str) -> bool:
    """检查该新闻是否已推送给该用户。"""
    redis = get_redis_client()
    if not redis.enabled:
        return False
    result = await redis.get(f"push:{chat_id}:{news_id}")
    return result is not None


async def filter_news_for_user(
    news_list: list[dict[str, Any]],
    user_prefs: dict[str, Any],
    current_utc_hour: int,
) -> list[dict[str, Any]]:
    """
    根据用户偏好过滤新闻。

    过滤条件:
    a. 当前 UTC 小时 ∈ 用户 push_times_utc
    b. category ∈ 用户 categories
    c. importance ≥ 用户 min_importance
    d. push:{chatId}:{newsId} 不存在（未推送过）

    结果按 importance 降序，取 Top 8。

    Parameters
    ----------
    news_list : list[dict]  全部新闻
    user_prefs : dict  用户偏好
    current_utc_hour : int  当前 UTC 小时 (0-23)

    Returns
    -------
    list[dict]  过滤后的新闻列表（最多 8 篇）
    """
    chat_id = str(user_prefs.get("chat_id", ""))
    push_times = user_prefs.get("push_times_utc", [])
    categories = [c.lower() for c in user_prefs.get("categories", [])]
    min_importance = user_prefs.get("min_importance", 1)

    # a. 时间窗口检查
    #    push_times 可能是 int 列表 [0, 6, 12] 或字符串列表 ["00:00", "06:00"]
    if push_times:
        allowed_hours = set()
        for t in push_times:
            if isinstance(t, int):
                allowed_hours.add(t)
            elif isinstance(t, str) and ":" in t:
                try:
                    allowed_hours.add(int(t.split(":")[0]))
                except ValueError:
                    pass
            elif isinstance(t, str):
                try:
                    allowed_hours.add(int(t))
                except ValueError:
                    pass

        if allowed_hours and current_utc_hour not in allowed_hours:
            logger.debug("用户 %s: 当前 UTC %dh 不在推送时段 %s", chat_id, current_utc_hour, allowed_hours)
            return []

    filtered = []

    for news in news_list:
        ai = news.get("ai", {})

        # 跳过无效新闻
        if not ai.get("valid", False):
            continue

        # b. 分类过滤
        news_category = ai.get("category", "uncategorized").lower()
        if categories and news_category not in categories:
            continue

        # c. 重要性过滤
        importance = ai.get("importance", 0)
        if importance < min_importance:
            continue

        # d. 推送去重
        news_id = news.get("fingerprint", "")
        if news_id and await _is_already_pushed(chat_id, news_id):
            logger.debug("用户 %s: 新闻 %s 已推送过，跳过。", chat_id, news_id)
            continue

        filtered.append(news)

    # 按 importance 降序排序，取 Top N
    filtered.sort(key=lambda x: x.get("ai", {}).get("importance", 0), reverse=True)
    top_news = filtered[:MAX_NEWS_PER_PUSH]

    logger.info(
        "用户 %s: 从 %d 条新闻中匹配到 %d 条（Top %d）。",
        chat_id, len(news_list), len(top_news), MAX_NEWS_PER_PUSH,
    )
    return top_news
