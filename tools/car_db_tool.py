"""
库查询工具
"""
import os
import re
from typing import Annotated, Any, Optional

from dotenv import load_dotenv
from langchain_core.tools import tool
from mysql.connector import connect
from api.monitor import monitor
from utils.logger_utils import logger


load_dotenv(override=True)

# 设置白名单
_FAULT_TABLES = [
    t.strip() for t in os.getenv("CAR_FAULT_TABLES", "car_fault_01,car_fault_02").split(",") if t.strip()
]

_SNAPSHOT_DATE = os.getenv("CAR_FAULT_SNAPSHOT_DATE", "2026-10-14").strip()

_FAULT_COLUMNS = (
    "complaint_id, brand, car_series, car_model, "
    "problem_desc, typical_problem, complaint_date, status"
)

# 单次返回上限
_MAX_CASES = 30
_IDENT = re.compile(r"^[A-Za-z0-9_]+$")



def _db_name() -> str:
    return (os.getenv("MYSQL_DATABASE") or "").strip()


def _connect():
    db = _db_name()
    if not db:
        raise RuntimeError("无法查询汽车业务库")
    return connect(
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASSWORD"),
        database=db,
        charset="utf8mb4",
        autocommit=True,
        connection_timeout=8,
    )


def _existing_tables(conn) -> set:
    with conn.cursor() as cur:
        cur.execute(
            "select table_name from information_schema.tables where table_schema = %s",
            (_db_name(),),
        )
        return {str(row[0]).lower() for row in cur.fetchall()}


def _fault_tables(conn) -> list:
    exist = _existing_tables(conn)
    return [t for t in _FAULT_TABLES if _IDENT.match(t) and t.lower() in exist]


def _dedup_cte(tables: list) -> str:
    """投诉表合并去重"""
    union = " UNION ALL ".join(
        "SELECT {cols} FROM `{t}`".format(cols=_FAULT_COLUMNS, t=t) for t in tables
    )
    return (
        "WITH u AS ({union}), "
        "r AS (SELECT u.*, ROW_NUMBER() OVER (PARTITION BY complaint_id "
        "ORDER BY complaint_date DESC) AS rn FROM u) "
    ).format(union=union)


def _normalize_status(raw: Optional[str]) -> str:
    """清洗投诉状态字段。"""
    if not raw:
        return "未知"
    text = str(raw).strip()
    size = len(text)
    if size >= 4 and size % 2 == 0 and text[: size // 2] == text[size // 2:]:
        return text[: size // 2]
    return text


def _like(value: str) -> str:
    """把用户输入包装成 LIKE 模式"""
    safe = str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return "%{}%".format(safe)


def _where_filters(brand: str = "", series: str = "", keyword: str = ""):
    clauses, params = [], []
    if brand:
        clauses.append("(brand like %s or car_model like %s)")
        params.extend([_like(brand), _like(brand)])
    if series:
        clauses.append("(car_series like %s or car_model like %s)")
        params.extend([_like(series), _like(series)])
    if keyword:
        clauses.append("(problem_desc like %s or typical_problem like %s)")
        params.extend([_like(keyword), _like(keyword)])
    where = (" AND " + " AND ".join(clauses)) if clauses else ""
    return where, params


def _sample_note(total: int) -> str:
    return "（本地投诉样本 {} 条 · 数据截止 {}；样本不等于全量，勿据此判断投诉率或做车型排名）".format(
        total, _SNAPSHOT_DATE
    )


def _no_fault_tables_msg() -> str:
    return (
        "未找到可用的投诉表（配置的表名 {tables} 在库 {db} 中不存在）。"
        "请检查 .env 的 MYSQL_DATABASE 与 CAR_FAULT_TABLES。".format(
            tables=",".join(_FAULT_TABLES), db=_db_name() or "(空)"
        )
    )


def query_series_matches(keyword: str, limit: int = 20) -> list:
    """按品牌/车系/车型匹配本地库中的车型"""
    if not keyword:
        return []
    limit = max(1, min(int(limit or 20), 50))
    with _connect() as conn:
        tables = _fault_tables(conn)
        if not tables:
            return []
        sql = (
            _dedup_cte(tables)
            + "SELECT brand, car_series, count(*) AS cases, count(DISTINCT car_model) AS models "
            + "FROM r WHERE rn = 1 AND (brand like %s OR car_series like %s OR car_model like %s) "
            + "GROUP BY brand, car_series ORDER BY cases DESC, brand ASC LIMIT %s"
        )
        with conn.cursor() as cur:
            cur.execute(sql, (_like(keyword),) * 3 + (limit,))
            return [
                {"brand": r[0], "series": r[1], "cases": int(r[2]), "models": int(r[3])}
                for r in cur.fetchall()
            ]


def query_brands(limit: int = 80):
    """返回本地库覆盖的品牌及案例数"""
    with _connect() as conn:
        tables = _fault_tables(conn)
        if not tables:
            return [], 0
        cte = _dedup_cte(tables)
        with conn.cursor() as cur:
            cur.execute(cte + "SELECT count(DISTINCT brand) FROM r WHERE rn = 1")
            total = int(cur.fetchone()[0] or 0)
            cur.execute(
                cte + "SELECT brand, count(*) AS cases FROM r WHERE rn = 1 "
                "GROUP BY brand ORDER BY cases DESC, brand ASC LIMIT %s",
                (max(1, min(int(limit or 80), 200)),),
            )
            rows = [{"brand": r[0], "cases": int(r[1])} for r in cur.fetchall()]
    return rows, total


def query_complaint_cases(brand: str = "", series: str = "", keyword: str = "", limit: int = 8) -> list:
    """检索投诉案例原始记录"""
    limit = max(1, min(int(limit or 8), _MAX_CASES))
    where, params = _where_filters(brand, series, keyword)
    with _connect() as conn:
        tables = _fault_tables(conn)
        if not tables:
            return []
        sql = (
            _dedup_cte(tables)
            + "SELECT " + _FAULT_COLUMNS + " FROM r WHERE rn = 1"
            + where
            + " ORDER BY complaint_date DESC, complaint_id DESC LIMIT %s"
        )
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params) + (limit,))
            return [
                {
                    "complaint_id": r[0],
                    "brand": r[1],
                    "series": r[2],
                    "model": r[3],
                    "problem": r[4],
                    "typical": r[5],
                    "date": str(r[6]) if r[6] else "",
                    "status": _normalize_status(r[7]),
                }
                for r in cur.fetchall()
            ]

