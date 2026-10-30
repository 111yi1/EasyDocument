"""文件操作：用默认程序打开文档、在资源管理器中定位，以及用户主动拖入后的移动。

默认只做"打开/定位"，绝不修改、移动或删除用户文件；唯一的例外是 v0.5
的“把文件拖入窗口并确认移动”功能（`move_file`），且始终由用户操作触发。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .scanner import path_key


class FileAccessError(RuntimeError):
    """文件不存在或无法通过系统打开。"""


def open_file(path: str | Path) -> None:
    target = Path(path)
    if not target.is_file():
        raise FileAccessError(f"文件不存在：{target}")
    try:
        os.startfile(str(target))  # type: ignore[attr-defined]  # Windows 专用
    except OSError as exc:
        raise FileAccessError(f"无法打开文件：{target}\n{exc}") from exc


def reveal_in_explorer(path: str | Path) -> None:
    """在资源管理器中选中文件；文件已不存在时退化为打开所在目录。"""
    target = Path(path)
    if target.exists():
        args = ["explorer", f"/select,{target}"]
    else:
        parent = target.parent
        if not parent.exists():
            raise FileAccessError(f"文件与所在目录都不存在：{target}")
        args = ["explorer", str(parent)]
    try:
        subprocess.Popen(args, close_fds=True)
    except OSError as exc:
        raise FileAccessError(f"无法打开资源管理器：{target}\n{exc}") from exc


def unique_destination(
    directory: str | Path,
    name: str,
    reserved: set[str] | None = None,
) -> Path:
    """在目标文件夹中找一个不会覆盖已有文件/索引的名字。

    同名时依次尝试「名称 (2).ext」「名称 (3).ext」；`reserved` 是额外保留的
    路径键（由 `scanner.path_key` 生成），用于避开数据库中已存在但磁盘上
    暂时缺失的索引路径。
    """
    folder = Path(directory)
    safe_name = Path(name).name
    if not safe_name:
        raise FileAccessError("文件名为空，无法移动。")
    stem, suffix = Path(safe_name).stem, Path(safe_name).suffix
    reserved_keys = set(reserved or ())
    for index in range(1, 1000):
        candidate_name = safe_name if index == 1 else f"{stem} ({index}){suffix}"
        candidate = folder / candidate_name
        if path_key(candidate) in reserved_keys:
            continue
        if candidate.exists():
            continue
        return candidate
    raise FileAccessError(f"目标文件夹中同名文件过多，无法为 {safe_name} 生成新名字。")


def move_file(source: str | Path, destination: str | Path) -> Path:
    """把文件移动到目标路径（目标必须不存在）；跨卷时自动复制后删除源文件。

    只用于用户在界面上明确确认的拖入移动；失败时抛出 FileAccessError。
    """
    src = Path(source)
    dst = Path(destination)
    if not src.is_file():
        raise FileAccessError(f"源文件不存在或不是普通文件：{src}")
    if dst.exists():
        raise FileAccessError(f"目标位置已存在同名文件，已取消：{dst}")
    try:
        shutil.move(str(src), str(dst))
    except OSError as exc:
        raise FileAccessError(f"无法移动文件：{src}\n{exc}") from exc
    return dst
