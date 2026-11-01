
from pathlib import Path
from typing import Annotated
from langchain_core.tools import tool
from api.monitor import monitor
from api.context import  get_session_context
from utils.path_utils import resolve_path

# Markdown生成工具
def generate_markdown(
        content: Annotated[str, "要写入文档的文本内容"],
        filename: Annotated[str, "文档的文件名"],
        path: Annotated[str, "文件保存的绝对路径"] = ""
):
    """把文本内容写成 Markdown 文件，保存到当前会话的工作目录。
    用于交付报告：用户明确要求出报告 / 导出 / 整理成文档 / 发我时调用，
    先把结论写成 .md；需要 PDF 时再交给 convert_md_to_pdf 转换，不要跳过 Markdown。
    filename 使用有业务含义的中文名，不要带目录前缀。
    """
    monitor.report_tool("Markdown文档生成工具", {"写入的文本内容": content})

    if not filename.endswith('.md'):
        filename += '.md'

    # 获取上下文中的会话目录
    session_dir = get_session_context()

    if path and path != ".":
        full_input_path = str(Path(path) / filename)
    else:
        full_input_path = filename
    full_path_str = resolve_path(full_input_path, session_dir)
    file_path = Path(full_path_str)

    # 获取父目录
    parent_dir = file_path.parent

    try:
        if not parent_dir.exists():
            parent_dir.mkdir(parents=True, exist_ok=True)

        # 写入文本
        file_path.write_text(content, encoding='utf-8')

        return f"Markdown文件 '{file_path}' 已成功生成并保存。"
    except Exception as e:
        return f"生成Markdown文件失败: {str(e)}"
