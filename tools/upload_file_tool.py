"""
主智能体工具
"""
from pathlib import Path
from typing import Annotated

import docx
import pandas as pd
import pypdf

from api.context import get_session_context
from api.monitor import monitor
from utils.logger_utils import logger
from utils.path_utils import resolve_path


_PREVIEW_ROWS = 5

_DESCRIBE_MAX_COLUMNS = 15


def _read_text(file_path: Path) -> str:
    """读取文本文件"""
    try:
        return file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return file_path.read_text(encoding="gbk")


def _summarize_excel(file_path: Path, filename: str) -> str:
    """
    汇总 Excel 内容
    """
    sheets = pd.read_excel(str(file_path), sheet_name=None)
    if not sheets:
        return f"文件: {filename}\n该 Excel 文件没有任何工作表"

    blocks = [f"文件: {filename}", f"工作表数量: {len(sheets)}"]
    for sheet_name, df in sheets.items():
        blocks.append(f"\n=== 工作表: {sheet_name} ===")
        blocks.append(f"行数: {len(df)}, 列数: {len(df.columns)}")
        if df.empty:
            blocks.append("（空表）")
            continue
        blocks.append(f"列名: {', '.join(df.columns.astype(str))}")
        blocks.append(f"[前 {_PREVIEW_ROWS} 行预览]:")
        blocks.append(df.head(_PREVIEW_ROWS).to_string(index=False))

        numeric_df = df.select_dtypes(include="number")
        if numeric_df.empty:
            continue
        if len(numeric_df.columns) > _DESCRIBE_MAX_COLUMNS:
            blocks.append(f"[统计描述] 数值列过多（{len(numeric_df.columns)} 列），仅展示前 {_DESCRIBE_MAX_COLUMNS} 列:")
            numeric_df = numeric_df.iloc[:, :_DESCRIBE_MAX_COLUMNS]
        else:
            blocks.append("[统计描述]:")
        blocks.append(numeric_df.describe().to_string())
    return "\n".join(blocks)


#读取上传的文件
def read_file_content(
        filename: Annotated[str, "要读取的文件名，如 '开篇.txt'，不要带目录前缀"],
        instruction: Annotated[str, "读取说明，用于描述你想从该文件中获取什么信息"] = "",
) -> str:
    """
    读取指定文件，对于excel文件，会自动提供统计数据信息
    """
    monitor.report_tool(
        tool_name="读取上传的文件工具",
        args={
            "filename": filename,
            "instruction": instruction
        }
    )
    #获取工作目录路径
    session_dir = get_session_context()
    #获取上传的文件的具体路径
    try:
        file_path = Path(resolve_path(filename, session_dir))
    except Exception as e:
        logger.error(f"[read_file_content] 路径解析失败: {e}")
        return f"错误：路径解析失败 {e}"

    if not file_path.exists():
        return f"错误：文件不存在 {file_path}"
    if not file_path.is_file():
        return f"错误：{file_path} 不是文件"

    #判断文件后缀
    ext = file_path.suffix.lower()
    try:
        if ext in (".md", ".txt"):  #文本文件
            return _read_text(file_path)
        if ext == ".docx":  #word文档
            document = docx.Document(str(file_path))
            lines = [para.text for para in document.paragraphs]
            # 表格往往是报告的信息主体，一并取出
            for table in document.tables:
                for row in table.rows:
                    lines.append(" | ".join(cell.text.strip() for cell in row.cells))
            return "\n".join(line for line in lines if line.strip())
        if ext == ".pdf":  #pdf内容
            reader = pypdf.PdfReader(str(file_path))
            pages = [(page.extract_text() or "") for page in reader.pages]
            text = "\n".join(pages).strip()
            return text or f"文件: {filename}\n（该 PDF 未能提取出文本，可能是扫描件，需要 OCR）"
        if ext in (".xlsx", ".xls"):  #excel文件
            try:
                return _summarize_excel(file_path, filename)
            except ImportError:
                return ("错误：缺少读取该 Excel 的依赖。.xlsx 需要 openpyxl，"
                        ".xls 需要 xlrd，请在当前环境安装后重试")
        try:
            return _read_text(file_path)
        except UnicodeDecodeError:
            return f"错误：不支持的文件格式 '{ext}'，且无法作为文本读取"
    except Exception as e:
        logger.error(f"[read_file_content] 读取失败 {file_path}: {e}", exc_info=True)
        return f"错误：读取文件失败 {e}"
