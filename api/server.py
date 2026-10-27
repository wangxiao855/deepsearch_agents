"""
API接口
"""
import asyncio
import shutil
import uuid
from pathlib import Path
from typing import List

import uvicorn
from fastapi import FastAPI, UploadFile, File, Form
from pydantic import BaseModel
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import FileResponse, HTMLResponse
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from agent.main_agent import reset_thread_memory, run_deep_agent
from api.monitor import manager
from tools.car_db_tool import query_complaint_cases, query_series_matches
from utils.logger_utils import logger

# 项目根目录路径
_project_root = Path(__file__).resolve().parent.parent

app = FastAPI(title="车问 · 购车参谋 API")

_output_dir = _project_root / "output"
_output_dir.mkdir(exist_ok=True)

_upload_dir = _project_root / "upload"
_upload_dir.mkdir(exist_ok=True)

app.mount("/outputs", StaticFiles(directory=str(_output_dir)), name="outputs")

# 配置跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# 定义任务请求的模型
class TaskRequest(BaseModel):
    query: str
    thread_id: str = None

@app.on_event("startup")
async def startup_event():
    """
    服务启动时，获取当前运行的事件循环，并绑定到 WebSocket 管理器
    """
    loop = asyncio.get_running_loop()
    manager.set_loop(loop)
    logger.debug(f"连接到事件循环: {id(loop)}")

def _log_task_exception(task: asyncio.Task):
    try:
        task.result()
    except asyncio.CancelledError:
        logger.warning("后台任务被取消")
    except Exception as e:
        logger.error(f"后台任务异常: {e}", exc_info=True)

@app.post("/api/task")
async def run_task(request: TaskRequest):
    """
    智能体任务启动
    """
    thread_id = request.thread_id or str(uuid.uuid4())
    logger.info(f"[Server] 收到任务 thread_id={thread_id} query={request.query[:80]!r}")

    task = asyncio.create_task(run_deep_agent(request.query, thread_id))

    task.add_done_callback(_log_task_exception)

    return {"status": "started", "thread_id": thread_id}


@app.post("/api/upload")
async def upload_files(files: List[UploadFile] = File(...), thread_id: str = Form(...)):
    """文件上传接口"""
    thread_id = (thread_id or "").strip()
    if not thread_id:
        return {"error": "缺少 thread_id，无法确定上传到哪个会话目录"}
    target_dir = _upload_dir / f"session_{thread_id}"
    target_dir.mkdir(parents=True, exist_ok=True)

    saved_files = []
    # 遍历并写入文件
    for file in files:
        safe_name = Path(file.filename or "").name
        if not safe_name:
            continue
        file_path = target_dir / safe_name
        with file_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        saved_files.append(safe_name)
    return {"status": "uploaded", "files": saved_files}


@app.get("/api/download")
async def download_file(path: str):
    """文件下载接口"""

    abs_path = Path(path).resolve()
    output_abs = _output_dir.resolve()

    if not abs_path.is_relative_to(output_abs):
        return {"error": "拒绝访问"}
    if not abs_path.exists():
        return {"error": "文件不存在"}
    return FileResponse(abs_path, filename=abs_path.name)


@app.get("/api/files")
async def list_files(path: str):
    """文件列表查询"""
    logger.debug(f"[Files] 请求文件列表: {path}")
    # 获取绝对路径对象
    abs_path = Path(path).resolve()
    # 是否存在
    if not abs_path.exists():
        return {"error": "目录不存在"}
    files = []
    try:
        # 递归查找所有文件
        for file_path in abs_path.rglob("*"):
            if file_path.is_file():
                stat = file_path.stat()
                files.append({
                    "name": file_path.name,
                    "type": "file",
                    "path": str(file_path),
                    "size": stat.st_size,
                    "mtime": stat.st_mtime
                })
    except Exception as e:
        logger.error(f"遍历文件失败: {e}")
        return {"error": str(e)}
    files.sort(key=lambda x: x.get("mtime", 0), reverse=True)
    return {"files": files}

# @app.get("/api/cars/suggest")
# async def suggest_cars(q: str = "", limit: int = 12):
#     """
#     车型联想接口
#     """
#     keyword = (q or "").strip()
#     if len(keyword) < 1:
#         return {"items": []}
#     try:
#         items = query_series_matches(keyword, limit)
#     except Exception as e:
#         logger.warning(f"车型联想失败: {e}")
#         return {"items": [], "error": f"本地库查询失败: {e}"}
#     return {"items": items, "source": "本地投诉样本"}
#
#
# @app.get("/api/cars/complaints")
# async def car_complaints(brand: str = "", series: str = "", keyword: str = "", limit: int = 20):
#     """
#     某车型的投诉案例
#     """
#     try:
#         cases = query_complaint_cases(brand, series, keyword, limit)
#     except Exception as e:
#         logger.warning(f"[Cars] 投诉案例查询失败: {e}")
#         return {"items": [], "error": f"本地库查询失败: {e}"}
#     return {
#         "items": cases,
#         "sample_note": "本地投诉样本（597 条，快照 2026-10-14），样本不等于全量，不代表投诉率",
#     }

#  post增  -用户点击新对话，网页发出post请求，被后端app.post拦截，并执行下方函数（为函数传参为自定义的数据模型的对话id）
@app.post("/api/session/reset")
async def reset_session(request: TaskRequest):
    """
    清空某个会话的对话记忆（前端「新对话」按钮用）。
    只清上下文，不删 output/ 下已生成的报告文件。
    """
    thread_id = (request.thread_id or "").strip()
    if not thread_id:
        return {"status": "ignored", "reason": "缺少 thread_id"}
    ok = reset_thread_memory(thread_id)
    return {"status": "reset" if ok else "failed", "thread_id": thread_id}

@app.websocket("/ws/{thread_id}")
async def websocket_endpoint(websocket: WebSocket, thread_id: str):
    """
    WebSocket 实时通讯
    """
    await manager.connect(websocket, thread_id)

    try:
        while True:
            data = await websocket.receive_text()

            await websocket.send_json({
                "type": "pong",
                "message": f"服务端已收到: {data}"
            })

    except WebSocketDisconnect:
        manager.disconnect(websocket, thread_id)
        logger.debug(f"[WebSocket] 客户端已断开: {thread_id}")

    except Exception as e:
        logger.error(f"[WebSocket] 连接异常: {e}", exc_info=True)
        manager.disconnect(websocket, thread_id)

_ui_dist = _project_root / "UI" / "dist"

if _ui_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_ui_dist), html=True), name="ui")
else:
    @app.get("/", include_in_schema=False)
    async def index_hint():
        """没有前端构建产物时，给一个能照着操作的说明页"""
        return HTMLResponse(
            "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
            "<title>车问 · 购车参谋</title></head>"
            "<body style='font-family:system-ui,sans-serif;max-width:760px;margin:60px auto;line-height:1.8'>"
            "<h2>后端已启动，但还没有前端构建产物</h2>"
            "<p><b>方式一（开发模式，改代码即时生效）：</b><br>"
            "<code>cd UI &amp;&amp; npm install &amp;&amp; npm run dev</code><br>"
            "然后打开 <a href='http://localhost:5173'>http://localhost:5173</a></p>"
            "<p><b>方式二（单端口，和后端同源）：</b><br>"
            "<code>cd UI &amp;&amp; npm install &amp;&amp; npm run build</code><br>"
            "然后刷新本页</p>"
            "<p>接口文档：<a href='/docs'>/docs</a></p>"
            "</body></html>"
        )


def start_server():
    uvicorn.run(app, host="0.0.0.0", port=8000)