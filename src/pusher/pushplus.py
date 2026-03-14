"""
PushPlus 微信推送模块

通过 PushPlus API 将新闻推送到微信。
- 用户关注「pushplus 推送加」公众号后获取 token
- 每天免费 200 条推送
- 支持 HTML / Markdown / 纯文本

环境变量:
    PUSHPLUS_TOKEN — PushPlus 推送 Token
"""

import logging
import os

import aiohttp

logger = logging.getLogger(__name__)

PUSHPLUS_API = "http://www.pushplus.plus/send"
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN", "")


def _markdown_to_html(md_text: str) -> str:
    """
    将 Telegram Markdown 格式转换为 HTML（供 PushPlus 使用）。

    - *bold* → <b>bold</b>
    - _italic_ → <i>italic</i>
    - [text](url) → <a href="url">text</a>
    - 换行 → <br>
    - 分隔线 ━/─ → <hr>
    """
    import re

    text = md_text

    # 转义的 Markdown 字符还原
    text = text.replace("\\*", "⟨STAR⟩")
    text = text.replace("\\_", "⟨UNDER⟩")
    text = text.replace("\\`", "⟨TICK⟩")
    text = text.replace("\\[", "⟨LBRACKET⟩")

    # 链接 [text](url)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)

    # 粗体 *text*
    text = re.sub(r"\*([^*]+)\*", r"<b>\1</b>", text)

    # 斜体 _text_
    text = re.sub(r"_([^_]+)_", r"<i>\1</i>", text)

    # 还原转义字符
    text = text.replace("⟨STAR⟩", "*")
    text = text.replace("⟨UNDER⟩", "_")
    text = text.replace("⟨TICK⟩", "`")
    text = text.replace("⟨LBRACKET⟩", "[")

    # 分隔线
    text = re.sub(r"[━─]{5,}", "<hr>", text)

    # 换行
    text = text.replace("\n", "<br>\n")

    # 包装在样式容器中
    styled = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
                max-width: 600px; margin: 0 auto; padding: 16px; 
                background: #f8f9fa; border-radius: 12px;">
        <div style="background: white; padding: 20px; border-radius: 8px; 
                    box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
            {text}
        </div>
        <div style="text-align: center; color: #999; font-size: 12px; margin-top: 12px;">
            Powered by News-Bot 🤖
        </div>
    </div>
    """
    return styled


async def send_pushplus(
    content: str,
    title: str = "📰 今日新闻摘要",
    token: str = "",
) -> bool:
    """
    通过 PushPlus 推送消息到微信。

    Parameters
    ----------
    content : str  消息内容（Markdown 格式，会自动转 HTML）
    title : str  消息标题
    token : str  PushPlus Token（为空时从环境变量读取）

    Returns
    -------
    bool  是否发送成功
    """
    tkn = token or PUSHPLUS_TOKEN
    if not tkn:
        logger.warning("PUSHPLUS_TOKEN 未设置，跳过微信推送。")
        return False

    html_content = _markdown_to_html(content)

    payload = {
        "token": tkn,
        "title": title,
        "content": html_content,
        "template": "html",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                PUSHPLUS_API,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("code") == 200:
                        logger.info("✅ PushPlus 微信推送成功")
                        return True
                    else:
                        logger.error(
                            "PushPlus API 错误: code=%s msg=%s",
                            data.get("code"),
                            data.get("msg"),
                        )
                        return False
                else:
                    body = await resp.text()
                    logger.error("PushPlus HTTP %d: %s", resp.status, body[:200])
                    return False

    except Exception as exc:
        logger.error("PushPlus 推送异常: %s", exc)
        return False
