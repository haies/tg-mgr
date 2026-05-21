"""HTML 模板处理"""
from pathlib import Path


def load_html_template() -> str:
    """加载 HTML 模板文件"""
    template_path = Path(__file__).parent / "export.template.html"
    if not template_path.exists():
        raise FileNotFoundError(f"HTML 模板文件不存在: {template_path}")
    with open(template_path, encoding="utf-8") as f:
        return f.read()