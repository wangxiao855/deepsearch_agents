"""
定义网络搜索工具（Tavily）。
"""
import os
from typing import Literal

from dotenv import load_dotenv
from langchain_core.tools import tool
from tavily import TavilyClient

from api.monitor import monitor
from tools import budget
from utils.logger_utils import logger

load_dotenv(override=True)

_SEARCH_BUDGET_DEFAULT = 18

_client = None

_DOMAIN_PRESETS = {
    "price": [
        "dongchedi.com", "autohome.com.cn", "yiche.com", "pcauto.com.cn",
    ],
    "review": [
        "dongchedi.com", "autohome.com.cn", "yiche.com", "pconline.com.cn",
    ],
    "aftersales": [
        "12365auto.com", "samr.gov.cn", "dongchedi.com", "autohome.com.cn",
    ],
    "owner": [
        "dongchedi.com", "autohome.com.cn", "yiche.com",
    ],
}

_VALID_TIME_RANGE = {"day", "week", "month", "year"}


def _get_client() -> TavilyClient:
    global _client
    if _client is None:
        api_key = os.getenv("TAVILY_API_KEY")
        if not api_key:
            raise RuntimeError("无法使用网络搜索工具")
        _client = TavilyClient(api_key=api_key)
    return _client


def _parse_domains(include_domains: str, preset: str) -> list:
    """合并显式域名与预置白名单"""
    domains = []
    if include_domains:
        domains += [d.strip() for d in include_domains.replace("，", ",").split(",") if d.strip()]
    if preset:
        domains += _DOMAIN_PRESETS.get(preset.strip().lower(), [])
    seen, result = set(), []
    for d in domains:
        d = d.replace("https://", "").replace("http://", "").strip("/")
        if d and d not in seen:
            seen.add(d)
            result.append(d)
    return result


def _render(query: str, response: dict, extra_note: str = "") -> str:
    results = response.get("results") or []
    if not results:
        return ""

    parts = ["检索问题: {}".format(query), "结果条数: {}".format(len(results))]
    if extra_note:
        parts.append(extra_note)

    answer = response.get("answer")
    if answer:
        parts.append("[整体总结] {}".format(answer))

    for index, item in enumerate(results, 1):
        parts.append(
            "\n[{}] {}\n    链接: {}\n    摘要: {}".format(
                index,
                item.get("title", "无标题"),
                item.get("url", ""),
                (item.get("content") or "").strip(),
            )
        )
    return "\n".join(parts)


def _report_sources(response: dict):
    """把检索结果作为出处推给前端"""
    for item in (response.get("results") or [])[:5]:
        try:
            monitor.report_source(
                title=(item.get("title") or "无标题")[:80],
                url=item.get("url", ""),
                note="网络检索",
            )
        except Exception:
            pass


@tool(description="Tavily 网络搜索工具")
def internet_search_tool(
    query: str,
    topic: Literal["general", "news", "finance"] = "general",
    max_results: int = 5,
    include_domains: str = "",
    preset: str = "",
    time_range: str = "",
    search_depth: str = "basic",
) -> str:
    """
    根据问题进行网络查询
    """
    try:
        max_results = max(1, min(int(max_results or 5), 10))
    except (TypeError, ValueError):
        max_results = 5

    time_range = (time_range or "").strip().lower()
    if time_range and time_range not in _VALID_TIME_RANGE:
        time_range = ""
    search_depth = "advanced" if str(search_depth).strip().lower() == "advanced" else "basic"

    domains = _parse_domains(include_domains, preset)

    # 预算守卫：四个子智能体并行时共享同一份预算，防止四路同时爆搜
    allowed, used, limit = budget.consume("search", _SEARCH_BUDGET_DEFAULT)
    if not allowed:
        logger.warning("检索预算已用完，拒绝第 {} 次检索: {!r}".format(used, query))
        monitor.report_tool(tool_name="网络搜索工具（预算已用完）", args={"query": query})
        return (
            "本轮检索预算已用完（上限 {} 次，已调用 {} 次）。\n"
            "请立即停止检索，基于已获得的信息给出结论；确实缺失的关键数据，"
            "直接写「未获取到」并说明建议去哪个渠道确认，不要用常识填补。".format(limit, used)
        )

    # 记录日志
    logger.info(
        "调用了网络搜索工具: query={!r} domains={} time_range={!r} ({}/{})".format(
            query, domains, time_range, used, limit
        )
    )
    # 推送前端可视化
    monitor.report_tool(
        tool_name="网络搜索工具",
        args={
            "query": query,
            "topic": topic,
            "max_results": max_results,
            "domains": domains,
            "time_range": time_range,
        },
    )

    request_args = {
        "query": query,
        "topic": topic,
        "max_results": max_results,
        "include_answer": "basic",
        "search_depth": search_depth,
    }
    if domains:
        request_args["include_domains"] = domains
    if time_range:
        request_args["time_range"] = time_range

    try:
        response = _get_client().search(**request_args)
    except Exception as e:
        logger.error("[Tavily] 搜索失败: {}".format(e), exc_info=True)
        return "网络搜索失败: {}".format(e)

    text = _render(query, response)
    note = ""
    if not text and domains:
        logger.info("[Tavily] 限定站点后无结果，放开域名重试")
        request_args.pop("include_domains", None)
        try:
            response = _get_client().search(**request_args)
            text = _render(query, response, extra_note="注意：限定站点检索无结果，以下为放开站点后的结果。")
        except Exception as e:
            logger.error("[Tavily] 放开域名重试失败: {}".format(e))

    if not text:
        return "没有检索到与「{}」相关的结果。可以换更具体的车系/年款关键词，或去掉时间限制。".format(query)

    _report_sources(response)
    left = budget.remaining("search", _SEARCH_BUDGET_DEFAULT)
    if left <= 3:
        text += "\n\n（提醒：本轮检索预算仅剩 {} 次，请优先补齐关键缺口，不要重复检索。）".format(left)
    return text
