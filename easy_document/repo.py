"""数据访问层：所有 SQL 都在这里，UI 不直接操作 sqlite3。

返回值为 sqlite3.Row（可按列名取值），不额外定义 ORM 模型。
写操作内部 commit，调用方无需手动提交。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable

from . import dates

_UPDATE_DOC_FIELDS = {
    "path",
    "rel_path",
    "name",
    "ext",
    "size",
    "mtime",
    "ctime",
    "status",
    "missing_since",
    "file_id",
}

# 区分“不传该字段”与“显式传 None（清空颜色）”
_UNSET = object()

# ---------------------------------------------------------------------------
# 文件夹
# ---------------------------------------------------------------------------


def list_folders(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM folders ORDER BY name COLLATE NOCASE, path"
    ).fetchall()


def get_folder(conn: sqlite3.Connection, folder_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM folders WHERE id = ?", (folder_id,)).fetchone()


def get_folder_by_path(conn: sqlite3.Connection, path: str | Path) -> sqlite3.Row | None:
    resolved = str(Path(path).resolve())
    return conn.execute("SELECT * FROM folders WHERE path = ?", (resolved,)).fetchone()


def add_folder(
    conn: sqlite3.Connection,
    path: str | Path,
    recursive: bool = True,
    auto_add: bool = False,
) -> int:
    resolved = str(Path(path).resolve())
    if get_folder_by_path(conn, resolved):
        raise ValueError("该文件夹已在管理中")
    name = Path(resolved).name or resolved
    cursor = conn.execute(
        "INSERT INTO folders(path, name, recursive, auto_add, created_at)"
        " VALUES(?, ?, ?, ?, ?)",
        (resolved, name, int(recursive), int(auto_add), dates.iso_now()),
    )
    conn.commit()
    return int(cursor.lastrowid)


def update_folder(
    conn: sqlite3.Connection,
    folder_id: int,
    *,
    recursive: bool | None = None,
    auto_add: bool | None = None,
) -> None:
    sets, params = [], []
    if recursive is not None:
        sets.append("recursive = ?")
        params.append(int(recursive))
    if auto_add is not None:
        sets.append("auto_add = ?")
        params.append(int(auto_add))
    if not sets:
        return
    params.append(folder_id)
    conn.execute(f"UPDATE folders SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()


def touch_folder_scan(conn: sqlite3.Connection, folder_id: int) -> None:
    conn.execute(
        "UPDATE folders SET last_scan_at = ? WHERE id = ?",
        (dates.iso_now(), folder_id),
    )
    conn.commit()


def remove_folder(conn: sqlite3.Connection, folder_id: int) -> None:
    conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
    conn.commit()


def document_counts_by_folder(conn: sqlite3.Connection) -> dict[int, int]:
    rows = conn.execute(
        "SELECT folder_id, COUNT(*) AS c FROM documents"
        " WHERE status != 'ignored' GROUP BY folder_id"
    ).fetchall()
    return {int(r["folder_id"]): int(r["c"]) for r in rows}


# ---------------------------------------------------------------------------
# 文档
# ---------------------------------------------------------------------------


def get_document(conn: sqlite3.Connection, doc_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()


def get_document_by_path(
    conn: sqlite3.Connection, path: str | Path
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM documents WHERE path = ?", (str(path),)
    ).fetchone()


def list_documents(
    conn: sqlite3.Connection,
    *,
    folder_id: int | None = None,
    status: str | None = None,
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM documents WHERE 1 = 1"
    params: list[Any] = []
    if folder_id is not None:
        sql += " AND folder_id = ?"
        params.append(folder_id)
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY name COLLATE NOCASE"
    return conn.execute(sql, params).fetchall()


def add_document(
    conn: sqlite3.Connection,
    folder_id: int,
    path: str | Path,
    rel_path: str,
    name: str,
    ext: str,
    size: int,
    mtime: str | None,
    ctime: str | None,
    file_id: str | None = None,
) -> int:
    try:
        cursor = conn.execute(
            "INSERT INTO documents(folder_id, path, rel_path, name, ext, size,"
            " mtime, ctime, added_at, status, file_id)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)",
            (
                folder_id,
                str(path),
                rel_path,
                name,
                ext,
                int(size),
                mtime,
                ctime,
                dates.iso_now(),
                file_id,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"文件已在索引中：{path}") from exc
    conn.commit()
    return int(cursor.lastrowid)


def update_document(conn: sqlite3.Connection, doc_id: int, **fields: Any) -> None:
    sets, params = [], []
    for key, value in fields.items():
        if key not in _UPDATE_DOC_FIELDS:
            raise ValueError(f"不允许更新字段：{key}")
        sets.append(f"{key} = ?")
        params.append(value)
    if not sets:
        return
    params.append(doc_id)
    conn.execute(f"UPDATE documents SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()


def set_document_status(
    conn: sqlite3.Connection,
    doc_id: int,
    status: str,
    missing_since: str | None = None,
) -> None:
    conn.execute(
        "UPDATE documents SET status = ?, missing_since = ? WHERE id = ?",
        (status, missing_since, doc_id),
    )
    conn.commit()


def relink_document(
    conn: sqlite3.Connection,
    doc_id: int,
    *,
    path: str | Path,
    rel_path: str,
    name: str,
    ext: str,
    size: int,
    mtime: str | None,
    ctime: str | None,
    file_id: str | None = None,
    folder_id: int | None = None,
) -> None:
    """文件被移动/改名后重新指向新路径，历史记录保持不变。

    `folder_id` 用于文件被移到另一个受管文件夹的情况；为 None 时保持原文件夹。
    """
    conn.execute(
        "UPDATE documents SET folder_id = COALESCE(?, folder_id), path = ?,"
        " rel_path = ?, name = ?, ext = ?, size = ?, mtime = ?, ctime = ?,"
        " file_id = ?, status = 'active', missing_since = NULL"
        " WHERE id = ?",
        (
            folder_id,
            str(path),
            rel_path,
            name,
            ext,
            int(size),
            mtime,
            ctime,
            file_id,
            doc_id,
        ),
    )
    conn.commit()


def remove_document(conn: sqlite3.Connection, doc_id: int) -> None:
    """仅移除索引；不会删除磁盘文件。"""
    conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# 日期记录
# ---------------------------------------------------------------------------


def add_entry(
    conn: sqlite3.Connection,
    document_id: int,
    day: str,
    note: str = "",
    source: str = "manual",
) -> int:
    parsed = dates.try_parse_day(day)
    if parsed is None:
        raise ValueError(f"日期格式无效：{day}")
    now = dates.iso_now()
    cursor = conn.execute(
        "INSERT INTO entries(document_id, day, note, source, created_at, updated_at)"
        " VALUES(?, ?, ?, ?, ?, ?)",
        (document_id, parsed, note, source, now, now),
    )
    conn.commit()
    return int(cursor.lastrowid)


def update_entry(
    conn: sqlite3.Connection,
    entry_id: int,
    *,
    day: str | None = None,
    note: str | None = None,
) -> None:
    sets, params = [], []
    if day is not None:
        parsed = dates.try_parse_day(day)
        if parsed is None:
            raise ValueError(f"日期格式无效：{day}")
        sets.append("day = ?")
        params.append(parsed)
    if note is not None:
        sets.append("note = ?")
        params.append(note)
    if not sets:
        return
    sets.append("updated_at = ?")
    params.append(dates.iso_now())
    params.append(entry_id)
    conn.execute(f"UPDATE entries SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()


def delete_entry(conn: sqlite3.Connection, entry_id: int) -> None:
    conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
    conn.commit()


def get_entry(conn: sqlite3.Connection, entry_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()


def entries_for_document(
    conn: sqlite3.Connection, document_id: int
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM entries WHERE document_id = ? ORDER BY day DESC, id DESC",
        (document_id,),
    ).fetchall()


def documents_for_day(
    conn: sqlite3.Connection, day: str, folder_id: int | None = None
) -> list[sqlite3.Row]:
    """某天的文档（按文档去重），附当天最新备注与记录条数。

    folder_id 为 None 时返回全部文件夹；否则只返回该文件夹下的文档。
    """
    sql = """
        SELECT d.id AS id, d.name AS name, d.path AS path, d.mtime AS mtime,
               d.size AS size, d.status AS status,
               (SELECT e2.note FROM entries e2
                 WHERE e2.document_id = d.id AND e2.day = ?
                 ORDER BY e2.id DESC LIMIT 1) AS note,
               (SELECT COUNT(*) FROM entries e3
                 WHERE e3.document_id = d.id AND e3.day = ?) AS entry_count
        FROM documents d
        WHERE EXISTS (SELECT 1 FROM entries e
                       WHERE e.document_id = d.id AND e.day = ?)
          AND d.status != 'ignored'
    """
    params: list[Any] = [day, day, day]
    if folder_id is not None:
        sql += " AND d.folder_id = ?"
        params.append(folder_id)
    sql += " ORDER BY d.name COLLATE NOCASE"
    return conn.execute(sql, params).fetchall()


def day_counts(
    conn: sqlite3.Connection,
    start_day: str,
    end_day: str,
    folder_id: int | None = None,
) -> dict[str, int]:
    sql = (
        "SELECT e.day AS day, COUNT(DISTINCT e.document_id) AS c"
        " FROM entries e JOIN documents d ON d.id = e.document_id"
        " WHERE e.day BETWEEN ? AND ?"
    )
    params: list[Any] = [start_day, end_day]
    if folder_id is not None:
        sql += " AND d.folder_id = ?"
        params.append(folder_id)
    sql += " GROUP BY e.day"
    rows = conn.execute(sql, params).fetchall()
    return {str(r["day"]): int(r["c"]) for r in rows}


def day_titles(
    conn: sqlite3.Connection,
    start_day: str,
    end_day: str,
    folder_id: int | None = None,
) -> dict[str, list[str]]:
    """月历单元格用：每天的最新若干文档名（按当天记录从新到旧）。"""
    sql = """
        SELECT e.day AS day, d.name AS name, MAX(e.id) AS last_id
        FROM entries e JOIN documents d ON d.id = e.document_id
        WHERE e.day BETWEEN ? AND ? AND d.status != 'ignored'
    """
    params: list[Any] = [start_day, end_day]
    if folder_id is not None:
        sql += " AND d.folder_id = ?"
        params.append(folder_id)
    sql += " GROUP BY e.day, d.id ORDER BY e.day, last_id DESC"
    rows = conn.execute(sql, params).fetchall()
    result: dict[str, list[str]] = {}
    for row in rows:
        result.setdefault(str(row["day"]), []).append(str(row["name"]))
    return result


def recent_entries(
    conn: sqlite3.Connection, limit: int = 50, folder_id: int | None = None
) -> list[sqlite3.Row]:
    sql = """
        SELECT e.id AS entry_id, e.day AS day, e.note AS note,
               e.updated_at AS updated_at, e.source AS source,
               d.id AS doc_id, d.name AS name, d.path AS path, d.status AS status
        FROM entries e JOIN documents d ON d.id = e.document_id
        WHERE d.status != 'ignored'
    """
    params: list[Any] = []
    if folder_id is not None:
        sql += " AND d.folder_id = ?"
        params.append(folder_id)
    sql += " ORDER BY e.day DESC, e.id DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def search(
    conn: sqlite3.Connection,
    text: str,
    limit: int = 300,
    folder_id: int | None = None,
) -> list[sqlite3.Row]:
    """按文件名或备注子串搜索，返回匹配的记录（附日期与文档）。"""
    pattern = f"%{text}%"
    sql = """
        SELECT e.id AS entry_id, e.day AS day, e.note AS note,
               d.id AS doc_id, d.name AS name, d.path AS path, d.status AS status
        FROM entries e JOIN documents d ON d.id = e.document_id
        WHERE (d.name LIKE ? OR e.note LIKE ?)
    """
    params: list[Any] = [pattern, pattern]
    if folder_id is not None:
        sql += " AND d.folder_id = ?"
        params.append(folder_id)
    sql += " ORDER BY e.day DESC, e.id DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def has_entry_on_day(
    conn: sqlite3.Connection, document_id: int, day: str
) -> bool:
    """该文档在指定日期是否已有记录（避免重复补记）。"""
    row = conn.execute(
        "SELECT 1 FROM entries WHERE document_id = ? AND day = ? LIMIT 1",
        (document_id, day),
    ).fetchone()
    return row is not None


def timeline(
    conn: sqlite3.Connection,
    folder_id: int | None = None,
    limit: int = 1000,
) -> list[sqlite3.Row]:
    """按日期倒序列出记录（附文档信息），用于列表/时间轴视图。"""
    sql = """
        SELECT e.id AS entry_id, e.day AS day, e.note AS note,
               e.source AS source, e.updated_at AS updated_at,
               d.id AS doc_id, d.name AS name, d.path AS path, d.status AS status
        FROM entries e JOIN documents d ON d.id = e.document_id
        WHERE d.status != 'ignored'
    """
    params: list[Any] = []
    if folder_id is not None:
        sql += " AND d.folder_id = ?"
        params.append(folder_id)
    sql += " ORDER BY e.day DESC, e.id DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def documents_without_entries(
    conn: sqlite3.Connection, folder_id: int | None = None
) -> list[sqlite3.Row]:
    """没有任何日期记录的文档（“未归类”），列表视图单独成组。"""
    sql = (
        "SELECT d.* FROM documents d WHERE d.status != 'ignored'"
        " AND NOT EXISTS (SELECT 1 FROM entries e WHERE e.document_id = d.id)"
    )
    params: list[Any] = []
    if folder_id is not None:
        sql += " AND d.folder_id = ?"
        params.append(folder_id)
    sql += " ORDER BY d.name COLLATE NOCASE"
    return conn.execute(sql, params).fetchall()


def entries_in_days(
    conn: sqlite3.Connection,
    days: Iterable[str],
    folder_id: int | None = None,
) -> list[sqlite3.Row]:
    """某个日期集合（时间段）内的全部记录，按日期倒序。"""
    parsed = sorted({str(day) for day in days if day})
    if not parsed:
        return []
    placeholders = ", ".join("?" * len(parsed))
    sql = f"""
        SELECT e.id AS entry_id, e.day AS day, e.note AS note,
               e.source AS source, d.id AS doc_id, d.name AS name,
               d.path AS path, d.status AS status
        FROM entries e JOIN documents d ON d.id = e.document_id
        WHERE e.day IN ({placeholders}) AND d.status != 'ignored'
    """
    params: list[Any] = list(parsed)
    if folder_id is not None:
        sql += " AND d.folder_id = ?"
        params.append(folder_id)
    sql += " ORDER BY e.day DESC, d.name COLLATE NOCASE"
    return conn.execute(sql, params).fetchall()


# ---------------------------------------------------------------------------
# 时间段（多日分组）
# ---------------------------------------------------------------------------


def list_periods(
    conn: sqlite3.Connection, folder_id: int | None = None
) -> list[sqlite3.Row]:
    """列出时间段及其天数、首尾日期与文档数（文档数可按文件夹限定）。"""
    folder_filter = " AND d.folder_id = ?" if folder_id is not None else ""
    sql = f"""
        SELECT p.id AS id, p.name AS name, p.color AS color, p.note AS note,
               p.created_at AS created_at,
               (SELECT COUNT(*) FROM period_days pd WHERE pd.period_id = p.id)
                   AS day_count,
               (SELECT MIN(pd.day) FROM period_days pd WHERE pd.period_id = p.id)
                   AS first_day,
               (SELECT MAX(pd.day) FROM period_days pd WHERE pd.period_id = p.id)
                   AS last_day,
               (SELECT COUNT(DISTINCT e.document_id)
                  FROM period_days pd
                  JOIN entries e ON e.day = pd.day
                  JOIN documents d ON d.id = e.document_id
                 WHERE pd.period_id = p.id{folder_filter}) AS doc_count
        FROM periods p
        ORDER BY first_day IS NULL, first_day DESC, p.name COLLATE NOCASE
    """
    params: list[Any] = [folder_id] if folder_id is not None else []
    return conn.execute(sql, params).fetchall()


def get_period(conn: sqlite3.Connection, period_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM periods WHERE id = ?", (period_id,)
    ).fetchone()


def period_days(conn: sqlite3.Connection, period_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT day FROM period_days WHERE period_id = ? ORDER BY day",
        (period_id,),
    ).fetchall()
    return [str(r["day"]) for r in rows]


def period_day_map(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    """日期 → 所属时间段（一天最多属于一个时间段）。"""
    rows = conn.execute(
        "SELECT pd.day AS day, p.id AS period_id, p.name AS name, p.color AS color"
        " FROM period_days pd JOIN periods p ON p.id = pd.period_id"
    ).fetchall()
    return {str(r["day"]): r for r in rows}


def conflicting_days(
    conn: sqlite3.Connection,
    days: Iterable[str],
    exclude_period_id: int | None = None,
) -> list[str]:
    """返回已属于其它时间段的日期。"""
    parsed = _clean_days(days)
    if not parsed:
        return []
    placeholders = ", ".join("?" * len(parsed))
    sql = f"SELECT day FROM period_days WHERE day IN ({placeholders})"
    params: list[Any] = list(parsed)
    if exclude_period_id is not None:
        sql += " AND period_id != ?"
        params.append(exclude_period_id)
    rows = conn.execute(sql, params).fetchall()
    return sorted(str(r["day"]) for r in rows)


def create_period(
    conn: sqlite3.Connection,
    name: str,
    days: Iterable[str],
    color: str | None = None,
    note: str = "",
    move: bool = False,
) -> int:
    """新建时间段。日期已被其它时间段占用时，move=False 抛 ValueError。"""
    title = (name or "").strip()
    if not title:
        raise ValueError("时间段名称不能为空。")
    parsed = _clean_days(days)
    if not parsed:
        raise ValueError("请至少选择一个日期。")
    conflicts = conflicting_days(conn, parsed)
    if conflicts and not move:
        raise ValueError(_conflict_message(conflicts))
    try:
        cursor = conn.execute(
            "INSERT INTO periods(name, color, note, created_at) VALUES(?, ?, ?, ?)",
            (title, color, note, dates.iso_now()),
        )
        period_id = int(cursor.lastrowid)
        _detach_days(conn, conflicts)
        conn.executemany(
            "INSERT OR IGNORE INTO period_days(period_id, day) VALUES(?, ?)",
            [(period_id, day) for day in parsed],
        )
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return period_id


def update_period(
    conn: sqlite3.Connection,
    period_id: int,
    *,
    name: str | None = None,
    color: str | None = None,
    note: str | None = None,
) -> None:
    sets, params = [], []
    if name is not None:
        title = name.strip()
        if not title:
            raise ValueError("时间段名称不能为空。")
        sets.append("name = ?")
        params.append(title)
    if color is not None:
        sets.append("color = ?")
        params.append(color)
    if note is not None:
        sets.append("note = ?")
        params.append(note)
    if not sets:
        return
    params.append(period_id)
    conn.execute(f"UPDATE periods SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()


def add_period_days(
    conn: sqlite3.Connection,
    period_id: int,
    days: Iterable[str],
    move: bool = False,
) -> None:
    """把日期加入时间段；与其它时间段冲突时 move=False 抛 ValueError。"""
    parsed = _clean_days(days)
    if not parsed:
        raise ValueError("请至少选择一个日期。")
    conflicts = conflicting_days(conn, parsed, exclude_period_id=period_id)
    if conflicts and not move:
        raise ValueError(_conflict_message(conflicts))
    try:
        _detach_days(conn, conflicts)
        conn.executemany(
            "INSERT OR IGNORE INTO period_days(period_id, day) VALUES(?, ?)",
            [(period_id, day) for day in parsed],
        )
    except Exception:
        conn.rollback()
        raise
    conn.commit()


def set_period_days(
    conn: sqlite3.Connection,
    period_id: int,
    days: Iterable[str],
    move: bool = False,
) -> None:
    """整体替换时间段包含的日期（编辑用）。"""
    parsed = _clean_days(days)
    if not parsed:
        raise ValueError("请至少选择一个日期。")
    conflicts = conflicting_days(conn, parsed, exclude_period_id=period_id)
    if conflicts and not move:
        raise ValueError(_conflict_message(conflicts))
    try:
        conn.execute("DELETE FROM period_days WHERE period_id = ?", (period_id,))
        _detach_days(conn, conflicts)
        conn.executemany(
            "INSERT OR IGNORE INTO period_days(period_id, day) VALUES(?, ?)",
            [(period_id, day) for day in parsed],
        )
    except Exception:
        conn.rollback()
        raise
    conn.commit()


def remove_period_days(
    conn: sqlite3.Connection, period_id: int, days: Iterable[str]
) -> None:
    """只解除日期归属，记录与文档不受影响。"""
    parsed = _clean_days(days)
    if not parsed:
        return
    placeholders = ", ".join("?" * len(parsed))
    conn.execute(
        f"DELETE FROM period_days WHERE period_id = ? AND day IN ({placeholders})",
        [period_id, *parsed],
    )
    conn.commit()


def delete_period(conn: sqlite3.Connection, period_id: int) -> None:
    conn.execute("DELETE FROM periods WHERE id = ?", (period_id,))
    conn.commit()


def _clean_days(days: Iterable[str]) -> list[str]:
    """校验收敛日期列表：去除重复、按日期排序；含非法日期时抛 ValueError。"""
    result: list[str] = []
    for raw in days or []:
        parsed = dates.try_parse_day(str(raw))
        if parsed is None:
            raise ValueError(f"日期格式无效：{raw}")
        if parsed not in result:
            result.append(parsed)
    result.sort()
    return result


def _detach_days(conn: sqlite3.Connection, days: Iterable[str]) -> None:
    parsed = list(days)
    if not parsed:
        return
    placeholders = ", ".join("?" * len(parsed))
    conn.execute(f"DELETE FROM period_days WHERE day IN ({placeholders})", parsed)


def _conflict_message(days: list[str]) -> str:
    shown = "、".join(days[:8])
    more = f" 等 {len(days)} 天" if len(days) > 8 else ""
    return f"以下日期已属于其它时间段：{shown}{more}"


# ---------------------------------------------------------------------------
# 逻辑图（图板）
# ---------------------------------------------------------------------------


def list_board_nodes(
    conn: sqlite3.Connection, folder_id: int | None = None
) -> list[sqlite3.Row]:
    """图板节点及对应文档信息；folder_id 用于按当前文件夹作用域过滤。"""
    sql = """
        SELECT bn.id AS node_id, bn.document_id AS document_id, bn.x AS x,
               bn.y AS y, bn.label AS label, bn.color AS color,
               d.name AS name, d.path AS path, d.status AS status, d.ext AS ext,
               d.folder_id AS folder_id
        FROM board_nodes bn JOIN documents d ON d.id = bn.document_id
        WHERE d.status != 'ignored'
    """
    params: list[Any] = []
    if folder_id is not None:
        sql += " AND d.folder_id = ?"
        params.append(folder_id)
    sql += " ORDER BY bn.id"
    return conn.execute(sql, params).fetchall()


def board_document_ids(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute("SELECT document_id FROM board_nodes").fetchall()
    return {int(row["document_id"]) for row in rows}


def add_board_node(
    conn: sqlite3.Connection,
    document_id: int,
    x: float = 40.0,
    y: float = 40.0,
    label: str = "",
    color: str | None = None,
) -> bool:
    """把文档加入图板；已在图板上时返回 False（不移动原有位置）。"""
    cursor = conn.execute(
        "INSERT OR IGNORE INTO board_nodes(document_id, x, y, label, color)"
        " VALUES(?, ?, ?, ?, ?)",
        (int(document_id), float(x), float(y), label or "", color),
    )
    conn.commit()
    return cursor.rowcount > 0


def update_board_node(
    conn: sqlite3.Connection,
    document_id: int,
    *,
    x: float | None = None,
    y: float | None = None,
    label: str | None = None,
    color: str | None = _UNSET,
) -> None:
    """更新节点；color 显式传 None 表示清除单独颜色（恢复跟随时间段）。"""
    sets, params = [], []
    if x is not None:
        sets.append("x = ?")
        params.append(float(x))
    if y is not None:
        sets.append("y = ?")
        params.append(float(y))
    if label is not None:
        sets.append("label = ?")
        params.append(label)
    if color is not _UNSET:
        sets.append("color = ?")
        params.append(color)
    if not sets:
        return
    params.append(int(document_id))
    conn.execute(
        f"UPDATE board_nodes SET {', '.join(sets)} WHERE document_id = ?", params
    )
    conn.commit()


def update_board_nodes_positions(
    conn: sqlite3.Connection, positions: dict[int, tuple[float, float]]
) -> None:
    """批量保存节点坐标（拖动多选节点时一次提交）。"""
    if not positions:
        return
    try:
        for document_id, (x, y) in positions.items():
            conn.execute(
                "UPDATE board_nodes SET x = ?, y = ? WHERE document_id = ?",
                (float(x), float(y), int(document_id)),
            )
    except Exception:
        conn.rollback()
        raise
    conn.commit()


def remove_board_node(conn: sqlite3.Connection, document_id: int) -> None:
    """从图板移除节点，同时删掉与它相连的连线（不影响文档本身）。"""
    conn.execute(
        "DELETE FROM board_edges WHERE from_doc = ? OR to_doc = ?",
        (int(document_id), int(document_id)),
    )
    conn.execute(
        "DELETE FROM board_nodes WHERE document_id = ?", (int(document_id),)
    )
    conn.commit()


def remove_board_nodes(conn: sqlite3.Connection, document_ids: list[int]) -> None:
    """批量移除图板节点及相连连线。"""
    ids = [int(item) for item in document_ids]
    if not ids:
        return
    placeholders = ", ".join("?" * len(ids))
    try:
        conn.execute(
            f"DELETE FROM board_edges WHERE from_doc IN ({placeholders})"
            f" OR to_doc IN ({placeholders})",
            [*ids, *ids],
        )
        conn.execute(
            f"DELETE FROM board_nodes WHERE document_id IN ({placeholders})", ids
        )
    except Exception:
        conn.rollback()
        raise
    conn.commit()


def list_board_edges(
    conn: sqlite3.Connection, folder_id: int | None = None
) -> list[sqlite3.Row]:
    sql = """
        SELECT be.id AS edge_id, be.from_doc AS from_doc, be.to_doc AS to_doc,
               be.label AS label, df.name AS from_name, dt.name AS to_name
        FROM board_edges be
        JOIN documents df ON df.id = be.from_doc
        JOIN documents dt ON dt.id = be.to_doc
        WHERE df.status != 'ignored' AND dt.status != 'ignored'
    """
    params: list[Any] = []
    if folder_id is not None:
        sql += " AND df.folder_id = ? AND dt.folder_id = ?"
        params.extend([folder_id, folder_id])
    sql += " ORDER BY be.id"
    return conn.execute(sql, params).fetchall()


def add_board_edge(
    conn: sqlite3.Connection,
    from_doc: int,
    to_doc: int,
    label: str = "",
) -> int:
    if int(from_doc) == int(to_doc):
        raise ValueError("不能把文档连到它自己。")
    try:
        cursor = conn.execute(
            "INSERT INTO board_edges(from_doc, to_doc, label) VALUES(?, ?, ?)",
            (int(from_doc), int(to_doc), label or ""),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("这两篇文档之间已经有连线了。") from exc
    conn.commit()
    return int(cursor.lastrowid)


def update_board_edge(
    conn: sqlite3.Connection, edge_id: int, label: str
) -> None:
    conn.execute(
        "UPDATE board_edges SET label = ? WHERE id = ?", (label or "", int(edge_id))
    )
    conn.commit()


def remove_board_edge(conn: sqlite3.Connection, edge_id: int) -> None:
    conn.execute("DELETE FROM board_edges WHERE id = ?", (int(edge_id),))
    conn.commit()


def clear_board(conn: sqlite3.Connection, folder_id: int | None = None) -> None:
    """清空图板；给定 folder_id 时只清空该文件夹下文档的节点、相关连线与注释。

    folder_id 为 None 时清空全部（含所有注释块）；文件夹作用域下保留全局注释
    （`board_notes.folder_id IS NULL`）。
    """
    if folder_id is None:
        conn.execute("DELETE FROM board_edges")
        conn.execute("DELETE FROM board_nodes")
        conn.execute("DELETE FROM board_notes")
        conn.commit()
        return
    conn.execute("DELETE FROM board_notes WHERE folder_id = ?", (folder_id,))
    rows = conn.execute(
        "SELECT id FROM documents WHERE folder_id = ?", (folder_id,)
    ).fetchall()
    ids = [int(row["id"]) for row in rows]
    if not ids:
        conn.commit()
        return
    placeholders = ", ".join("?" * len(ids))
    conn.execute(
        f"DELETE FROM board_edges WHERE from_doc IN ({placeholders})"
        f" OR to_doc IN ({placeholders})",
        [*ids, *ids],
    )
    conn.execute(
        f"DELETE FROM board_nodes WHERE document_id IN ({placeholders})", ids
    )
    conn.commit()


# ---------------------------------------------------------------------------
# 图板注释块（与文档无关，可单独拖动/编辑）
# ---------------------------------------------------------------------------


def list_board_notes(
    conn: sqlite3.Connection, folder_id: int | None = None
) -> list[sqlite3.Row]:
    """列出注释块；给定 folder_id 时包含该文件夹与全局（NULL）注释。"""
    sql = "SELECT * FROM board_notes"
    params: list[Any] = []
    if folder_id is not None:
        sql += " WHERE folder_id IS NULL OR folder_id = ?"
        params.append(folder_id)
    sql += " ORDER BY id"
    return conn.execute(sql, params).fetchall()


def add_board_note(
    conn: sqlite3.Connection,
    folder_id: int | None,
    x: float,
    y: float,
    text: str,
    color: str | None = None,
) -> int:
    cursor = conn.execute(
        "INSERT INTO board_notes(folder_id, x, y, text, color, created_at)"
        " VALUES(?, ?, ?, ?, ?, ?)",
        (folder_id, float(x), float(y), text or "", color, dates.iso_now()),
    )
    conn.commit()
    return int(cursor.lastrowid)


def update_board_note(
    conn: sqlite3.Connection,
    note_id: int,
    *,
    x: float | None = None,
    y: float | None = None,
    text: str | None = None,
    color: str | None = _UNSET,
) -> None:
    sets, params = [], []
    if x is not None:
        sets.append("x = ?")
        params.append(float(x))
    if y is not None:
        sets.append("y = ?")
        params.append(float(y))
    if text is not None:
        sets.append("text = ?")
        params.append(text)
    if color is not _UNSET:
        sets.append("color = ?")
        params.append(color)
    if not sets:
        return
    params.append(int(note_id))
    conn.execute(
        f"UPDATE board_notes SET {', '.join(sets)} WHERE id = ?", params
    )
    conn.commit()


def remove_board_note(conn: sqlite3.Connection, note_id: int) -> None:
    conn.execute("DELETE FROM board_notes WHERE id = ?", (int(note_id),))
    conn.commit()


def board_snapshot(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """导出整张图板（节点/连线/注释），用于关系图的撤销。

    撤销采用“操作前快照 + 恢复快照”的方式：图板数据量很小，整表备份比
    为每种操作编写逆操作更可靠；恢复后节点/注释会得到新的自增主键，但
    文档绑定、坐标、颜色、文字都不会丢失。
    """
    nodes = [
        {
            "document_id": int(row["document_id"]),
            "x": float(row["x"]),
            "y": float(row["y"]),
            "label": str(row["label"] or ""),
            "color": row["color"],
        }
        for row in conn.execute(
            "SELECT document_id, x, y, label, color FROM board_nodes ORDER BY id"
        ).fetchall()
    ]
    edges = [
        {
            "from_doc": int(row["from_doc"]),
            "to_doc": int(row["to_doc"]),
            "label": str(row["label"] or ""),
        }
        for row in conn.execute(
            "SELECT from_doc, to_doc, label FROM board_edges ORDER BY id"
        ).fetchall()
    ]
    notes = [
        {
            "folder_id": int(row["folder_id"]) if row["folder_id"] is not None else None,
            "x": float(row["x"]),
            "y": float(row["y"]),
            "text": str(row["text"] or ""),
            "color": row["color"],
            "created_at": str(row["created_at"] or dates.iso_now()),
        }
        for row in conn.execute(
            "SELECT folder_id, x, y, text, color, created_at"
            " FROM board_notes ORDER BY id"
        ).fetchall()
    ]
    return {"nodes": nodes, "edges": edges, "notes": notes}


def restore_board_snapshot(conn: sqlite3.Connection, snapshot: dict) -> None:
    """用一个快照整体恢复图板（撤销用）；失败时回滚，不留下半张图。"""
    nodes = snapshot.get("nodes") or []
    edges = snapshot.get("edges") or []
    notes = snapshot.get("notes") or []
    try:
        conn.execute("DELETE FROM board_edges")
        conn.execute("DELETE FROM board_nodes")
        conn.execute("DELETE FROM board_notes")
        conn.executemany(
            "INSERT INTO board_nodes(document_id, x, y, label, color)"
            " VALUES(?, ?, ?, ?, ?)",
            [
                (
                    int(item["document_id"]),
                    float(item["x"]),
                    float(item["y"]),
                    str(item.get("label") or ""),
                    item.get("color"),
                )
                for item in nodes
            ],
        )
        conn.executemany(
            "INSERT INTO board_edges(from_doc, to_doc, label) VALUES(?, ?, ?)",
            [
                (
                    int(item["from_doc"]),
                    int(item["to_doc"]),
                    str(item.get("label") or ""),
                )
                for item in edges
            ],
        )
        conn.executemany(
            "INSERT INTO board_notes(folder_id, x, y, text, color, created_at)"
            " VALUES(?, ?, ?, ?, ?, ?)",
            [
                (
                    item.get("folder_id"),
                    float(item["x"]),
                    float(item["y"]),
                    str(item.get("text") or ""),
                    item.get("color"),
                    str(item.get("created_at") or dates.iso_now()),
                )
                for item in notes
            ],
        )
    except Exception:
        conn.rollback()
        raise
    conn.commit()


def document_period_colors(
    conn: sqlite3.Connection, folder_id: int | None = None
) -> dict[int, sqlite3.Row]:
    """文档 → 最近一条“落在时间段内”的记录所属时间段的颜色与名称。"""
    sql = """
        SELECT latest.document_id AS document_id, latest.day AS day,
               p.id AS period_id, p.name AS name, p.color AS color
        FROM (
            SELECT e.document_id AS document_id, MAX(e.day) AS day
            FROM entries e JOIN period_days pd ON pd.day = e.day
            GROUP BY e.document_id
        ) AS latest
        JOIN period_days pd2 ON pd2.day = latest.day
        JOIN periods p ON p.id = pd2.period_id
        JOIN documents d ON d.id = latest.document_id
    """
    params: list[Any] = []
    if folder_id is not None:
        sql += " WHERE d.folder_id = ?"
        params.append(folder_id)
    rows = conn.execute(sql, params).fetchall()
    return {int(row["document_id"]): row for row in rows}


def all_entries(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """全部记录（附文档与文件夹信息），供导出使用。"""
    return conn.execute(
        """
        SELECT e.id AS entry_id, e.document_id AS document_id, e.day AS day,
               e.note AS note, e.source AS source,
               e.created_at AS created_at, e.updated_at AS updated_at,
               d.name AS name, d.rel_path AS rel_path, d.path AS path,
               d.ext AS ext, d.size AS size, d.status AS status, d.mtime AS mtime,
               f.id AS folder_id, f.name AS folder_name, f.path AS folder_path
        FROM entries e
        JOIN documents d ON d.id = e.document_id
        JOIN folders f ON f.id = d.folder_id
        ORDER BY e.day, e.id
        """
    ).fetchall()


# ---------------------------------------------------------------------------
# 统计与设置
# ---------------------------------------------------------------------------


def stats(conn: sqlite3.Connection, folder_id: int | None = None) -> dict[str, int]:
    """文件夹总数始终是全局值；文档/缺失数可按当前文件夹限定。"""
    docs_where = "status != 'ignored'"
    missing_where = "status = 'missing'"
    params: list[Any] = []
    if folder_id is not None:
        docs_where += " AND folder_id = ?"
        missing_where += " AND folder_id = ?"
        params = [folder_id, folder_id]
    sql = (
        "SELECT"
        " (SELECT COUNT(*) FROM folders WHERE active = 1) AS folders,"
        f" (SELECT COUNT(*) FROM documents WHERE {docs_where}) AS documents,"
        f" (SELECT COUNT(*) FROM documents WHERE {missing_where}) AS missing"
    )
    row = conn.execute(sql, params).fetchone()
    return {
        "folders": int(row["folders"]),
        "documents": int(row["documents"]),
        "missing": int(row["missing"]),
    }


def last_scan_time(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT MAX(last_scan_at) AS t FROM folders").fetchone()
    return row["t"] if row and row["t"] else None


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def get_int(conn: sqlite3.Connection, key: str, default: int) -> int:
    try:
        return int(get_setting(conn, key, str(default)) or default)
    except (TypeError, ValueError):
        return default


def get_bool(conn: sqlite3.Connection, key: str, default: bool = False) -> bool:
    value = get_setting(conn, key, "1" if default else "0")
    return value == "1"


def executemany_entries(conn: sqlite3.Connection, rows: Iterable[tuple]) -> None:
    """预留：批量插入记录（导入大量文件时使用）。"""
    conn.executemany(
        "INSERT INTO entries(document_id, day, note, source, created_at, updated_at)"
        " VALUES(?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
