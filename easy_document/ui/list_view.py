"""时间轴/列表视图：按天或按时间段分组浏览全部记录。

用于“只记得大致日期范围”时比日历更快地翻找：默认按天倒序分组，
也可以按时间段（多日分组）归档显示；没有记录的文档单独成组。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .. import dates, repo
from ..config import COLOR_MUTED, FONT_SMALL, FONT_UI
from .widgets import Tooltip

SOURCE_TEXT = {"import": "导入", "manual": "手动", "auto": "自动", "scan": "扫描"}
GROUP_DAY = "按天"
GROUP_PERIOD = "按时间段"
TIMELINE_LIMIT = 1500


class ListView(ttk.Frame):
    def __init__(self, master: tk.Misc, app) -> None:
        super().__init__(master, padding=(6, 4))
        self.app = app
        self.conn = app.conn
        self._row_info: dict[str, tuple[str | None, int]] = {}
        self._tips: dict[str, str] = {}
        self.group_mode = GROUP_DAY
        self._build()
        self.reload()

    # -- 构建 ---------------------------------------------------------------

    def _build(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 4))
        ttk.Label(bar, text="分组：").pack(side="left")
        self.group_combo = ttk.Combobox(
            bar, values=[GROUP_DAY, GROUP_PERIOD], state="readonly", width=10
        )
        self.group_combo.set(GROUP_DAY)
        self.group_combo.pack(side="left")
        self.group_combo.bind("<<ComboboxSelected>>", self._on_group_change)
        self.count_label = ttk.Label(bar, text="", foreground=COLOR_MUTED, font=FONT_SMALL)
        self.count_label.pack(side="left", padx=(12, 0))
        ttk.Label(
            bar,
            text="单击行跳转到该日，双击打开文件",
            foreground=COLOR_MUTED,
            font=FONT_SMALL,
        ).pack(side="right")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            body,
            columns=("day", "note", "source"),
            show="tree headings",
            selectmode="browse",
        )
        self.tree.heading("#0", text="分组 / 文档")
        self.tree.column("#0", width=280, minwidth=160, stretch=True)
        for col, label, width in (
            ("day", "日期", 90),
            ("note", "备注摘要", 420),
            ("source", "来源", 70),
        ):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, minwidth=60, anchor="w", stretch=col == "note")
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        self.empty_label = ttk.Label(
            self, text="还没有记录。添加文件夹并扫描后，这里会按时间顺序显示。",
            foreground="#9aa0a6",
        )
        self.tooltip = Tooltip(self.tree)

        self.tree.tag_configure("group", font=FONT_UI, foreground="#111827")
        self.tree.tag_configure("muted", foreground=COLOR_MUTED)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._on_activate)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Motion>", self._on_motion)
        self.tree.bind("<Leave>", lambda _e: self._hide_tip())

    def _on_group_change(self, _event=None) -> None:
        self.group_mode = self.group_combo.get() or GROUP_DAY
        self.reload()

    # -- 数据 ---------------------------------------------------------------

    def reload(self) -> None:
        rows = repo.timeline(self.conn, folder_id=self.app.current_folder_id, limit=TIMELINE_LIMIT)
        period_map = repo.period_day_map(self.conn)
        periods = repo.list_periods(self.conn, folder_id=self.app.current_folder_id)
        orphans = repo.documents_without_entries(
            self.conn, folder_id=self.app.current_folder_id
        )

        self._row_info = {}
        self._tips = {}
        self.tooltip.hide()
        self.tree.delete(*self.tree.get_children())

        if self.group_mode == GROUP_PERIOD:
            self._fill_by_period(rows, period_map, periods)
        else:
            self._fill_by_day(rows)
        self._fill_orphans(orphans)

        self.count_label.config(
            text=(
                f"{len(rows)} 条记录 · {len(orphans)} 篇未归类"
                + (
                    f"（仅显示最近 {TIMELINE_LIMIT} 条）"
                    if len(rows) >= TIMELINE_LIMIT
                    else ""
                )
            )
        )
        if rows or orphans:
            self.empty_label.pack_forget()
        else:
            self.empty_label.pack(pady=30)

    def _fill_by_day(self, rows) -> None:
        groups: dict[str, list] = {}
        order: list[str] = []
        for row in rows:
            day = str(row["day"])
            if day not in groups:
                groups[day] = []
                order.append(day)
            groups[day].append(row)
        for day in order:
            group_iid = self.tree.insert(
                "",
                "end",
                text=f"{day} {dates.weekday_cn(day)}（{len(groups[day])} 条）",
                values=("", "", ""),
                open=True,
                tags=("group",),
            )
            for row in groups[day]:
                self._insert_entry(group_iid, row)

    def _fill_by_period(self, rows, period_map, periods) -> None:
        buckets: dict[int, list] = {int(p["id"]): [] for p in periods}
        loose: list = []
        for row in rows:
            info = period_map.get(str(row["day"]))
            if info is None:
                loose.append(row)
            else:
                buckets.setdefault(int(info["period_id"]), []).append(row)

        for period in periods:
            period_id = int(period["id"])
            entries = buckets.get(period_id, [])
            if not period["first_day"]:
                label = f"{period['name']}（未设置日期）"
            else:
                first = dates.short_day(period["first_day"])
                last = dates.short_day(period["last_day"]) if period["last_day"] else first
                span = first if first == last else f"{first} ~ {last}"
                label = f"{period['name']}（{span} · {len(entries)} 条）"
            group_iid = self.tree.insert(
                "", "end", text=label, values=("", "", ""), open=True, tags=("group",)
            )
            for row in entries:
                self._insert_entry(group_iid, row)
            if not entries:
                self.tree.insert(group_iid, "end", text="（该时间段内没有记录）", values=("", "", ""), tags=("muted",))

        if loose:
            group_iid = self.tree.insert(
                "",
                "end",
                text=f"未分组日期（{len(loose)} 条）",
                values=("", "", ""),
                open=True,
                tags=("group",),
            )
            for row in loose:
                self._insert_entry(group_iid, row)

    def _insert_entry(self, parent: str, row) -> None:
        iid = f"e{row['entry_id']}"
        note = self._one_line(row["note"], 80)
        self.tree.insert(
            parent,
            "end",
            iid=iid,
            text=str(row["name"]),
            values=(
                dates.short_day(row["day"]),
                note or "（无备注）",
                SOURCE_TEXT.get(row["source"], row["source"]),
            ),
        )
        self._row_info[iid] = (str(row["day"]), int(row["doc_id"]))
        self._tips[iid] = "\n".join(
            part
            for part in (
                str(row["name"]),
                f"日期：{row['day']}",
                f"备注：{row['note']}" if row["note"] else "",
                str(row["path"]),
                "状态：文件缺失" if row["status"] == "missing" else "",
            )
            if part
        )

    def _fill_orphans(self, docs) -> None:
        if not docs:
            return
        group_iid = self.tree.insert(
            "",
            "end",
            text=f"未归类文档（{len(docs)} 篇）",
            values=("", "", ""),
            tags=("group",),
        )
        for doc in docs:
            iid = f"d{doc['id']}"
            self.tree.insert(
                group_iid,
                "end",
                iid=iid,
                text=str(doc["name"]),
                values=(
                    "—",
                    "还没有日期记录",
                    "缺失" if doc["status"] == "missing" else "",
                ),
                tags=("muted",) if doc["status"] == "missing" else (),
            )
            self._row_info[iid] = (None, int(doc["id"]))
            self._tips[iid] = "\n".join(
                part
                for part in (
                    str(doc["name"]),
                    str(doc["path"]),
                    "状态：文件缺失" if doc["status"] == "missing" else "",
                )
                if part
            )

    # -- 交互 ---------------------------------------------------------------

    def _on_select(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        info = self._row_info.get(selection[0])
        if info is None:
            return
        day, doc_id = info
        if day:
            self.app.jump_to_day(day, doc_id)
        else:
            self.app.doc_panel.show_document(doc_id)

    def _on_activate(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        info = self._row_info.get(iid)
        if info is not None:
            self.app.open_document(info[1])

    def _on_right_click(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        info = self._row_info.get(iid)
        if info is None:
            return
        self.tree.selection_set(iid)
        day, doc_id = info
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="打开文档", command=lambda: self.app.open_document(doc_id))
        menu.add_command(
            label="在资源管理器中定位", command=lambda: self.app.reveal_document(doc_id)
        )
        menu.add_command(
            label="添加记录…", command=lambda: self.app.add_record_dialog(doc_id)
        )
        menu.add_separator()
        if day:
            menu.add_command(
                label="在日历中查看这一天",
                command=lambda: self.app.jump_to_day(day, doc_id),
            )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _on_motion(self, event) -> None:
        row = self.tree.identify_row(event.y)
        if row == getattr(self, "_hover_row", None):
            return
        self._hover_row = row
        tip = self._tips.get(row, "")
        if tip:
            self.tooltip.schedule(tip, event.x_root, event.y_root)
        else:
            self._hide_tip()

    def _hide_tip(self) -> None:
        self._hover_row = None
        self.tooltip.hide()

    @staticmethod
    def _one_line(text, limit: int = 80) -> str:
        if not text:
            return ""
        flat = " ".join(str(text).split())
        return flat if len(flat) <= limit else flat[: limit - 1] + "…"
