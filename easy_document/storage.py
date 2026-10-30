"""数据目录解析、迁移与清理。

数据目录优先级（高 → 低）：
1. 命令行 `--data-dir`
2. 环境变量 `EASY_DOCUMENT_DATA`
3. `%APPDATA%\\EasyDocument\\location.json` 中保存的路径
4. 默认 `%APPDATA%\\EasyDocument`

location.json 只保存几十字节的路径指针；数据库、备份、日志都写在新位置。
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path

from . import db
from .config import Paths, default_data_root

ENV_VAR = "EASY_DOCUMENT_DATA"
LOCATION_FILENAME = "location.json"


def location_file() -> Path:
    return default_data_root() / LOCATION_FILENAME


def read_location() -> Path | None:
    try:
        data = json.loads(location_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("data_dir")
    if not raw or not isinstance(raw, str):
        return None
    try:
        return Path(raw).expanduser()
    except (TypeError, ValueError):
        return None


def write_location(data_dir: Path | None) -> None:
    target = location_file()
    if data_dir is None:
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"data_dir": str(Path(data_dir).expanduser().resolve())},
        ensure_ascii=False,
        indent=2,
    )
    target.write_text(payload, encoding="utf-8")


def resolve_data_dir(explicit: str | Path | None = None) -> tuple[Path, str]:
    if explicit:
        return Path(explicit).expanduser(), "--data-dir"
    env_value = os.environ.get(ENV_VAR)
    if env_value:
        return Path(env_value).expanduser(), f"环境变量 {ENV_VAR}"
    saved = read_location()
    if saved:
        return saved, "用户设置（location.json）"
    return default_data_root(), "默认位置"


def prepare_paths(
    explicit: str | Path | None = None,
) -> tuple[Paths, str, str | None]:
    """解析并创建数据目录；失败时回退默认位置并返回警告文本。"""
    candidate, source = resolve_data_dir(explicit)
    try:
        paths = Paths.create(candidate)
    except OSError as exc:
        fallback = Paths.create(default_data_root())
        warning = (
            f"无法使用数据目录 {candidate}：{exc}\n"
            f"已回退到默认位置 {fallback.data_dir}。"
        )
        return fallback, "默认位置", warning
    return paths, source, None


def _norm(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _within(child: Path, parent: Path) -> bool:
    try:
        return os.path.commonpath([_norm(child), _norm(parent)]) == _norm(parent)
    except ValueError:
        return False


def _copy_files(source_dir: Path, target_dir: Path, pattern: str) -> list[str]:
    warnings: list[str] = []
    if not source_dir.is_dir():
        return warnings
    target_dir.mkdir(parents=True, exist_ok=True)
    for item in source_dir.glob(pattern):
        if not item.is_file():
            continue
        try:
            shutil.copy2(item, target_dir / item.name)
        except OSError as exc:
            warnings.append(f"{item}：{exc}")
    return warnings


def switch_data(
    conn: sqlite3.Connection,
    current: Paths,
    target: str | Path,
    copy: bool = True,
) -> tuple[Paths, list[str]]:
    """将数据位置切换到 target。

    copy=True：复制当前数据库（用 SQLite backup，安全）与备份/日志文件；
    copy=False：直接使用目标目录（已有数据库则沿用，否则重启后新建空库）。
    成功后才写入 location.json；失败抛异常且不改变指针。
    """
    target_path = Path(target).expanduser()
    try:
        target_path = target_path.resolve()
    except OSError:
        target_path = target_path.absolute()

    current_dir = current.data_dir
    if _norm(target_path) == _norm(current_dir):
        raise ValueError("新位置与当前数据目录相同。")
    if _within(target_path, current_dir) or _within(current_dir, target_path):
        raise ValueError("新位置不能是当前数据目录的子目录或父目录。")

    target_db = target_path / "app.db"
    if copy and target_db.exists():
        raise ValueError(
            "目标目录中已经有 EasyDocument 数据（app.db）。\n"
            "请选择空目录，或关闭「复制现有数据」以直接使用现有数据。"
        )

    paths = Paths.create(target_path)
    warnings: list[str] = []
    if copy:
        try:
            db.backup_to(conn, paths.db_path)
        except Exception:
            try:
                paths.db_path.unlink()
            except OSError:
                pass
            raise
        warnings.extend(_copy_files(current.backups_dir, paths.backups_dir, "*.db"))
        warnings.extend(_copy_files(current.logs_dir, paths.logs_dir, "*.log*"))
    write_location(paths.data_dir)
    return paths, warnings


def delete_data_files(paths: Paths) -> list[str]:
    """删除数据目录中的 EasyDocument 文件，返回未能删除的项。

    只处理 app.db*、backups/*.db、logs/*.log*，不碰目录中的其它文件。
    """
    targets: list[Path] = [
        paths.data_dir / "app.db",
        paths.data_dir / "app.db-wal",
        paths.data_dir / "app.db-shm",
    ]
    if paths.backups_dir.is_dir():
        targets.extend(paths.backups_dir.glob("*.db"))
    if paths.logs_dir.is_dir():
        targets.extend(paths.logs_dir.glob("*.log*"))
    errors: list[str] = []
    for item in targets:
        try:
            if item.exists():
                item.unlink()
        except OSError as exc:
            errors.append(f"{item}：{exc}")
    return errors
