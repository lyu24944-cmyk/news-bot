"""
AI 分类与摘要引擎

支持多 Provider 降级链: GLM-5(元景) → DeepSeek → OpenAI → 纯文本兜底
- 自动捕获限流/认证/超时异常，切换下一个 Provider
- Prompt 模板根据 target_lang 动态注入
- 严格 JSON 输出格式
- 英文源额外输出 original_title
"""

import asyncio
import json
import logging
import os
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# ── AI Provider 配置 ──────────────────────────────────────

AI_PROVIDERS: list[dict[str, Any]] = [
    {
        "name": "GLM-5 (元景)",
        "env_key": "YUANJING_KEY",
        "base_url": "https://maas.ai-yuanjing.com/openai/v1/chat/completions",
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
  "category": "分类（如：科技/财经/政治/社会/娱乐/体育/国际/教育/健康）",
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
        content=content[:3000],  # 限制输入长度
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
            timeout=aiohttp.ClientTimeout(total=30),
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
        logger.warning("[%s] 请求超时", provider["name"])
        return None
    except Exception as exc:
        logger.warning("[%s] 请求异常: %s", provider["name"], exc)
        return None


async def call_ai_with_fallback(
    prompt: str,
    semaphore: asyncio.Semaphore,
) -> str | None:
    """
    依照降级链依次尝试每个 Provider。

    DeepSeek → OpenAI → None (由调用方处理兜底)
    使用 Semaphore 控制并发。
    """
    async with semaphore:
        async with aiohttp.ClientSession() as session:
            for provider in AI_PROVIDERS:
                api_key = os.environ.get(provider["env_key"], "")
                if not api_key:
                    logger.debug("[%s] 未配置 API Key，跳过", provider["name"])
                    continue

                logger.info("🤖 尝试 AI Provider: %s", provider["name"])
                result = await _call_provider(session, provider, api_key, prompt)
                if result:
                    logger.info("✅ [%s] 返回成功", provider["name"])
                    return result

                logger.warning("⚠️  [%s] 失败，尝试下一个…", provider["name"])

    logger.warning("所有 AI Provider 均不可用，将使用降级结果。")
    return None


def make_fallback_result(article: dict) -> dict[str, Any]:
    """
    所有 Provider 都失败时返回降级结果。

    {valid:true, category:"uncategorized", headline:原文前50字,
     summary:"AI服务暂时不可用", importance:2}
    """
    title = article.get("title", "")
    headline = title[:50] if title else "(无标题)"
    return {
        "valid": True,
        "category": "uncategorized",
        "tags": [],
        "importance": 2,
        "headline": headline,
        "summary": "AI 服务暂时不可用，无法生成摘要。",
    }


async def process_article_with_ai(
    article: dict,
    semaphore: asyncio.Semaphore,
    target_lang: str = "中文",
) -> str | None:
    """
    用 AI 处理单篇文章，返回原始文本响应。

    Parameters
    ----------
    article : dict  含 title, cleaned_content, source_lang
    semaphore : asyncio.Semaphore  AI 并发控制
    target_lang : str  输出语言

    Returns
    -------
    str | None  AI 返回的原始文本（可能含 JSON），None 表示全部失败
    """
    prompt = build_prompt(
        title=article.get("title", ""),
        content=article.get("cleaned_content", ""),
        source_lang=article.get("source_lang", "en"),
        target_lang=target_lang,
    )
    return await call_ai_with_fallback(prompt, semaphore)