@tool
def list_car_brands() -> str:
    """列出本地投诉库覆盖的汽车品牌及各品牌案例数。用于确认库里有哪些品牌、避免同车异名。"""
    monitor.report_tool("查询本地投诉库品牌列表")
    try:
        rows, total = query_brands()
    except Exception as e:
        logger.error("[car_db] 品牌列表失败: {}".format(e))
        return "查询失败：{}".format(e)
    if not rows:
        return _no_fault_tables_msg()
    lines = ["本地投诉库覆盖 {} 个品牌（按案例数排序，展示前 {}）：".format(total, min(len(rows), 40))]
    lines += ["- {}：{} 条".format(r["brand"], r["cases"]) for r in rows[:40]]
    lines.append("提示：品牌名以本列表为准，检索时用这里的标准写法。")
    return "\n".join(lines)


@tool
def search_car_series(
    keyword: Annotated[str, "品牌或车系关键词"],
    limit: Annotated[int, "返回条数，默认 15"] = 15,
) -> str:
    """
    在本地投诉库中模糊匹配车型，返回品牌 / 标准车系名 / 案例数 / 覆盖的年款数
    """
    monitor.report_tool("本地投诉库车型匹配", {"keyword": keyword})
    try:
        rows = query_series_matches(keyword, limit)
    except Exception as e:
        logger.error("[car_db] 车型匹配失败: {}".format(e))
        return "查询失败：{}".format(e)
    if not rows:
        return "本地投诉库中没有匹配「{}」的车型。可以先用 list_car_brands 看库里有哪些品牌。".format(keyword)
    lines = ["匹配到 {} 个车系（本地库标准名）：".format(len(rows))]
    for r in rows:
        lines.append(
            "- {brand} / {series}（案例 {cases} 条，覆盖 {models} 个年款车型）".format(**r)
        )
    lines.append("请用上面的 brand 与 series 原文继续检索案例或做统计。")
    return "\n".join(lines)


