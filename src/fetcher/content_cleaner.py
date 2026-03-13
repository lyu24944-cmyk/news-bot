"""
Content Cleaner - HTML 内容清洗模块

双重 html.unescape 解码 → BeautifulSoup 剥离标签 → 清理空白换行。
"""

import html
import re

from bs4 import BeautifulSoup


def unescape_html(text: str) -> str:
    """双重 html.unescape，处理嵌套编码的实体（如 &amp;amp;）。"""
    return html.unescape(html.unescape(text))


def strip_html_tags(text: str) -> str:
    """使用 BeautifulSoup 移除所有 HTML 标签，保留纯文本。"""
    soup = BeautifulSoup(text, "html.parser")
    return soup.get_text(separator=" ")


def normalize_whitespace(text: str) -> str:
    """将多余的空白和换行压缩为单个空格，并去除首尾空白。"""
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def pick_best_content(entry: dict) -> str:
    """
    从文章字典中选取最佳内容字段。

    优先级: raw_content (来自 content:encoded / summary) → title
    """
    content = entry.get("raw_content", "")
    if not content:
        content = entry.get("title", "")
    return content


def clean_article(article: dict) -> dict:
    """
    清洗单篇文章，返回新字典。

    处理流程:
    1. 选取最佳原始内容
    2. 双重 HTML 实体解码
    3. 剥离 HTML 标签
    4. 规范化空白

    Returns
    -------
    dict  包含 title, link, cleaned_content, source_lang
    """
    raw = pick_best_content(article)

    # Pipeline: unescape → strip tags → normalize
    text = unescape_html(raw)
    text = strip_html_tags(text)
    text = normalize_whitespace(text)

    # 标题也做清洗
    title = normalize_whitespace(strip_html_tags(unescape_html(article.get("title", ""))))

    return {
        "title": title,
        "link": article.get("link", ""),
        "cleaned_content": text,
        "source_lang": article.get("source_lang", "en"),
    }


def clean_articles(articles: list[dict]) -> list[dict]:
    """批量清洗文章列表。"""
    return [clean_article(a) for a in articles]
