# 车问 · 购车参谋

> 输入一款车或一个预算，输出“现在卖多少钱、别人怎么评价、售后靠不靠谱、车主真实体验如何”，并给出买不买、买哪款、到店该问什么的建议。

基于 **deepagents + LangGraph** 的多智能体购车决策助手：主智能体负责调度与交叉核验，四个子智能体并行取证（行情 / 口碑 / 售后 / 车主），前端通过 WebSocket 实时展示取证进度、出处卡片与逐字生成的分析结论。

---

## 为什么做这个

买车这件事真正的难点不是"搜不到信息"，而是**信息太多且互相打架**：厂商话术、媒体软文、车主牢骚、几年前的旧报价混在一起，普通人没有能力分辨哪句能信。

所以这个项目的目标不是"把搜索结果汇总给你"，而是：

- **给出决策**：该不该买、买哪款、什么时候买、到店问什么；
- **把每句话的出处摆出来**，让用户能自己核验；
- **明确告诉你哪些信息不可信**——样本不足就直接说"未获取到"，绝不拿常识填空。

---

## 它能回答什么

| 用户的真实困惑 | Agent 对应的能力 | 数据来源 |
|---|---|---|
| 这款车现在到底多少钱？优惠多少？ | 指导价 / 终端裸车价 / 落地价三档分开，带日期与城市 | 本地车型库 + 网络检索 |
| 卖得好不好？还会不会降价？ | 月销量（零售 / 批发分开）、在售年款、交付周期 | 本地销量库 + 网络检索 |
| 媒体怎么评价？有什么硬伤？ | 专业评测、横评、实测数据，正反并列 | 网络检索 + 正文抓取 |
| 售后靠不靠谱？保养贵不贵？ | 质保条款分项、保养单价、召回公告、服务网络 | 官方条款 + 召回公告检索 |
| 车主实际体验如何？有什么通病？ | 真实车主原话、问题频次、真实能耗、用车成本 | 本地投诉样本 + 车主社区 |
| 和竞品比该选谁？ | 2~3 款竞品对比表 + 适合人群 | 上述四维汇总 |

---

## 架构

```
┌─────────────┐   POST /api/task        ┌──────────────────────────┐
│  浏览器      │ ──────────────────────► │  FastAPI (api/server.py) │
│  Vue 3      │ ◄────────────────────── │  · create_task 后台跑     │
└─────────────┘   WS /ws/{thread_id}    └────────────┬─────────────┘
       ▲                                              │
       │  session_created / stage / source            ▼
       │  tool_start / assistant_call          run_deep_agent
       │  answer_chunk / task_result / error   (agent/main_agent.py)
       │                                              │
       └──────────────────────────────────────────────┤
                                                      ▼
                                       主智能体（购车顾问）
                          车型归一 → 派活 → 交叉核验 → 结构化输出
                                                      │
              ┌───────────────┬───────────────┬───────┴───────┐
              ▼               ▼               ▼               ▼
          行情情报助手     口碑评测助手     售后保障助手     车主反馈助手
              │               │               │               │
              ├───────────────┴───────┬───────┴───────────────┤
              ▼                       ▼                       ▼
     internet_search_tool        fetch_webpage            car_db_tool
     （站点白名单/时间范围）      （网页抓取正文）      （只读：投诉/价格/销量）
```

### 数据分三层

| 层 | 内容 | 特点 |
|---|---|---|
| **L1 事实层** | 本地投诉案例库（597 条） | 可引用的具体案例，用于回答"被投诉过什么" |
| **L2 结构化层** | 车型价格 / 配置 / 月销量 | 有数据日期与来源链接，可核对 |
| **L3 时效层** | 终端优惠 / 新车上市 / 评测 / 召回 | 每次都联网查，只信当月的 |

**分工原则**：参数与历史查库（准），时效信息查网（新）。缺失任何一层都会自动降级，不会中断任务。

---

## 快速开始

### 1. 环境

- Python **3.12**
- Node.js 18+
- MySQL 8.x

```bash
python -m venv .venv 

pip install -r requirements.txt
```

<details>
<summary>requirements.txt 内容（折叠展开）</summary>

```
deepagents==0.7.21
langchain-core==1.6.6
langchain-openai==1.6.7
langgraph==1.2.12

fastapi==0.142.2
uvicorn==0.54.0
websockets==16.1.1
python-multipart==0.0.32

tavily-python==0.8.4
mysql-connector-python==26.7.0

httpx==0.28.1
lxml==6.1.3

pypdf==6.19.0
python-docx==1.2.0
pandas==3.0.6
openpyxl==3.1.5

Markdown==3.11
colorlog==6.12.0
python-dotenv==1.2.4
PyYAML==6.0.3

pywin32==312
```

</details>

### 2. 配置

在项目根目录创建 `.env` 并填写（`.env` 已在 `.gitignore` 中，不会被提交）。模板如下：

