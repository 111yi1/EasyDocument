"""程序入口：DPI 感知 → 数据目录/日志 → 建库/迁移/备份 → 启动界面。"""

from __future__ import annotations

import argparse
import ctypes
import sys
from tkinter import messagebox


def enable_dpi_awareness() -> None:
    """Windows 高 DPI 下避免界面发虚；失败时静默忽略。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()  # type: ignore[attr-defined]
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="EasyDocument", description="本地 Word 文档索引与工作日志工具"
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="自定义数据目录（默认 %%APPDATA%%\\EasyDocument）",
    )
    args = parser.parse_args(argv)

    enable_dpi_awareness()

    from easy_document import db, repo, storage
    from easy_document.config import setup_logging

    paths, data_source, startup_warning = storage.prepare_paths(args.data_dir)
    log = setup_logging(paths)
    log.info("启动 EasyDocument，数据目录：%s（来源：%s）", paths.data_dir, data_source)
    if startup_warning:
        log.warning(startup_warning)

    try:
        conn = db.connect(paths.db_path)
        existing_version = db.schema_version(conn)
        backup = None
        # 已有数据库先备份再迁移：升级出问题时可回到升级前的当日备份
        if existing_version:
            keep = repo.get_int(conn, "backup.keep", 7)
            backup = db.backup_daily(conn, paths.backups_dir, keep=keep)
        db.migrate(conn)
        if not existing_version:
            keep = repo.get_int(conn, "backup.keep", 7)
            backup = db.backup_daily(conn, paths.backups_dir, keep=keep)
        if backup:
            log.info("已创建数据库备份：%s", backup)
    except Exception:
        log.exception("初始化数据库失败")
        raise

    from easy_document.ui.app import App

    try:
        app = App(paths, conn, log, data_source=data_source)
        if startup_warning:
            app.after(
                600,
                lambda: messagebox.showwarning("数据目录提示", startup_warning),
            )
        app.mainloop()
    except Exception:
        log.exception("未捕获的界面异常")
        raise
    finally:
        conn.close()
        log.info("退出 EasyDocument")
    return 0


if __name__ == "__main__":
    sys.exit(main())
