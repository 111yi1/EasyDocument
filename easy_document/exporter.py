"""导出 JSON / CSV：只读数据库，不修改任何数据。

- JSON：全部文件夹、文档、记录、时间段（含日期）与图板，用于备份、换机或外部处理；
- CSV：全部日期记录，一行一条，UTF-8 BOM（Excel 直接打开中文不乱码）。

格式化逻辑都在这里，UI 只负责选路径与展示结果。
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path

from . import db, dates, repo
from .config import APP_VERSION

EXPORT_FORMAT = "easy-document-export"
EXPORT_FORMAT_VERSION = 1

CSV_HEADERS = (
    "文件夹",
    "文件名",
    "相对路径",
    "绝对路径",
    "扩展名",
    "大小(字节)",
    "修改时间",
    "状态",
    "记录日期",
    "备注",
    "来源",
    "创建时间",
    "更新时间",
)


def export_json(conn, target: str | Path) -> dict[str, int]:
    """把全部数据写成 JSON（格式见模块说明），返回各类条数。"""
    payload = {
        "format": EXPORT_FORMAT,
        "format_version": EXPORT_FORMAT_VERSION,
        "app_version": APP_VERSION,
        "schema_version": db.SCHEMA_VERSION,
        "exported_at": dates.iso_now(),
        "folders": [dict(row) for row in repo.list_folders(conn)],
        "documents": [dict(row) for row in repo.list_documents(conn)],
        "entries": [dict(row) for row in repo.all_entries(conn)],
        "periods": [],
        "board": {
            "nodes": [dict(row) for row in repo.list_board_nodes(conn)],
            "edges": [dict(row) for row in repo.list_board_edges(conn)],
            "notes": [dict(row) for row in repo.list_board_notes(conn)],
        },
    }
    for period in repo.list_periods(conn):
        item = dict(period)
        item["days"] = repo.period_days(conn, int(period["id"]))
        payload["periods"].append(item)

    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2))
    return {
        "folders": len(payload["folders"]),
        "documents": len(payload["documents"]),
        "entries": len(payload["entries"]),
        "periods": len(payload["periods"]),
        "nodes": len(payload["board"]["nodes"]),
        "edges": len(payload["board"]["edges"]),
        "notes": len(payload["board"]["notes"]),
    }


def export_csv(conn, target: str | Path) -> dict[str, int]:
    """把全部日期记录写成 CSV，返回记录条数。"""
    rows = repo.all_entries(conn)
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(CSV_HEADERS)
            for row in rows:
                writer.writerow(
                    (
                        row["folder_name"],
                        row["name"],
                        row["rel_path"],
                        row["path"],
                        row["ext"],
                        int(row["size"]),
                        row["mtime"] or "",
                        row["status"],
                        row["day"],
                        row["note"],
                        row["source"],
                        row["created_at"],
                        row["updated_at"],
                    )
                )
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return {"rows": len(rows)}


def _write_text_atomic(path: Path, text: str) -> None:
    """先写临时文件再替换，避免导出失败留下半个文件。"""
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