```ini
# 模型：OpenAI 兼容接口
QWEN_APIKEY=
QWEN_URL=
QWEN_MODEL=

# 网络搜索（必填，否则行情/口碑/车主维度失效）
TAVILY_API_KEY=

# MySQL
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=
MYSQL_PASSWORD=
MYSQL_DATABASE=car_fault_db      # 投诉案例库
CAR_INTEL_DB=car_shopping        # 车型价格/销量库

# 投诉样本
CAR_FAULT_TABLES=car_fault_01,car_fault_02
CAR_FAULT_SNAPSHOT_DATE=2026-10-14

# 正文抓取
WEB_FETCH_TIMEOUT=20
WEB_FETCH_MAX_CHARS=6000
WEB_FETCH_JINA=auto             

# 每轮提问的检索预算（四维共享）
SEARCH_BUDGET_PER_TURN=18
```

### 3. 数据库

两个库，职责分离。**都为空也能启动**——工具会返回"未配置"提示，agent 自动降级为纯网络检索。

| 库 | 表 | 作用 |
|---|---|---|
| `car_fault_db` | `car_fault_01` / `car_fault_02` | 投诉案例：`complaint_id, brand, car_series, car_model, problem_desc, typical_problem, complaint_date, status` |
| `car_shopping` | `car_intel_series` | 车系：`brand, series, level, energy_type, guide_price_low/high, price_date, source_url` |
| | `car_intel_trim` | 单配置价：`series_id, model_year, trim_name, guide_price, ...` |
| | `car_intel_sales_monthly` | 月销量：`series_id, stat_month, retail, wholesale, caliber, source_url` |

投诉两张表按 `complaint_id` 合并去重（CTE + `ROW_NUMBER()`），有重叠也不会重复统计。

### 4. 启动

```bash
python main.py         
```

---

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/task` | `{query, thread_id?}`，启动任务并立即返回 `thread_id`；同一 id 续接多轮对话 |
| `POST` | `/api/upload` | multipart，`files` + `thread_id`，存到 `upload/session_{thread_id}` |
| `POST` | `/api/session/reset` | `{thread_id}`，清空该会话的对话记忆（不删已生成的报告） |
| `GET` | `/api/files?path=` | 列出 output 下的文件（限制在 output 内） |
| `GET` | `/api/download?path=` | 下载 output 下的文件（限制在 output 内） |
| `GET` | `/outputs/{path}` | output 目录的只读静态访问（报告 PDF 等） |
| `WS` | `/ws/{thread_id}` | 实时事件流 |

> `/api/cars/suggest`、`/api/cars/complaints`（车型联想、投诉案例核验）已实现但默认注释，需要时取消注释即可。

### WebSocket 事件

| event | 说明 |
|---|---|
| `session_created` | 会话工作目录已创建 |
| `stage` | 四维取证进度：`{stage: 行情/口碑/售后/车主/结论, status: running/done/failed}` |
| `source` | 一条信息来源，前端聚合成可点击的出处卡片 |
| `tool_start` | 某个工具开始执行 |
| `assistant_call` | 派活给某个子智能体 |
| `answer_chunk` | **最终回答的 token 增量**，前端边收边显示 |
| `task_result` | 完整最终回答（与 `answer_chunk` 累计结果一致） |
| `error` | 任务失败，前端复位状态 |


---

## 目录结构

```
deepsearch_agents/
├── main.py                     # 入口：uvicorn 启动
├── prompt/prompts.yaml         # 全部提示词（主智能体+4个子智能体）
├── agent/
│   ├── main_agent.py           # 编排、日期注入、多轮记忆、流式事件解析
│   ├── llm.py                  # 模型客户端
│   ├── load_prompt.py          # 提示词加载
│   └── sub_agents/             # market / review / aftersales / owner
├── api/
│   ├── server.py               # HTTP + WebSocket + 静态托管
│   ├── monitor.py              # 事件总线，把进度投递到对应 WebSocket
│   └── context.py              # ContextVar 请求级隔离（会话目录 / thread_id）
├── tools/
│   ├── car_db_tool.py          # 只读查询：投诉案例 + 车型价格 + 月销量
│   ├── internet_search_tool.py # Tavily 检索（站点白名单 / 时间范围 / 来源上报）
│   ├── webpage_tool.py         # 正文抓取+熔断
│   ├── budget.py               # 每轮检索预算
│   ├── upload_file_tool.py     # 读 txt/md/docx/pdf/xlsx
│   ├── markdown_tool.py        # 写报告
│   └── pdf_tool.py             # Markdown → PDF（Word COM）
├── utils/                      # 日志、路径清洗、Word 转 PDF
├── tests/golden_cases.md       # 回归用例（含反向用例：禁止出现什么）
└── UI/                         # 前端
```

---

## 免责声明

本项目输出仅为购车决策的辅助信息，价格、政策、质保条款等均来自公开渠道，可能存在滞后或地区差异，**最终以品牌官网、门店报价与官方公告为准**。
