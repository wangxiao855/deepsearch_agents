
from pathlib import Path
from typing import Annotated, Optional
from api.monitor import monitor
from api.context import get_session_context
from utils.path_utils import resolve_path
from utils.converter_utils import convert_md_to_pdf_via_word

def convert_md_to_pdf(
        md_filename: Annotated[str, "要转换的文档路径"],
        pdf_filename: Annotated[Optional[str], "输出的PDF文件路径"] = None
) -> str:
    """
    将Markdown文档转换为PDF
    """
    monitor.report_tool("Markdown转PDF工具")

    try:
        session_dir = get_session_context()
        md_path = Path(md_filename).with_suffix('.md')
        md_abs_path = Path(resolve_path(str(md_path), session_dir))

        if not md_abs_path.exists():
            return f"错误：文件不存在 {md_abs_path}"

        # 确定输出路径
        if pdf_filename:
            pdf_path = Path(pdf_filename).with_suffix('.pdf')
            pdf_abs_path = Path(resolve_path(str(pdf_path), session_dir))
        else:
            pdf_abs_path = md_abs_path.with_suffix('.pdf')

        # 调用核心转换逻辑
        return convert_md_to_pdf_via_word(md_abs_path, pdf_abs_path)

    except Exception as e:
        return f"转换失败: {str(e)}"
