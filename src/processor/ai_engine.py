"""
AI 分类与摘要引擎

支持多 Provider 降级链: GLM-5(元景) → DeepSeek → OpenAI
- 每次请求之间强制间隔，避免免费额度限流
- 失败自动重试（指数退避）
- AI 全部失败时，使用本地关键词分类 + 原文截取摘要（不再输出"AI服务暂时不可用"）
"""

import asyncio
import json
import logging
import os
import re
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# ── AI Provider 配置 ──────────────────────────────────────

AI_PROVIDERS: list[dict[str, Any]] = [
    {
        "name": "GLM-5 (元景)",
        "env_key": "YUANJING_KEY",
        "base_url": "https://maas-api.ai-yuanjing.com/openapi/compatible-mode/v1/chat/completions",
        "model": "glm-5",
    },
    {
        "name": "DeepSeek",
        "env_key": "DEEPSEEK_KEY",
        "base_url": "https://api.deepseek.com/v1/chat/completions",
        "model": "deepseek-chat",
    },
    {
        "name": "OpenAI",
        "env_key": "OPENAI_KEY",
        "base_url": "https://api.openai.com/v1/chat/completions",
        "model": "gpt-4o-mini",
    },
]

# ── 请求间隔（秒）──────────────────────────────────────────
# 这是根源修复：每次 AI 请求之间强制等待，避免免费额度限流
REQUEST_INTERVAL = 3  # 秒

# ── Prompt 模板 ───────────────────────────────────────────

PROMPT_TEMPLATE_ZH = """\
你是一个专业新闻编辑，请分析以下新闻内容并以严格 JSON 格式输出。

要求：
1. 判断内容是否为有效新闻（非广告、非垃圾内容）
2. 如果有效，进行分类、打标签、评估重要性、生成标题和摘要
3. 摘要应当简洁精炼，保留核心信息，100-200字

输出语言：{target_lang}

如果内容有效，输出格式：
{{
  "valid": true,
  "category": "分类（必须使用以下英文 ID: tech/world/finance/science/society/sports/health）",
  "tags": ["标签1", "标签2", "标签3"],
  "importance": 重要性评分1-5,
  "headline": "精炼标题",
  "summary": "100-200字摘要"{extra_fields}
}}

如果内容无效（广告、空内容等），输出：
{{
  "valid": false,
  "reason": "判定为无效的原因"
}}

只输出 JSON，不要输出任何其他内容。

---
原始标题：{title}
新闻内容：
{content}
"""

EXTRA_FIELD_EN = ',\n  "original_title": "原始英文标题"'


def build_prompt(
    title: str,
    content: str,
    source_lang: str,
    target_lang: str = "中文",
) -> str:
    """构建 AI Prompt，动态注入语言和额外字段。"""
    extra_fields = EXTRA_FIELD_EN if source_lang == "en" else ""
    return PROMPT_TEMPLATE_ZH.format(
        target_lang=target_lang,
        title=title,
        content=content[:3000],
        extra_fields=extra_fields,
    )


# ── AI 调用 ───────────────────────────────────────────────

