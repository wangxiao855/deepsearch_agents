"""
网页正文抓取工具。
"""
import os
import re
from typing import Annotated
from dotenv import load_dotenv
from langchain_core.tools import tool
from api.monitor import monitor
from tools import budget
from utils.logger_utils import logger



load_dotenv(override=True)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_TIMEOUT = float(os.getenv("WEB_FETCH_TIMEOUT", "20"))
_DEFAULT_MAX_CHARS = int(os.getenv("WEB_FETCH_MAX_CHARS", "6000"))
_MIN_USEFUL_CHARS = 300


_FETCH_BUDGET_DEFAULT = 12

_JINA_ENABLED = os.getenv("WEB_FETCH_JINA", "auto").strip().lower() not in (
    "0", "false", "off", "no",
)
_JINA_MAX_FAILURES = 2
_jina_failures = 0

_SKIP_PATTERNS = (
    "google.com/search", "bing.com/search", "baidu.com/s?", "so.com/s?",
    "javascript:", "mailto:",
)


def _fetch_by_httpx(url: str):
    """优先用httpx抓取"""
    try:
        import httpx

        headers = {
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True, headers=headers) as client:
            resp = client.get(url)
            if resp.status_code == 200 and resp.text:
                return resp.text, str(resp.url)
            logger.warning("httpx 状态码 {}".format(resp.status_code))
    except Exception as e:
        logger.warning("httpx 抓取失败 {}: {}".format(url, e))
    return None, None


def _fetch_by_requests(url: str):
    """httpx不可用或被拦"""
    try:
        import requests

        resp = requests.get(url, timeout=_TIMEOUT, headers={"User-Agent": _UA})
        if resp.status_code == 200 and resp.text:
            resp.encoding = resp.apparent_encoding or resp.encoding
            return resp.text, str(resp.url)
    except Exception as e:
        logger.warning("requests 抓取失败 {}: {}".format(url, e))
    return None, None

def _extract_with_lxml(html: str):
    """
    用 lxml 提取正文
    """
    try:
        from lxml import html as lxml_html
    except Exception:
        return _strip_tags(html), ""

    try:
        tree = lxml_html.fromstring(html)
    except Exception:
        return _strip_tags(html), ""
    title = ""
    try:
        node = tree.find(".//title")
        if node is not None:
            title = " ".join((node.text_content() or "").split())[:120]
    except Exception:
        title = ""

    for bad in tree.xpath(
        "//script|//style|//noscript|//iframe|//nav|//footer|//header|//aside|//form|//button"
    ):
        parent = bad.getparent()
        if parent is not None:
            parent.remove(bad)
    best_node, best_len = None, 0
    for node in tree.xpath("//article|//main|//div|//section"):
        para_len = sum(len(p.text_content().strip()) for p in node.xpath(".//p"))
        if para_len > best_len:
            best_node, best_len = node, para_len
    root = best_node if best_node is not None else tree

    # 按文档顺序收集块级文本
    blocks = []
    for el in root.xpath(".//h1|.//h2|.//h3|.//p|.//li|.//td"):
        text = " ".join(el.text_content().split())
        if text:
            blocks.append(text)

    # 去重相邻重复
    deduped = []
    for block in blocks:
        if not deduped or deduped[-1] != block:
            deduped.append(block)
    return "\n".join(deduped), title

def _jina_available() -> bool:
    """熔断器，连续失败达到阈值后，本进程内不再尝试"""
    return _JINA_ENABLED and _jina_failures < _JINA_MAX_FAILURES


def _fetch_by_jina(url: str) -> str:
    """
    第三方抽取服务：把目标页交给JinaReader渲染成Markdown
    """
    global _jina_failures
    if not _jina_available():
        return ""
    try:
        import httpx
        timeout = min(_TIMEOUT, 8.0) + 2
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get("https://r.jina.ai/{}".format(url), headers={"User-Agent": _UA})
            if resp.status_code == 200 and resp.text:
                _jina_failures = 0
                return resp.text
            _jina_failures += 1
            logger.warning(
                "Jina 返回状态码 {}（连续失败 {} 次）".format(resp.status_code, _jina_failures)
            )
    except Exception as e:
        _jina_failures += 1
        logger.warning("Jina 抓取失败 {}: {}".format(url, e))
    if _jina_failures >= _JINA_MAX_FAILURES:
        logger.warning(
            "Jina Reader 连续失败 {} 次，本进程内不再尝试；".format(_jina_failures)
        )
    return ""





def _strip_tags(html: str) -> str:
    """最后的兜底"""
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;?", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _compress(text: str, max_chars: int) -> str:
    """压缩空白并截断，避免塞进上下文。"""
    text = re.sub(r"\n{3,}", "\n\n", text or "")
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...(正文过长，已截断，共 {} 字)".format(len(text))


@tool
def fetch_webpage(
    url: Annotated[str, "要读取正文的网页地址"],
    max_chars: Annotated[int, "最多返回多少字符"] = _DEFAULT_MAX_CHARS,
) -> str:
    """
    读取指定网页的正文内容，用于获取搜索摘要之外的详细信息
    """
    monitor.report_tool("网页正文抓取工具", {"url": url})

    target = (url or "").strip()
    if not target.startswith(("http://", "https://")):
        return "抓取失败：url 必须以 http:// 或 https:// 开头，当前为「{}」".format(url)
    for pattern in _SKIP_PATTERNS:
        if pattern in target:
            return "抓取跳过：{} 属于搜索结果页，对结论没有价值，请换具体文章页。".format(target)

    allowed, used, limit = budget.consume("fetch", _FETCH_BUDGET_DEFAULT)
    if not allowed:
        logger.warning("正文抓取预算已用完，拒绝第 {} 次抓取: {}".format(used, target))
        return (
            "【抓取已终止】本轮正文抓取预算（{} 次）已用完，这是第 {} 次调用。\n"
            "fetch_webpage 与 internet_search_tool 都不会再返回新内容，继续调用只是浪费轮次。\n"
            "请立刻停止调用工具，用已读到的页面写最终结论；没读到的逐条标「未获取到」。".format(
                limit, used
            )
        )

    max_chars = max(500, min(int(max_chars or _DEFAULT_MAX_CHARS), 20000))

    html, final_url = _fetch_by_httpx(target)
    if html is None:
        html, final_url = _fetch_by_requests(target)

    text, page_title = "", ""
    if html:
        text, page_title = _extract_with_lxml(html)

    # 改走 Jina
    if len(text) < _MIN_USEFUL_CHARS and _jina_available():
        logger.info("直连正文过短，改用 Jina Reader".format(len(text)))
        jina_text = _fetch_by_jina(target)
        if len(jina_text) > len(text):
            text = jina_text

    if len(text) < 100:
        return (
            "抓取失败：没有取到有效正文（可能被反爬拦截或需要登录）。"
            "请改用其他来源，或在结论中说明该信息未获取到。".format(target)
        )

    display_title = page_title or (final_url or target)
    monitor.report_source(title=display_title, url=final_url or target, note="正文抓取")
    body = _compress(text, max_chars)
    header = "来源：{}\n标题：{}\n抓取字数：约 {} 字\n{}".format(
        final_url or target, display_title, len(body), "-" * 40
    )
    return header + "\n" + body
