"""
主智能体
"""
import shutil
from datetime import date
from pathlib import Path

from deepagents import create_deep_agent
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver

from agent.llm import llm
from agent.load_prompt import main_agent_config
from agent.sub_agents.aftersales_sub_agent import aftersales_sub_agent
from agent.sub_agents.market_sub_agent import market_sub_agent
from agent.sub_agents.owner_sub_agent import owner_sub_agent
from agent.sub_agents.review_sub_agent import review_sub_agent
from api.context import reset_session_context, set_session_context, set_thread_context
from api.monitor import monitor
from tools.budget import reset as reset_search_budget
from tools.markdown_tool import generate_markdown
from tools.pdf_tool import convert_md_to_pdf
from tools.upload_file_tool import read_file_content
from utils.logger_utils import logger

_checkpointer = MemorySaver()

main_agent = create_deep_agent(
    model=llm,
    system_prompt=main_agent_config["system_prompt"],
    tools=[read_file_content, generate_markdown, convert_md_to_pdf],
    subagents=[market_sub_agent, review_sub_agent, aftersales_sub_agent, owner_sub_agent],
    checkpointer=_checkpointer,
)

_STAGE_BY_AGENT = {
    agent["name"]: stage
    for agent, stage in (
        (market_sub_agent, "行情"),
        (review_sub_agent, "口碑"),
        (aftersales_sub_agent, "售后"),
        (owner_sub_agent, "车主"),
    )
}

# 获取项目的根路径
project_root = Path(__file__).parent.parent.resolve()


def _prepare_session_environment(thread_id: str):
    """
    初始化会话运行环境
    """
    # 设置工作目录
    session_dir = project_root / "output" / f"session_{thread_id}"
    # 创建工作目录
    session_dir.mkdir(parents=True, exist_ok=True)

    # 标准化字符串路径
    session_dir_str = str(session_dir).replace("\\", "/")
    # 获取工作目录相
    relative_session_dir = str(session_dir.relative_to(project_root)).replace("\\", "/")
    # 获取上传文件的目录
    upload_dir = project_root / "upload" / f"session_{thread_id}"
    # 创建存储上传信息的变量
    upload_info = " "
    if upload_dir.exists():
        # 获取upload_dir中的所有文件名
        file_names = [item.name for item in upload_dir.iterdir() if item.is_file()]
        if file_names:
            for file_name in file_names:
                shutil.copy2(upload_dir / file_name, session_dir / file_name)
            upload_info = (
                "\n    [已上传文件]已加载到工作目录：\n"
                + "\n".join([f"   -{f}" for f in file_names])
                + "\n   请先使用工具读取参考这些文件 "
            )
    return session_dir_str, relative_session_dir, upload_info


def _process_stream_chunk(chunk, task_tracker=None):
    """
    处理 LangGraph 流式输出的增量状态
    """
    reported_result = False
    tracker = task_tracker if task_tracker is not None else {}

    for node_name, state in chunk.items():
        if not state or "messages" not in state:
            continue
        messages = state["messages"]
        if not isinstance(messages, list) or not messages:
            continue

        # 子智能体返回，把对应维度标记完成
        if node_name == "tools":
            for msg in messages:
                if isinstance(msg, ToolMessage):
                    agent_name = tracker.pop(msg.tool_call_id, None)
                    if agent_name:
                        stage = _STAGE_BY_AGENT.get(agent_name, agent_name)
                        failed = str(getattr(msg, "status", "") or "").lower() == "error"
                        monitor.report_stage(
                            stage,
                            "failed" if failed else "done",
                            str(msg.content or "")[:120],
                        )
            continue
        if node_name != "model":
            continue

        last_msg = messages[-1]
        if not isinstance(last_msg, AIMessage):
            continue

        # 调用工具
        if last_msg.tool_calls:
            for tool in last_msg.tool_calls:
                if tool["name"] == "task":
                    agent_name = tool["args"].get("subagent_type", "Agent")
                    tracker[tool.get("id", "")] = agent_name
                    stage = _STAGE_BY_AGENT.get(agent_name, agent_name)
                    monitor.report_stage(stage, "running", tool["args"].get("description") or "")
                    monitor.report_assistant(
                        agent_name,
                        {"desc": tool["args"].get("description")},
                    )
        # 生成最终回复
        elif last_msg.content:
            monitor.report_task_result(last_msg.content)
            reported_result = True
    return reported_result


def reset_thread_memory(thread_id: str) -> bool:
    """
    清空会话的对话记忆
    """
    try:
        _checkpointer.delete_thread(thread_id)
        return True
    except Exception as e:
        logger.warning(f"清空会话记忆失败，thread_id={thread_id}: {e}")
        return False


def _emit_answer_delta(payload) -> None:
    """
    把模型逐 token 输出转发给前端。
    """
    try:
        chunk, meta = payload
    except (TypeError, ValueError):
        return
    if not isinstance(meta, dict) or meta.get("langgraph_node") != "model":
        return
    if "|" in str(meta.get("langgraph_checkpoint_ns") or ""):
        return

    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") in ("text", "text_delta"):
                parts.append(item.get("text") or "")
        text = "".join(parts)
    else:
        text = ""

    if text:
        monitor.report_answer_chunk(text)


async def run_deep_agent(query: str, thread_id: str):
    """
    触发deepagent的调用
    :param query: 用户问题
    :param thread_id: 会话 ID（由服务端保证非空）
    :return:
    """
    session_token = None
    thread_token = None
    reported_result = False
    try:
        thread_token = set_thread_context(thread_id)
        # 获取数据
        session_dir_str, relative_session_dir, upload_info = _prepare_session_environment(thread_id)
        # 存储会话id路径
        session_token = set_session_context(session_dir_str)
        # 向客户端页面返回当前会话对应的地址信息
        monitor.report_session_dir(path=session_dir_str)

        reset_search_budget(thread_id)

        # 注入对话日期
        today = date.today().isoformat()
        path_instruction = f"""
                【运行环境】
                今天日期: {today}
                工作目录: {relative_session_dir}
                {upload_info}

                规则：
                1. 新生成文件必须保存到工作目录：'{relative_session_dir}/filename'
                2. 读取已上传的文件时，请直接将文件名（例如：'开票单.txt'）作为 filename 参数传入（read_file_content）读取工具，不要带上任何目录前缀。
                3. 使用相对路径，禁止使用绝对路径
                4. 若存在上传文件，请先分析内容（用户可能上传了报价单、配置表或对比清单）
                5. 所有价格、销量、政策类信息必须标注日期；日期以今天为基准判断「是否过期」
                """
        config = {"configurable": {"thread_id": thread_id}}
        tracker = {}
        async for mode, payload in main_agent.astream(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": f"用户的问题{query},额外描述{path_instruction}",
                    }
                ]
            },
            config=config,
            stream_mode=["updates", "messages"],
        ):
            if mode == "messages":
                _emit_answer_delta(payload)
                continue
            reported_result = _process_stream_chunk(payload, tracker) or reported_result

        if not reported_result:
            monitor.report_error("任务已结束，但智能体未返回最终回复")
    except Exception as e:
        logger.error(f"[Agent] 任务执行失败: {e}", exc_info=True)
        monitor.report_error(f"任务执行失败: {e}")

    finally:
        # 清理上下文
        try:
            reset_session_context(session_token, thread_token)
        except Exception as e:
            logger.warning(f"清理上下文失败: {e}")
