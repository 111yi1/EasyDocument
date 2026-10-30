"""全局配置：数据目录、常量、默认设置与日志。

本模块不 import 其它项目模块，避免循环依赖。
"""

from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path

APP_NAME = "EasyDocument"
APP_VERSION = "0.5.0"
APP_TITLE = "EasyDocument — 本地文档时间管理"

# 默认管理的文档扩展名（可在设置中增删）
# Word + PowerPoint + Markdown；均为“只索引、不解析内容”
DEFAULT_EXTENSIONS: tuple[str, ...] = (
    ".doc",
    ".docx",
    ".docm",
    ".ppt",
    ".pptx",
    ".pptm",
    ".md",
)

# v0.1 时代的默认值，仅用于数据库迁移时识别“从未改过设置”的用户
LEGACY_DEFAULT_EXTENSIONS: tuple[str, ...] = (".doc", ".docx", ".docm")

# 设置表默认值（首次运行时写入；之后以数据库为准）
DEFAULT_SETTINGS: dict[str, str] = {
    "doc.extensions": ",".join(DEFAULT_EXTENSIONS),
    "default.date_source": "mtime",  # mtime | added
    "auto.log_on_open": "0",
    "backup.keep": "7",
    "calendar.firstweekday": "0",  # 0=周一
    "view.default": "calendar",  # calendar | list | graph
    "scan.prompt_mtime": "1",
    "window.geometry": "",
    "doc.split": "0",
    "last.selected_day": "",
    # 关系图：字号与缩放（Ctrl+滚轮修改缩放；字号在设置页修改）
    "graph.font_size": "9",
    "graph.zoom_percent": "100",
}

# 时间段/图板节点的候选颜色（同时用于日历色条、关系图染色与图例）
PERIOD_COLORS: tuple[str, ...] = (
    "#2563eb",  # 蓝
    "#059669",  # 绿
    "#d97706",  # 橙
    "#dc2626",  # 红
    "#7c3aed",  # 紫
    "#0891b2",  # 青
    "#db2777",  # 玫红
    "#4b5563",  # 灰
    "#0d9488",  # 蓝绿
    "#65a30d",  # 黄绿
    "#ca8a04",  # 金
    "#92400e",  # 棕
    "#4f46e5",  # 靛蓝
    "#0284c7",  # 天蓝
    "#e11d48",  # 玫瑰红
    "#8b5cf6",  # 紫罗兰
    "#c026d3",  # 品红
    "#475569",  # 石板灰
    "#16a34a",  # 深绿
    "#f97316",  # 亮橙
)

# 界面字体与颜色（集中管理，避免各文件写死）
FONT_FAMILY = "Microsoft YaHei UI"
FONT_UI = (FONT_FAMILY, 9)
FONT_UI_BOLD = (FONT_FAMILY, 9, "bold")
FONT_SMALL = (FONT_FAMILY, 8)
FONT_TITLE = (FONT_FAMILY, 13, "bold")

COLOR_ACCENT = "#2563eb"
COLOR_TEXT = "#1f2937"
COLOR_MUTED = "#6b7280"
COLOR_BORDER = "#e5e7eb"
COLOR_BG = "#ffffff"
COLOR_CELL_HAS = "#f7f8fa"
COLOR_SELECTED_BG = "#dbeafe"
COLOR_MULTI_BG = "#fef3c7"
COLOR_TODAY = "#2563eb"
COLOR_HOVER = "#eef4ff"
COLOR_WARN = "#b45309"


def default_data_root() -> Path:
    """默认数据目录：%APPDATA%\\EasyDocument（与代码分离）。"""
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / ".easy-document"
    return (base / APP_NAME).resolve()


@dataclass(frozen=True)
class Paths:
    """应用数据目录布局。"""

    data_dir: Path
    db_path: Path
    logs_dir: Path
    backups_dir: Path

    @classmethod
    def create(cls, data_dir: str | Path | None = None) -> "Paths":
        root = Path(data_dir).expanduser() if data_dir else default_data_root()
        root = root.resolve()
        logs_dir = root / "logs"
        backups_dir = root / "backups"
        logs_dir.mkdir(parents=True, exist_ok=True)
        backups_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            data_dir=root,
            db_path=root / "app.db",
            logs_dir=logs_dir,
            backups_dir=backups_dir,
        )


def setup_logging(paths: Paths, level: int = logging.INFO) -> logging.Logger:
    """配置滚动文件日志；重复调用不会重复添加 handler。"""
    logger = logging.getLogger(APP_NAME)
    logger.setLevel(level)
    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    file_handler = RotatingFileHandler(
        paths.logs_dir / "app.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    if sys.stderr is not None:  # pythonw 下 stderr 为 None
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(fmt)
        logger.addHandler(stream)
    return logger


def normalize_extensions(text: str) -> tuple[str, ...]:
    """把 "doc, .pptx；md" 规整为 ('.doc', '.pptx', '.md')；无效输入回退默认值。"""
    items = [t.strip().lower() for t in re.split(r"[,;，；\s]+", text or "") if t.strip()]
    exts = []
    for item in items:
        ext = item if item.startswith(".") else "." + item
        if ext not in exts:
            exts.append(ext)
    return tuple(exts) or DEFAULT_EXTENSIONS
