import datetime
import asyncio
from utils.logger_utils import logger
from typing import Any, Dict, Optional
from fastapi import WebSocket
from api.context import get_thread_context
import builtins

class ToolMonitor:
    """
    工具监控类，用于在工具执行过程中上报进度和状态。
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ToolMonitor, cls).__new__(cls)
            cls._instance.websocket_manager = None  # 预留给 FastAPI WebSocketManager
        return cls._instance

    def set_websocket_manager(self, manager):
        """设置 FastAPI 的 WebSocket 管理器"""
        self.websocket_manager = manager

    def _emit(self, event_type: str, message: str, data: Optional[Dict[str, Any]] = None):
        """内部发送方法"""
        payload = {
            "type": "monitor_event",
            "event": event_type,
            "message": message,
            "data": data or {},
            "timestamp": datetime.datetime.now().isoformat()
        }

        if self.websocket_manager:
            try:
                # 获取当前线程
                thread_id = get_thread_context()

                # 确保loop已加载
                manager_loop = self.websocket_manager.loop

                if manager_loop:
                    if thread_id:
                        # 检查当前是否在同一个事件循环中
                        try:
                            current_loop = asyncio.get_running_loop()
                        except RuntimeError:
                            current_loop = None

                        if current_loop and current_loop == manager_loop:
                            # 如果在同一个循环中，直接创建任务
                            current_loop.create_task(
                                self.websocket_manager.send_to_thread(payload, thread_id)
                            )
                        else:
                            asyncio.run_coroutine_threadsafe(
                                self.websocket_manager.send_to_thread(payload, thread_id),
                                manager_loop
                            )
                    else:
                        pass
            except Exception as e:
                logger.error(f"WebSocket send failed: {e}")
        if builtins and hasattr(builtins, 'runtime') and hasattr(builtins.runtime, 'stream_writer'):
            try:
                builtins.runtime.stream_writer(payload)
            except Exception:
                pass

        # 控制台保底输出 (方便调试)
        logger.debug(f"[Monitor:{event_type}] {message}")

    def report_tool(self, tool_name: str, args: Dict[str, Any] = None):
        """报告工具开始执行"""
        self._emit("tool_start", f"开始执行工具: {tool_name}", {"tool_name": tool_name, "args": args})

    def report_assistant(self, assistant_name: str, args: Dict[str, Any] = None):
        """报告正在调用的子智能体进度"""
        self._emit("assistant_call", f"正在调用助手: {assistant_name}",
                   {"assistant_name": assistant_name, "args": args})

    def report_task_result(self, result: str):
        """报告任务最终结果"""
        self._emit("task_result", "任务执行完成", {"result": result})

    def report_error(self, message: str):
        """报告任务失败"""
        self._emit("error", message, {"result": message})

    def report_session_dir(self, path: str):
        """报告任务工作目录"""
        self._emit("session_created", f"工作目录已创建: {path}", {"path": path})

    def report_stage(self, stage: str, status: str = "running", detail: str = ""):
        """报告四个维度的进度"""
        self._emit(
            "stage",
            f"[{stage}] {status}",
            {"stage": stage, "status": status, "detail": detail},
        )

    def report_source(self, title: str, url: str = "", note: str = "", published: str = ""):
        """报告一条信息来源"""
        self._emit(
            "source",
            f"来源: {title}",
            {"title": title, "url": url, "note": note, "published": published},
        )

    def report_answer_chunk(self, text: str):
        """
        报告最终回答的增量文本（token 级），前端边收边拼。

        为什么需要它：一次购车咨询要跑好几分钟，如果只在最后发一次 task_result，
        用户会盯着转圈等上十分钟，不知道是在干活还是卡死了。
        这个事件让正文字一个一个出现，同时 task_result 仍会在结束时补发完整文本。
        """
        if not text:
            return
        self._emit("answer_chunk", text, {"text": text})


# 全局单例实例
monitor = ToolMonitor()


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.loop = None

    def set_loop(self, loop):
        """显式设置事件循环"""
        self.loop = loop
        monitor.set_websocket_manager(self)
        logger.debug(f"id:{id(self.loop)}")

    async def connect(self, websocket: WebSocket, thread_id: str):
        await websocket.accept()
        self.active_connections[thread_id] = websocket
        logger.debug(f"Client connected: {thread_id}")

    def disconnect(self, websocket: WebSocket, thread_id: str):
        if thread_id in self.active_connections:
            del self.active_connections[thread_id]
        logger.debug(f"客户端未连接: {thread_id}")

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def send_to_thread(self, message: dict, thread_id: str):
        if thread_id in self.active_connections:
            websocket = self.active_connections[thread_id]
            await websocket.send_json(message)

manager = ConnectionManager()
