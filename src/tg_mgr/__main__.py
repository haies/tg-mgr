"""
tg-mgr 命令行入口 - 支持 python -m tg_mgr
"""
import sys
from pathlib import Path

# 确保 src/ 在 sys.path 中
src_path = Path(__file__).parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from tg_mgr import main  # noqa: E402

if __name__ == "__main__":
    main()
