"""
tg-mgr - Telegram 频道管理工具入口
"""
import sys
from pathlib import Path

# 确保 src/ 在 sys.path 中
src_path = Path(__file__).parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# Lazy import mapping - modules are imported only when first accessed
_LAZY_IMPORTS = {
    # External modules (from src/modules/)
    "clean": ("modules", "clean"),
    "export": ("modules", "export"),
    "filter": ("modules", "filter"),
    "forward": ("modules", "forward"),
    "info": ("modules", "info"),
    # Internal modules (from src/tg_mgr/)
    "init": ("tg_mgr", "init"),
    "sessions": ("tg_mgr", "sessions"),
}

_modules_cache = {}


def _get_module(name):
    """Lazy module loader."""
    if name not in _modules_cache:
        if name not in _LAZY_IMPORTS:
            raise ImportError(f"Unknown module: {name}")
        package, module_name = _LAZY_IMPORTS[name]
        if package == "modules":
            _modules_cache[name] = __import__(f"{package}.{module_name}", fromlist=[module_name])
        else:
            _modules_cache[name] = __import__(f"tg_mgr.{module_name}", fromlist=[module_name]).__getattribute__(module_name)
    return _modules_cache[name]


MODULES = {name: None for name in _LAZY_IMPORTS}


class _ModuleProxy:
    """Proxy object that lazy-loads the actual module on first attribute access."""
    def __init__(self, name):
        self._name = name

    def __getattr__(self, item):
        return getattr(_get_module(self._name), item)

    def __call__(self, *args, **kwargs):
        return _get_module(self._name)(*args, **kwargs)


def _get_all_modules():
    """Get all modules with lazy loading."""
    return {name: _ModuleProxy(name) for name in _LAZY_IMPORTS}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in _LAZY_IMPORTS:
        print("用法: tg <module> [args]")
        print("可用模块:", ", ".join(sorted(_LAZY_IMPORTS.keys())))
        sys.exit(1)

    module_name = sys.argv[1]
    module = _get_module(module_name)

    if not hasattr(module, "main"):
        print(f"模块 {module_name} 不支持命令行执行")
        sys.exit(1)

    # 保存原始 sys.argv，设置新参数让子模块正确解析
    original_argv = sys.argv
    sys.argv = [module_name] + sys.argv[2:]

    try:
        module.main()
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    main()