@tool
def get_complaint_cases(
    brand: Annotated[str, "品牌"] = "",
    series: Annotated[str, "车系，"] = "",
    keyword: Annotated[str, "问题关键词"] = "",
    limit: Annotated[int, "返回案例条数"] = 8,
) -> str:
    """
    检索真实投诉案例
    """
    monitor.report_tool(
        "本地投诉案例检索",
        {"brand": brand, "series": series, "keyword": keyword, "limit": limit},
    )
    try:
        rows = query_complaint_cases(brand, series, keyword, limit)
    except Exception as e:
        logger.error("[car_db] 投诉案例查询失败: {}".format(e))
        return "查询失败：{}".format(e)
    if not rows:
        return (
            "本地投诉样本中未检索到「{b} {s} {k}」相关案例。"
            "这不代表该车型没有售后问题，只是样本未覆盖。".format(b=brand, s=series, k=keyword)
        ).strip()

    lines = ["检索到 {} 条投诉案例{}：".format(len(rows), _sample_note(len(rows)))]
    for r in rows:
        lines.append(
            "[{cid}] {date} | {brand} {series} | {model}\n"
            "    问题：{problem}\n"
            "    归类：{typical}\n"
            "    状态：{status}".format(
                cid=r["complaint_id"],
                date=r["date"],
                brand=r["brand"],
                series=r["series"],
                model=r["model"],
                problem=r["problem"],
                typical=r["typical"],
                status=r["status"],
            )
        )
    return "\n".join(lines)


@tool
def get_complaint_summary(
    brand: Annotated[str, "品牌"] = "",
    series: Annotated[str, "车系"] = "",
) -> str:
    """
    统计某品牌/车系在本地投诉样本中的问题类型分布、涉及年款、处理状态分布。
    """
    monitor.report_tool("本地投诉样本统计", {"brand": brand, "series": series})
    where, params = _where_filters(brand, series)
    try:
        with _connect() as conn:
            tables = _fault_tables(conn)
            if not tables:
                return _no_fault_tables_msg()
            cte = _dedup_cte(tables)
            with conn.cursor() as cur:
                cur.execute(
                    cte + "SELECT count(*), min(complaint_date), max(complaint_date) FROM r WHERE rn = 1" + where,
                    tuple(params),
                )
                total, first_day, last_day = cur.fetchone()
                if not total:
                    return (
                        "本地投诉样本中未检索到「{b} {s}」的案例（样本快照 {d}）。".format(
                            b=brand or "(全部)", s=series or "(全部)", d=_SNAPSHOT_DATE
                        )
                        + "这只能说明样本未覆盖，不能据此认为该车型没有售后问题。"
                    )
                cur.execute(
                    cte
                    + "SELECT typical_problem, count(*) c FROM r WHERE rn = 1"
                    + where
                    + " GROUP BY typical_problem ORDER BY c DESC LIMIT 8",
                    tuple(params),
                )
                problems = cur.fetchall()
                cur.execute(
                    cte
                    + "SELECT car_model, count(*) c FROM r WHERE rn = 1"
                    + where
                    + " GROUP BY car_model ORDER BY c DESC LIMIT 6",
                    tuple(params),
                )
                models = cur.fetchall()
                cur.execute(
                    cte
                    + "SELECT status, count(*) c FROM r WHERE rn = 1"
                    + where
                    + " GROUP BY status ORDER BY c DESC",
                    tuple(params),
                )
                statuses = cur.fetchall()
    except Exception as e:
        logger.error("[car_db] 投诉统计失败: {}".format(e))
        return "查询失败：{}".format(e)

    # 状态分布先做脏值清洗，再合并计数
    status_counter = {}
    for raw, count in statuses:
        key = _normalize_status(raw)
        status_counter[key] = status_counter.get(key, 0) + int(count)

    lines = [
        "本地投诉样本统计：{} / {}".format(brand or "全部品牌", series or "全部车系"),
        "样本量：{} 条（数据截止 {}，实际时间范围 {} ~ {}）".format(
            total, _SNAPSHOT_DATE, first_day, last_day
        ),
        "",
        "【典型问题分布（样本内，按原文归类）】",
    ]
    lines += ["- {}：{} 条".format(str(p or "未归类")[:60], c) for p, c in problems]
    lines += ["", "【涉及年款车型 Top6】"]
    lines += ["- {}：{} 条".format(m, c) for m, c in models]
    lines += ["", "【处理状态分布】"]
    lines += ["- {}：{} 条".format(k, v) for k, v in sorted(status_counter.items(), key=lambda x: -x[1])]

    lines += [
        "",
        "使用提醒：以上是「样本内分布」，样本共 {} 条，且只覆盖 {} 前后几天。".format(total, _SNAPSHOT_DATE),
        "可以说明「这款车在样本里被投诉最多的是 X」，但不能说「这款车投诉率高 / 质量差」，",
        "也不能与其他车型比较投诉数量，更不能据此判断质量趋势。",
    ]
    return "\n".join(lines)

