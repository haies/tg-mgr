"""tg-mgr feature modules."""

from . import clean, export, filter, forward, info, sync

__all__ = ["clean", "export", "filter", "forward", "info", "sync"]

# Lazy imports for CLI entry points
_LAZY_IMPORTS = {
    "clean": ("modules.clean", "__init__"),
    "export": ("modules.export", "__init__"),
    "filter": ("modules", "filter"),
    "forward": ("modules.forward", "__init__"),
    "info": ("modules", "info"),
    "init": ("tg_mgr", "init"),
    "sessions": ("tg_mgr", "sessions"),
    "sync": ("modules", "sync"),
}
