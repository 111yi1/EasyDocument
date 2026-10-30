"""SQLite 连接、建表、版本迁移与备份。

约定：
- 连接只在主线程使用（Tkinter 主线程）。
- 表结构变更通过递增 SCHEMA_VERSION 并补充迁移语句完成。
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date
from pathlib import Path

from .config import DEFAULT_EXTENSIONS, LEGACY_DEFAULT_EXTENSIONS

SCHEMA_VERSION = 3

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

-- 受管文件夹
CREATE TABLE IF NOT EXISTS folders (
    id           INTEGER PRIMARY KEY,
    path         TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    recursive    INTEGER NOT NULL DEFAULT 1,
    auto_add     INTEGER NOT NULL DEFAULT 0,
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    last_scan_at TEXT
);

-- 文档索引（不存文件内容）
CREATE TABLE IF NOT EXISTS documents (
    id            INTEGER PRIMARY KEY,
    folder_id     INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
    path          TEXT NOT NULL UNIQUE,
    rel_path      TEXT NOT NULL,
    name          TEXT NOT NULL,
    ext           TEXT NOT NULL,
    size          INTEGER NOT NULL DEFAULT 0,
    mtime         TEXT,
    ctime         TEXT,
    added_at      TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',
    missing_since TEXT,
    file_id       TEXT
);
CREATE INDEX IF NOT EXISTS idx_doc_folder ON documents(folder_id);
CREATE INDEX IF NOT EXISTS idx_doc_status ON documents(status);

-- 日期记录（日历的唯一数据来源）
CREATE TABLE IF NOT EXISTS entries (
    id          INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    day         TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT 'manual',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entry_day ON entries(day);
CREATE INDEX IF NOT EXISTS idx_entry_doc ON entries(document_id);

-- 时间段（多日分组，v0.2 启用界面）
CREATE TABLE IF NOT EXISTS periods (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    color      TEXT,
    note       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS period_days (
    period_id INTEGER NOT NULL REFERENCES periods(id) ON DELETE CASCADE,
    day       TEXT NOT NULL,
    PRIMARY KEY (period_id, day)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_period_day ON period_days(day);

-- 逻辑图（v0.3 启用；v0.4 增加节点颜色与注释块）
CREATE TABLE IF NOT EXISTS board_nodes (
    id          INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL UNIQUE REFERENCES documents(id) ON DELETE CASCADE,
    x           REAL NOT NULL DEFAULT 40,
    y           REAL NOT NULL DEFAULT 40,
    label       TEXT NOT NULL DEFAULT '',
    color       TEXT
);
CREATE TABLE IF NOT EXISTS board_edges (
    id       INTEGER PRIMARY KEY,
    from_doc INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    to_doc   INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    label    TEXT NOT NULL DEFAULT '',
    UNIQUE (from_doc, to_doc)
);
-- 图板注释块（与文档无关的自由文本；folder_id 为 NULL 表示在所有文件夹可见）
CREATE TABLE IF NOT EXISTS board_notes (
    id         INTEGER PRIMARY KEY,
    folder_id  INTEGER REFERENCES folders(id) ON DELETE CASCADE,
    x          REAL NOT NULL DEFAULT 40,
    y          REAL NOT NULL DEFAULT 40,
    text       TEXT NOT NULL DEFAULT '',
    color      TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_board_note_folder ON board_notes(folder_id);

-- 键值设置
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def schema_version(conn: sqlite3.Connection) -> int:
    """当前数据库结构版本（新库为 0）。"""
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    version = schema_version(conn)
    if version == 0:
        conn.executescript(SCHEMA_SQL)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
        return
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"数据库结构版本 {version} 高于本程序支持的 {SCHEMA_VERSION}，请更新程序后再打开。"
        )
    if version < 2:
        _migrate_v1_to_v2(conn)
        version = 2
    if version < 3:
        _migrate_v2_to_v3(conn)
        version = 3
    if version != SCHEMA_VERSION:
        raise RuntimeError(f"无法从版本 {version} 迁移到 {SCHEMA_VERSION}。")


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """v1 → v2：把“从未改过的旧默认扩展名”扩展为 Word + PPT + Markdown。"""
    old_value = ",".join(LEGACY_DEFAULT_EXTENSIONS)
    new_value = ",".join(DEFAULT_EXTENSIONS)
    conn.execute(
        "UPDATE settings SET value = ? WHERE key = 'doc.extensions' AND value = ?",
        (new_value, old_value),
    )
    conn.execute("PRAGMA user_version = 2")
    conn.commit()


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    """v2 → v3：图板节点增加单独颜色，新增图板注释块。"""
    columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(board_nodes)")
    }
    if "color" not in columns:
        conn.execute("ALTER TABLE board_nodes ADD COLUMN color TEXT")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS board_notes (
            id         INTEGER PRIMARY KEY,
            folder_id  INTEGER REFERENCES folders(id) ON DELETE CASCADE,
            x          REAL NOT NULL DEFAULT 40,
            y          REAL NOT NULL DEFAULT 40,
            text       TEXT NOT NULL DEFAULT '',
            color      TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_board_note_folder ON board_notes(folder_id)"
    )
    conn.execute("PRAGMA user_version = 3")
    conn.commit()


def backup_daily(
    conn: sqlite3.Connection, backup_dir: str | Path, keep: int = 7
) -> Path | None:
    """每天首个启动时备份一次数据库；keep<=0 时跳过清理。返回新备份路径或 None。"""
    directory = Path(backup_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"app-{date.today():%Y%m%d}.db"
    if target.exists():
        return None

    backup_to(conn, target)
    if keep > 0:
        _prune_backups(directory, keep)
    return target


def backup_to(conn: sqlite3.Connection, target: str | Path) -> Path:
    """把当前数据库完整复制到指定文件（先写临时文件再替换）。"""
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        dst = sqlite3.connect(str(tmp))
        try:
            conn.backup(dst)
        finally:
            dst.close()
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return path


def _prune_backups(directory: Path, keep: int) -> None:
    backups = sorted(directory.glob("app-*.db"))
    for old in backups[:-keep]:
        try:
            old.unlink()
        except OSError:
            pass
