"""
文件名清理工具

功能：
- 移除文件名中的非法字符（/ \\ : * ? " < > |）
- 替换连续空格为单个下划线
- 保留原始文件扩展名
"""
import re


def sanitize_filename(filename: str) -> str:
    """
    清理文件名中的非法字符

    参数:
    filename: 原始文件名

    返回:
    清理后的安全文件名
    """
    # 保留文件扩展名（如果有）
    name, ext = "", ""
    if '.' in filename:
        name, ext = filename.rsplit('.', 1)
        ext = f".{ext}"
    else:
        name = filename

    # 移除非法字符
    cleaned = re.sub(r'[\\/*?:"<>|]', '_', name)

    # 替换连续空格和特殊空白字符
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    cleaned = cleaned.replace(' ', '_')

    # 处理特殊情况
    if not cleaned:
        cleaned = "file"
    if cleaned.startswith('.'):
        cleaned = f"file{cleaned}"

    return f"{cleaned}{ext}"
