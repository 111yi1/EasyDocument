"""文件夹扫描：只读文件系统，返回 FileInfo 列表，不碰数据库。

作为纯函数模块，便于单元测试；调用方（UI）负责在主线程写库。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from . import dates
from .config import DEFAULT_EXTENSIONS

# 系统/临时文件，不纳入索引
SKIP_NAMES = {"thumbs.db", "desktop.ini"}
TEMP_PREFIX = "~$"
FILE_ATTRIBUTE_HIDDEN = 0x2


@dataclass(frozen=True)
class FileInfo:
    path: Path
    rel_path: str
    name: str
    ext: str
    size: int
    mtime: str
    ctime: str
    file_id: str | None = None

    @property
    def mtime_day(self) -> str:
        """文件修改日期（'YYYY-MM-DD'），用作导入时的默认记录日期。"""
        return self.mtime[:10] if self.mtime else dates.today_str()


def is_document_file(
    name: str, extensions: tuple[str, ...] = DEFAULT_EXTENSIONS
) -> bool:
    lower = name.lower()
    return any(lower.endswith(ext) for ext in extensions)


def path_key(path: str | Path) -> str:
    """Windows 下大小写不敏感的路径比较键。"""
    return os.path.normcase(os.path.abspath(str(path)))


def folder_accessible(path: str | Path) -> bool:
    """文件夹是否可列出（盘符离线、权限拒绝时返回 False）。"""
    try:
        with os.scandir(path) as it:
            next(it, None)
        return True
    except OSError:
        return False


def scan_folder(
    folder_path: str | Path,
    extensions: tuple[str, ...] = DEFAULT_EXTENSIONS,
    recursive: bool = True,
) -> list[FileInfo]:
    """扫描文件夹中的受管文档（Word / PowerPoint / Markdown）。"""
    root = Path(folder_path)
    result: list[FileInfo] = []
    if not root.is_dir():
        return result

    stack = [str(root)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                entries = list(it)
        except OSError:
            continue  # 无权限的子目录直接跳过

        for entry in entries:
            name = entry.name
            lower = name.lower()
            if lower in SKIP_NAMES or name.startswith(TEMP_PREFIX):
                continue
            try:
                stat = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if _is_hidden(stat):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    if recursive and not entry.is_symlink():
                        stack.append(entry.path)
                    continue
            except OSError:
                continue
            if not is_document_file(name, extensions):
                continue
            result.append(_make_info(Path(entry.path), root, stat))

    result.sort(key=lambda fi: (fi.name.lower(), fi.rel_path))
    return result


def make_file_info(path: str | Path, root: str | Path) -> FileInfo:
    """为单个文件生成与扫描结果一致的 FileInfo（拖入移动后立即入库用）。"""
    target = Path(path)
    stat = target.stat()
    return _make_info(target, Path(root), stat)


def _make_info(path: Path, root: Path, stat: os.stat_result) -> FileInfo:
    try:
        rel_path = os.path.relpath(path, root)
    except ValueError:
        rel_path = path.name
    # DirEntry.stat() 在 Windows 上返回的 st_ino/st_dev 恒为 0（走的是快速路径），
    # 这里补一次 os.stat 以取得文件卷号 + 文件索引，供 v0.2 识别重命名/移动。
    inode = 0
    device = 0
    try:
        full = os.stat(path)
        inode = getattr(full, "st_ino", 0)
        device = getattr(full, "st_dev", 0)
    except OSError:
        pass
    file_id = f"{device}:{inode}" if inode else None
    return FileInfo(
        path=path,
        rel_path=rel_path,
        name=path.name,
        ext=path.suffix.lower(),
        size=int(stat.st_size),
        mtime=dates.timestamp_iso(stat.st_mtime),
        ctime=dates.timestamp_iso(stat.st_ctime),
        file_id=file_id,
    )


def _is_hidden(stat: os.stat_result) -> bool:
    attrs = getattr(stat, "st_file_attributes", 0)
    return bool(attrs & FILE_ATTRIBUTE_HIDDEN)


def match_by_file_id(
    missing_docs: list,
    candidates: list[FileInfo],
) -> list[tuple[object, FileInfo]]:
    """按 file_id（st_dev:st_ino）把“缺失的旧路径”与“扫描到的新路径”配对。

    Windows 同卷内重命名/移动不会改变文件索引号，因此可据此自动重连。
    每个新文件最多配对一次；file_id 缺失（如网络盘）的项不参与匹配。
    """
    by_id: dict[str, FileInfo] = {}
    for fi in candidates:
        if fi.file_id:
            by_id.setdefault(fi.file_id, fi)
    pairs: list[tuple[object, FileInfo]] = []
    for doc in missing_docs:
        file_id = _field(doc, "file_id")
        if not file_id:
            continue
        fi = by_id.pop(file_id, None)
        if fi is not None:
            pairs.append((doc, fi))
    return pairs


def _field(obj, key: str, default=None):
    """兼容 sqlite3.Row / dict 的字段读取。"""
    try:
        return obj[key]
    except (KeyError, IndexError, TypeError):
        return default
