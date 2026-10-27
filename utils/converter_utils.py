"""
将md转成pdf工具函数的模块
"""
from utils.logger_utils import logger
from pathlib import Path
import markdown
import win32com.client
import pythoncom

def convert_md_to_pdf_via_word(md_abs_path: Path, pdf_abs_path: Path) -> str:
    """
    Markdown转换为PDF
    """
    temp_html_path = md_abs_path.with_suffix('.temp.html')
    word_app = None

    try:
        with open(md_abs_path, 'r', encoding='utf-8') as f:
            md_content = f.read()

        html_body = markdown.markdown(md_content, extensions=['tables', 'fenced_code'])
        html_content = f"""
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{ font-family: "Microsoft YaHei", "SimHei", sans-serif; }}
                table {{ border-collapse: collapse; width: 100%; }}
                th, td {{ border: 1px solid black; padding: 8px; }}
                pre {{ background-color: #f5f5f5; padding: 10px; border-radius: 4px; }}
                code {{ font-family: "Consolas", "Monaco", monospace; }}
            </style>
        </head>
        <body>
            {html_body}
        </body>
        </html>
        """

        with open(temp_html_path, 'w', encoding='utf-8') as f:
            f.write(html_content)

        # 调用 Word COM
        pythoncom.CoInitialize()
        word_app = win32com.client.Dispatch('Word.Application')
        word_app.Visible = False
        word_app.DisplayAlerts = False

        doc = word_app.Documents.Open(str(temp_html_path.resolve()))
        doc.SaveAs(str(pdf_abs_path.resolve()), FileFormat=17)  # wdFormatPDF = 17
        doc.Close(SaveChanges=0)

        if pdf_abs_path.exists():
            return f"成功转换: {pdf_abs_path}"
        else:
            return f"转换完成但未生成文件: {pdf_abs_path}"
    except Exception as e:
        logger.error(f"Word转换PDF失败: {e}", exc_info=True)
        return f"转换失败: {str(e)}"
    finally:
        # 资源清理
        if word_app:
            try:
                word_app.Quit()
            except Exception as e:
                logger.warning(f"关闭Word应用时出错: {e}")

        if temp_html_path.exists():
            try:
                temp_html_path.unlink()
            except Exception as e:
                logger.warning(f"删除临时HTML文件时出错: {e}")

        try:
            pythoncom.CoUninitialize()
        except Exception as e:
            logger.warning(f"释放COM初始化时出错: {e}")