_NO_INTEL_MSG = (
    "本地车型结构化库尚未配置（库 {db} 中缺少 {table} 表）。"
    "初始化方式：python scripts/init_car_intel_db.py 再 python scripts/seed_car_intel.py。"
    "在此之前请改用 internet_search_tool 检索公开数据，并注明来源与日期，不要编造价格或销量。"
)


def _intel_db() -> str:
    """价格/销量库名"""
    return (os.getenv("CAR_INTEL_DB") or _db_name()).strip()


def _connect_intel():
    db = _intel_db()
    if not db:
        raise RuntimeError("未配置 CAR_INTEL_DB / MYSQL_DATABASE，无法查询车型库")
    return connect(
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASSWORD"),
        database=db,
        charset="utf8mb4",
        autocommit=True,
        connection_timeout=8,
    )


def _intel_ready(conn, *tables: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "select table_name from information_schema.tables where table_schema = %s",
            (_intel_db(),),
        )
        exist = {str(row[0]).lower() for row in cur.fetchall()}
    return all(t.lower() in exist for t in tables)


def _money(value) -> str:
    if value is None:
        return "未获取到"
    try:
        return "{:.2f}".format(float(value))
    except (TypeError, ValueError):
        return str(value)


def _brand_series_clause(brand: str, series: str):
    clauses, params = [], []
    if brand:
        clauses.append("brand like %s")
        params.append(_like(brand))
    if series:
        clauses.append("series like %s")
        params.append(_like(series))
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


@tool
def search_model_library(
    keyword: Annotated[str, "品牌或车系关键词，例如"] = "",
    max_price: Annotated[float, "预算上限（万元）"] = 0,
    limit: Annotated[int, "返回条数，默认 12"] = 12,
) -> str:
    """在本地车型库中检索车型，返回品牌 / 车系 / 级别 / 能源形式 / 指导价区间 / 数据日期"""
    monitor.report_tool(
        "本地车型库检索", {"keyword": keyword, "max_price": max_price, "limit": limit}
    )
    limit = max(1, min(int(limit or 12), 40))
    try:
        budget = float(max_price or 0)
    except (TypeError, ValueError):
        budget = 0.0

    try:
        with _connect_intel() as conn:
            if not _intel_ready(conn, "car_intel_series"):
                return _NO_INTEL_MSG.format(db=_intel_db(), table="car_intel_series")
            sql = (
                "SELECT brand, series, level, energy_type, guide_price_low, guide_price_high, "
                "price_date, source_url FROM car_intel_series WHERE 1 = 1"
            )
            params = []
            if keyword:
                sql += " AND (brand like %s OR series like %s OR level like %s OR energy_type like %s)"
                params = [_like(keyword)] * 4
            if budget > 0:
                # 以「车系最低指导价」为判断依据：用户说 15 万，实际是看 15 万能买到什么
                sql += " AND guide_price_low <= %s"
                params.append(budget)
            sql += " ORDER BY guide_price_low ASC LIMIT %s"
            params.append(limit)
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
    except Exception as e:
        logger.error("[car_db] 车型库检索失败: {}".format(e))
        return "查询失败：{}".format(e)

    if not rows:
        cond = []
        if keyword:
            cond.append("关键词「{}」".format(keyword))
        if budget > 0:
            cond.append("预算 ≤{} 万".format(budget))
        return "本地车型库没有匹配（{}）的车系。库内共收录 40 余款，可换更短的品牌名再试。".format(
            "、".join(cond) or "无条件"
        )

    lines = ["本地车型库匹配结果（指导价，单位万元）："]
    for brand, series, level, energy, low, high, pdate, url in rows:
        span = _money(low) if low == high else "{}~{}".format(_money(low), _money(high))
        lines.append(
            "- {} {}（{} · {}）指导价 {}｜{}｜{}".format(
                brand, series, level or "级别未收录", energy or "能源未收录",
                span, pdate or "日期未知", url or "",
            )
        )
    lines.append("提示：以上为车系指导价区间，配置级价格用 get_trim_price 查，销量用 get_monthly_sales 查。")
    return "\n".join(lines)