async def _call_provider(
    session: aiohttp.ClientSession,
    provider: dict[str, str],
    api_key: str,
    prompt: str,
) -> str | None:
    """调用单个 AI Provider，返回文本响应。"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": provider["model"],
        "messages": [
            {"role": "system", "content": "You are a professional news editor. Respond only in valid JSON."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 800,
    }

    try:
        async with session.post(
            provider["base_url"],
            headers=headers,
            json=payload,
            # 核心修复: 15 秒超时快速失败，配合多次重试
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status == 429:
                logger.warning("[%s] 触发限流 (429)", provider["name"])
                return None
            if resp.status == 401:
                logger.warning("[%s] 认证失败 (401)", provider["name"])
                return None
            if resp.status != 200:
                body = await resp.text()
                logger.warning("[%s] HTTP %d: %s", provider["name"], resp.status, body[:200])
                return None

            data = await resp.json()
            choices = data.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
            return None

    except asyncio.TimeoutError:
        logger.warning("[%s] 请求超时 (15s)", provider["name"])
        return None
    except Exception as exc:
        logger.warning("[%s] 请求异常: %s", provider["name"], exc)
        return None


# 每个 Provider 最大重试次数
MAX_RETRIES = 3
# 重试间隔（秒）
RETRY_DELAY = 2


async def call_ai_with_fallback(prompt: str) -> str | None:
    """
    依照降级链依次尝试每个 Provider。

    根因: GLM-5 有约 40% 的请求会服务端挂起超时。
    修复: 15s 超时快速失败 + 每个 Provider 重试 3 次。
    概率: 单次失败率 40%, 3 次全失败概率 = 0.4^3 = 6.4%
    """
    async with aiohttp.ClientSession() as session:
        for provider in AI_PROVIDERS:
            api_key = os.environ.get(provider["env_key"], "")
            if not api_key:
                logger.debug("[%s] 未配置 API Key，跳过", provider["name"])
                continue

            for attempt in range(1, MAX_RETRIES + 1):
                if attempt > 1:
                    logger.info("🔄 [%s] 第 %d/%d 次重试…", provider["name"], attempt, MAX_RETRIES)
                    await asyncio.sleep(RETRY_DELAY)

                logger.info("🤖 [%s] 第 %d/%d 次请求", provider["name"], attempt, MAX_RETRIES)
                result = await _call_provider(session, provider, api_key, prompt)
                if result:
                    logger.info("✅ [%s] 成功 (第%d次)", provider["name"], attempt)
                    return result

            logger.warning("⚠️  [%s] %d 次均失败", provider["name"], MAX_RETRIES)

    logger.warning("所有 AI Provider 均不可用，使用本地降级。")
    return None


# ── 本地降级（关键词分类 + 原文摘要）─────────────────────────

# 关键词 → 分类映射
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "tech": [
        "ai", "artificial intelligence", "人工智能", "机器学习", "deep learning",
        "芯片", "chip", "gpu", "nvidia", "英伟达", "半导体", "semiconductor",
        "苹果", "apple", "google", "谷歌", "微软", "microsoft", "openai",
        "软件", "software", "app", "api", "代码", "code", "开源", "github",
        "算法", "数据", "data", "cloud", "云计算", "robot", "机器人",
        "手机", "手表", "数码", "科技", "tech", "startup", "创业",
    ],
    "finance": [
        "股票", "stock", "基金", "fund", "投资", "invest", "融资",
        "美元", "dollar", "经济", "economy", "gdp", "通胀", "inflation",
        "央行", "fed", "利率", "interest rate", "上市", "ipo", "估值",
        "营收", "revenue", "利润", "profit", "市值", "market cap",
    ],
    "world": [
        "战争", "war", "军事", "military", "总统", "president",
        "外交", "diplomatic", "联合国", "united nations", "选举", "election",
        "国际", "international", "global", "全球", "制裁", "sanction",
    ],
    "science": [
        "科学", "science", "研究", "research", "论文", "paper",
        "太空", "space", "nasa", "量子", "quantum", "物理", "physics",
        "生物", "bio", "基因", "gene", "实验", "experiment",
    ],
    "health": [
        "健康", "health", "医疗", "medical", "药物", "drug", "疫苗", "vaccine",
        "医院", "hospital", "疾病", "disease", "心理", "mental",
    ],
    "sports": [
        "体育", "sports", "足球", "soccer", "篮球", "basketball",
        "奥运", "olympic", "比赛", "match", "冠军", "champion",
    ],
    "society": [
        "社会", "society", "教育", "education", "文化", "culture",
        "生活", "life", "消费", "consumer", "品牌", "brand",
    ],
}


def _classify_by_keywords(title: str, content: str) -> str:
    """根据关键词对文章进行本地分类。"""
    text = f"{title} {content[:500]}".lower()
    scores: dict[str, int] = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text)
        if score > 0:
            scores[category] = score

    if scores:
        return max(scores, key=scores.get)
    return "society"  # 默认归类为「社会」


def _extract_summary(title: str, content: str, lang: str) -> str:
    """从原文中提取前几句话作为摘要（非 AI 生成）。"""
    text = content.strip()
    if not text:
        return title

    # 按句子分割
    if lang == "zh":
        # 中文按句号、感叹号、问号分句
        sentences = re.split(r"[。！？\n]+", text)
    else:
        # 英文按 . ! ? 分句
        sentences = re.split(r"[.!?\n]+", text)

    # 取前 3 句，组成 50-200 字摘要
    result = []
    char_count = 0
    for s in sentences:
        s = s.strip()
        if not s or len(s) < 5:
            continue
        result.append(s)
        char_count += len(s)
        if char_count >= 100 or len(result) >= 3:
            break

    if result:
        joiner = "。" if lang == "zh" else ". "
        summary = joiner.join(result)
        if lang == "zh" and not summary.endswith("。"):
            summary += "。"
        elif lang != "zh" and not summary.endswith("."):
            summary += "."
        return summary

    # 最后兜底：直接截取
    return text[:200] + ("…" if len(text) > 200 else "")


def _estimate_importance(title: str, content: str) -> int:
    """根据内容长度和关键词估算重要性。"""
    text = f"{title} {content[:300]}".lower()
    score = 2  # 基础分

    # 内容丰富 +1
    if len(content) > 500:
        score += 1

    # 含重大事件关键词 +1
    important_keywords = [
        "突发", "重大", "breaking", "首次", "首个", "first",
        "发布", "launch", "release", "收购", "acquisition",
        "禁止", "ban", "宣布", "announce", "billion", "亿",
    ]
    if any(kw in text for kw in important_keywords):
        score += 1

    return min(score, 5)


def make_fallback_result(article: dict) -> dict[str, Any]:
    """
    所有 AI Provider 失败时使用本地智能降级。

    不再输出"AI服务暂时不可用"，而是：
    - 用关键词匹配进行分类
    - 用原文前几句话作为摘要
    - 用标题作为 headline
    - 根据内容估算重要性
    """
    title = article.get("title", "")
    content = article.get("cleaned_content", "")
    lang = article.get("source_lang", "zh")

    headline = title[:50] if title else "(无标题)"
    category = _classify_by_keywords(title, content)
    summary = _extract_summary(title, content, lang)
    importance = _estimate_importance(title, content)

    # 从标题和内容提取简单 tag
    tags = []
    text = f"{title} {content[:200]}".lower()
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in text and kw not in tags and len(kw) > 1:
                tags.append(kw)
                if len(tags) >= 3:
                    break
        if len(tags) >= 3:
            break

    logger.info("📎 本地降级: [%s] imp=%d %s", category, importance, headline[:30])

    return {
        "valid": True,
        "category": category,
        "tags": tags,
        "importance": importance,
        "headline": headline,
        "summary": summary,
    }


async def process_article_with_ai(
    article: dict,
    semaphore: asyncio.Semaphore | None = None,
    target_lang: str = "中文",
) -> str | None:
    """
    用 AI 处理单篇文章，返回原始文本响应。

    注意：semaphore 参数保留以兼容旧接口，但不再使用。
    并发控制改由 main_fetch.py 中的顺序处理 + 间隔来保证。
    """
    prompt = build_prompt(
        title=article.get("title", ""),
        content=article.get("cleaned_content", ""),
        source_lang=article.get("source_lang", "en"),
        target_lang=target_lang,
    )
    return await call_ai_with_fallback(prompt)