@tool
def get_trim_price(
    brand: Annotated[str, "品牌"],
    series: Annotated[str, "车系"],
) -> str:
    """
    在车系指导价区间查询本地车型库中的价格
    """
    monitor.report_tool("本地车型价格查询", {"brand": brand, "series": series})
    where, params = _brand_series_clause(brand, series)
    try:
        with _connect_intel() as conn:
            if not _intel_ready(conn, "car_intel_series"):
                return _NO_INTEL_MSG.format(db=_intel_db(), table="car_intel_series")
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT series_id, brand, series, level, energy_type, guide_price_low, "
                    "guide_price_high, price_date, source_url, note FROM car_intel_series"
                    + where
                    + " ORDER BY guide_price_low ASC LIMIT 6",
                    tuple(params),
                )
                series_rows = cur.fetchall()
                if not series_rows:
                    return "本地车型库中没有「{} {}」的价格记录。可先用 search_model_library 查标准车系名。".format(
                        brand or "(任意品牌)", series
                    )

                trims = {}
                if _intel_ready(conn, "car_intel_trim"):
                    ids = [row[0] for row in series_rows]
                    fmt = ",".join(["%s"] * len(ids))
                    cur.execute(
                        "SELECT series_id, model_year, trim_name, guide_price FROM car_intel_trim "
                        "WHERE series_id IN ({}) ORDER BY model_year DESC, guide_price ASC".format(fmt),
                        tuple(ids),
                    )
                    for sid, year, name, price in cur.fetchall():
                        trims.setdefault(sid, []).append((year, name, price))
    except Exception as e:
        logger.error("[car_db] 价格查询失败: {}".format(e))
        return "查询失败：{}".format(e)

    lines = ["本地车型库价格（单位：万元；终端成交价需联网查，门店报价为准）："]
    for sid, b, s, level, energy, low, high, pdate, url, note in series_rows:
        span = _money(low) if low == high else "{}~{}".format(_money(low), _money(high))
        lines.append(
            "{b} {s}（{lv} · {en}）\n"
            "    指导价：{span}｜数据日期：{d}\n"
            "    来源：{u}".format(
                b=b, s=s, lv=level or "级别未收录", en=energy or "能源未收录",
                span=span, d=pdate or "日期未知", u=url or "未标注",
            )
        )
        if note:
            lines.append("    备注：{}".format(note))
        for year, name, price in trims.get(sid, []):
            lines.append("    · {}款 {}：{} 万".format(year, name, _money(price)))
        if sid not in trims:
            lines.append("    · 配置级价格未收录，可联网查具体年款与配置价")
    lines.append(
        "提醒：这是官方指导价，不是你的成交价；终端优惠按城市和月份差异很大，"
        "引用时必须带上数据日期，并说明需要到店确认。"
    )
    return "\n".join(lines)


@tool
def get_monthly_sales(
    brand: Annotated[str, "品牌，如「比亚迪」；可留空"],
    series: Annotated[str, "车系，如「秦PLUS」「Model Y」"],
) -> str:
    """
    查询本地车型库中的月度销量，带统计月份、口径与来源。
    """
    monitor.report_tool("本地车型销量查询", {"brand": brand, "series": series})
    where, params = _brand_series_clause(brand, series)
    try:
        with _connect_intel() as conn:
            if not _intel_ready(conn, "car_intel_sales_monthly", "car_intel_series"):
                return _NO_INTEL_MSG.format(db=_intel_db(), table="car_intel_sales_monthly")
            sql = (
                "SELECT s.brand, s.series, m.stat_month, m.retail, m.wholesale, m.caliber, "
                "m.source, m.source_url "
                "FROM car_intel_sales_monthly m JOIN car_intel_series s ON m.series_id = s.series_id"
            )
            if where:
                sql += where.replace("brand like", "s.brand like").replace(
                    "series like", "s.series like"
                )
            sql += " ORDER BY m.stat_month DESC LIMIT 24"
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
    except Exception as e:
        logger.error("[car_db] 销量查询失败: {}".format(e))
        return "查询失败：{}".format(e)

    if not rows:
        return (
            "本地车型库未收录「{} {}」的销量数据。请改用 internet_search_tool 检索"
            "「车型 + 月份 + 乘联会/上险量」，并标注口径与来源，不要估算。".format(
                brand or "(任意品牌)", series
            )
        )

    lines = ["本地车型库月度销量（单位：辆）："]
    for b, s, month, retail, wholesale, caliber, source, url in rows:
        parts = []
        if retail is not None:
            parts.append("零售 {}".format(retail))
        if wholesale is not None:
            parts.append("批发 {}".format(wholesale))
        lines.append(
            "- {b} {s}｜{m}｜{v}｜口径 {c}｜来源 {src}\n    链接：{u}".format(
                b=b, s=s, m=month, v=" / ".join(parts) or "未获取到",
                c=caliber or "未标注", src=source or "未标注", u=url or "未提供",
            )
        )
    lines.append(
        "提醒：零售与批发口径不可混用；单月数据只能说明当月表现，"
        "不要据此推断全年或趋势，除非有多月数据。"
    )
    return "\n".join(lines)
