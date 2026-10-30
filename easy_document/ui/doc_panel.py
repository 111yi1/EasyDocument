"""右栏：当天 / 最近 / 全部 / 时间段 / 缺失 / 搜索 列表，以及文档详情与记录历史。"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from .. import dates, docx_meta, repo
from ..config import (
    COLOR_MUTED,
    COLOR_WARN,
    FONT_SMALL,
    FONT_TITLE,
    FONT_UI_BOLD,
)
from .widgets import ListTab, human_size

STATUS_TEXT = {"active": "", "missing": "缺失", "ignored": "已忽略"}
SOURCE_TEXT = {"import": "导入", "manual": "手动", "auto": "自动", "scan": "扫描"}


class DocPanel(ttk.Frame):
    def __init__(self, master, app) -> None:
        super().__init__(master, padding=(4, 4))
        self.app = app
        self.conn = app.conn
        self.current_doc_id: int | None = None
        self.day: str | None = None
        self._maps: dict[str, dict[str, int]] = {}
        self._row_days: dict[str, str] = {}
        self._action_buttons: list[ttk.Button] = []
        self._meta_token = 0
        self._meta_queue: queue.Queue | None = None
        self._build()
        self.reload_lists()
        self._clear_detail()
        self.notebook.select(self.tabs["recent"])

    # -- 构建 ---------------------------------------------------------------

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="we", pady=(0, 4))
        self.title_label = ttk.Label(header, text="最近记录", font=FONT_TITLE)
        self.title_label.pack(side="left")
        ttk.Button(header, text="＋记录", command=self._add_record).pack(side="right")
        ttk.Button(header, text="定位", command=self._reveal_current).pack(
            side="right", padx=(0, 4)
        )
        ttk.Button(header, text="打开", command=self._open_current).pack(
            side="right", padx=(0, 4)
        )

        self.vsplit = ttk.PanedWindow(self, orient="vertical")
        self.vsplit.grid(row=1, column=0, sticky="nsew")

        self.notebook = ttk.Notebook(self.vsplit)
        self.tabs: dict[str, ListTab] = {}

        specs = [
            (
                "day",
                "当天",
                [("name", "文件名", 170), ("note", "备注摘要", 200), ("mtime", "修改时间", 150)],
                "点击日历中的日期，查看当天的文档",
            ),
            (
                "recent",
                "最近",
                [("day", "日期", 90), ("name", "文件名", 170), ("note", "备注摘要", 200)],
                "还没有任何记录。添加文件夹并扫描后，这里会显示最近记录。",
            ),
            (
                "all",
                "全部文档",
                [
                    ("name", "文件名", 170),
                    ("path", "路径", 240),
                    ("mtime", "修改时间", 150),
                    ("status", "状态", 70),
                ],
                "还没有纳入索引的文档。",
            ),
            (
                "missing",
                "缺失",
                [("name", "文件名", 170), ("path", "路径", 240), ("since", "缺失起始", 140)],
                "没有缺失文件。",
            ),
            (
                "search",
                "搜索",
                [("day", "日期", 90), ("name", "文件名", 170), ("note", "备注摘要", 200)],
                "在上方搜索框输入文件名或备注关键字后按回车。",
            ),
        ]
        for key, label, columns, empty in specs:
            tab = ListTab(
                self.notebook,
                columns,
                empty_text=empty,
                on_select=lambda iid, k=key: self._on_select(k, iid),
                on_activate=lambda iid, k=key: self._on_activate(k, iid),
            )
            if key == "missing":
                self.period_tab = PeriodTab(self.notebook, self.app)
                self.notebook.add(self.period_tab, text="时间段")
            self.notebook.add(tab, text=label)
            self.tabs[key] = tab

        self.vsplit.add(self.notebook, weight=3)
        self._build_detail()
        self.after(120, self._init_split)

    def _init_split(self) -> None:
        """恢复上次的上下分隔位置；首次打开时给列表约 55% 高度。"""
        try:
            height = self.vsplit.winfo_height()
            saved = repo.get_int(self.conn, "doc.split", 0)
            if saved >= 120:
                self.vsplit.sashpos(0, saved)
            elif height > 240:
                self.vsplit.sashpos(0, max(160, int(height * 0.55)))
        except tk.TclError:
            pass

    def _build_detail(self) -> None:
        detail = ttk.LabelFrame(self.vsplit, text="文档详情", padding=(10, 8))
        self.vsplit.add(detail, weight=1)
        detail.columnconfigure(0, weight=1)
        self.detail = detail

        top = ttk.Frame(detail)
        top.grid(row=0, column=0, sticky="we")
        top.columnconfigure(0, weight=1)
        self.name_label = ttk.Label(top, text="（未选择文档）", font=FONT_UI_BOLD)
        self.name_label.grid(row=0, column=0, sticky="w")
        self.status_label = ttk.Label(top, text="", foreground=COLOR_WARN, font=FONT_SMALL)
        self.status_label.grid(row=0, column=1, sticky="e")

        self.path_label = ttk.Label(
            detail, text="", foreground=COLOR_MUTED, wraplength=460, justify="left"
        )
        self.path_label.grid(row=1, column=0, sticky="w", pady=(2, 0))
        detail.bind("<Configure>", self._on_detail_resize)

        self.meta_label = ttk.Label(detail, text="", foreground=COLOR_MUTED)
        self.meta_label.grid(row=2, column=0, sticky="w", pady=(2, 2))

        self.extra_label = ttk.Label(
            detail,
            text="",
            foreground=COLOR_MUTED,
            font=FONT_SMALL,
            wraplength=460,
            justify="left",
        )
        self.extra_label.grid(row=3, column=0, sticky="w", pady=(0, 6))

        ttk.Label(detail, text="记录历史（双击可编辑）", foreground=COLOR_MUTED).grid(
            row=4, column=0, sticky="w"
        )
        entries = ttk.Treeview(
            detail,
            columns=("day", "note", "source", "updated"),
            show="headings",
            height=4,
            selectmode="browse",
        )
        for col, label, width in (
            ("day", "日期", 110),
            ("note", "备注", 320),
            ("source", "来源", 70),
            ("updated", "更新时间", 150),
        ):
            entries.heading(col, text=label)
            entries.column(col, width=width, anchor="w", stretch=col == "note")
        scrollbar = ttk.Scrollbar(detail, orient="vertical", command=entries.yview)
        entries.configure(yscrollcommand=scrollbar.set)
        entries.grid(row=5, column=0, sticky="nsew")
        scrollbar.grid(row=5, column=1, sticky="ns")
        detail.rowconfigure(5, weight=1)
        entries.bind("<Double-1>", lambda _e: self._edit_entry())
        self.entries_tree = entries

        buttons = ttk.Frame(detail)
        buttons.grid(row=6, column=0, columnspan=2, sticky="we", pady=(6, 0))
        for text, command in (
            ("添加记录", self._add_record),
            ("编辑记录", self._edit_entry),
            ("删除记录", self._delete_entry),
            ("重新链接…", self._relink_current),
            ("移除索引", self._remove_current),
        ):
            button = ttk.Button(buttons, text=text, command=command)
            button.pack(side="left", padx=(0, 4))
            self._action_buttons.append(button)
        self._set_buttons(False)

    def _on_detail_resize(self, event) -> None:
        try:
            self.path_label.configure(wraplength=max(320, event.width - 46))
            self.extra_label.configure(wraplength=max(320, event.width - 46))
        except Exception:
            pass

    # -- 数据加载 -----------------------------------------------------------

    def reload_lists(self) -> None:
        self._maps = {key: {} for key in self.tabs}
        self._row_days = {}
        self._fill_recent()
        self._fill_all()
        self._fill_missing()
        self.period_tab.reload()

    def reset_for_folder(self) -> None:
        """切换文件夹作用域时清空选中与详情，并按新范围重新加载。"""
        self.current_doc_id = None
        self.day = None
        self._clear_detail()
        self.reload_lists()
        self.show_recent()

    def _fill_day(self, day: str, select_doc_id: int | None = None) -> None:
        rows = repo.documents_for_day(
            self.conn, day, folder_id=self.app.current_folder_id
        )
        items = []
        self._maps["day"] = {}
        for row in rows:
            iid = str(row["id"])
            self._maps["day"][iid] = int(row["id"])
            note = self._one_line(row["note"], 40)
            if int(row["entry_count"] or 0) > 1:
                note = f"{note}（{row['entry_count']} 条）" if note else f"{row['entry_count']} 条记录"
            items.append(
                (
                    iid,
                    (row["name"], note, dates.short_time(row["mtime"])),
                    self._doc_tooltip(row, include_note=True),
                )
            )
        self.tabs["day"].set_rows(items)
        self.day = day
        self.title_label.config(
            text=f"{dates.format_day_cn(day)} {dates.weekday_cn(day)} · {len(rows)} 篇"
        )
        if select_doc_id is not None:
            self.tabs["day"].select_iid(str(select_doc_id))

    def _fill_recent(self) -> None:
        rows = repo.recent_entries(
            self.conn, 60, folder_id=self.app.current_folder_id
        )
        items = []
        for row in rows:
            iid = f"e{row['entry_id']}"
            self._maps["recent"][iid] = int(row["doc_id"])
            self._row_days[iid] = str(row["day"])
            note = self._one_line(row["note"], 40)
            items.append(
                (
                    iid,
                    (dates.short_day(row["day"]), row["name"], note or "（无备注）"),
                    self._doc_tooltip(row, include_note=True),
                )
            )
        self.tabs["recent"].set_rows(items)

    def _fill_all(self) -> None:
        items = []
        self._maps["all"] = {}
        for doc in repo.list_documents(
            self.conn, folder_id=self.app.current_folder_id
        ):
            if doc["status"] == "ignored":
                continue
            iid = str(doc["id"])
            self._maps["all"][iid] = int(doc["id"])
            items.append(
                (
                    iid,
                    (
                        doc["name"],
                        doc["path"],
                        dates.short_time(doc["mtime"]),
                        STATUS_TEXT.get(doc["status"], doc["status"]),
                    ),
                    self._doc_tooltip(doc),
                )
            )
        self.tabs["all"].set_rows(items)

    def _fill_missing(self) -> None:
        items = []
        self._maps["missing"] = {}
        for doc in repo.list_documents(
            self.conn, folder_id=self.app.current_folder_id, status="missing"
        ):
            iid = str(doc["id"])
            self._maps["missing"][iid] = int(doc["id"])
            items.append(
                (
                    iid,
                    (doc["name"], doc["path"], dates.short_time(doc["missing_since"])),
                    self._doc_tooltip(doc),
                )
            )
        self.tabs["missing"].set_rows(items)

    def show_day(self, day: str, select_doc_id: int | None = None) -> None:
        self._fill_day(day)
        ids = list(self._maps["day"].keys())
        target = str(select_doc_id) if select_doc_id is not None else (ids[0] if ids else None)
        if target and target in self._maps["day"]:
            self.tabs["day"].select_iid(target)
            self._load_detail(self._maps["day"][target])
        self.notebook.select(self.tabs["day"])

    def show_recent(self) -> None:
        self.day = None
        self.title_label.config(text="最近记录")
        self.notebook.select(self.tabs["recent"])

    def show_document(self, doc_id: int) -> None:
        """从其它视图（如列表视图的“未归类”组）直接定位到一篇文档。"""
        self._load_detail(doc_id)

    def show_search(self, text: str, rows) -> None:
        items = []
        self._maps["search"] = {}
        for row in rows:
            iid = f"e{row['entry_id']}"
            self._maps["search"][iid] = int(row["doc_id"])
            self._row_days[iid] = str(row["day"])
            items.append(
                (
                    iid,
                    (
                        dates.short_day(row["day"]),
                        row["name"],
                        self._one_line(row["note"], 40) or "（无备注）",
                    ),
                    self._doc_tooltip(row),
                )
            )
        self.tabs["search"].set_rows(items)
        self.title_label.config(text=f"搜索「{text}」 · {len(rows)} 条")
        self.notebook.select(self.tabs["search"])

    def reset_search(self) -> None:
        self.tabs["search"].set_rows([])
        if self.day:
            self.show_day(self.day)
        else:
            self.show_recent()

    def refresh_all(self) -> None:
        active_tab = None
        try:
            active_tab = self.notebook.select()
        except Exception:
            pass
        self.reload_lists()
        if self.day:
            self._fill_day(self.day)
        if self.current_doc_id is not None:
            self._load_detail(self.current_doc_id)
        else:
            self._clear_detail()
        if active_tab:
            try:
                self.notebook.select(active_tab)
            except Exception:
                pass

    # -- 选择与详情 ---------------------------------------------------------

    def _on_select(self, tab_key: str, iid: str) -> None:
        doc_id = self._maps.get(tab_key, {}).get(iid)
        if doc_id is not None:
            self._load_detail(doc_id)

    def _on_activate(self, tab_key: str, iid: str) -> None:
        doc_id = self._maps.get(tab_key, {}).get(iid)
        if doc_id is None:
            return
        if tab_key in ("recent", "search"):
            day = self._row_days.get(iid)
            if day:
                self.app.jump_to_day(day, doc_id)
            else:
                self._load_detail(doc_id)
        else:
            self.app.open_document(doc_id)

    def _load_detail(self, doc_id: int) -> None:
        doc = repo.get_document(self.conn, doc_id)
        if doc is None:
            self._clear_detail()
            return
        self.current_doc_id = int(doc_id)
        self.name_label.config(text=doc["name"])
        missing = doc["status"] == "missing"
        self.status_label.config(
            text="缺失（文件已不在该路径）" if missing else "",
            foreground=COLOR_WARN if missing else COLOR_MUTED,
        )
        self.path_label.config(text=doc["path"])
        self.meta_label.config(
            text=(
                f"{human_size(doc['size'])} · 修改：{dates.short_time(doc['mtime']) or '—'}"
                f" · 添加：{dates.short_time(doc['added_at'])}"
            )
        )
        self._load_docx_meta(int(doc_id), doc["path"])
        entries = repo.entries_for_document(self.conn, doc_id)
        self.entries_tree.delete(*self.entries_tree.get_children())
        for entry in entries:
            self.entries_tree.insert(
                "",
                "end",
                iid=str(entry["id"]),
                values=(
                    entry["day"],
                    self._one_line(entry["note"], 60),
                    SOURCE_TEXT.get(entry["source"], entry["source"]),
                    dates.short_time(entry["updated_at"]),
                ),
            )
        self._set_buttons(True)

    def _clear_detail(self) -> None:
        self.current_doc_id = None
        self._meta_token += 1
        self._meta_queue = None
        self.name_label.config(text="（未选择文档）")
        self.status_label.config(text="")
        self.path_label.config(text="")
        self.meta_label.config(text="")
        self.extra_label.config(text="")
        self.entries_tree.delete(*self.entries_tree.get_children())
        self._set_buttons(False)

    # -- Office 文档属性（后台读取，失败静默） ------------------------------

    def _load_docx_meta(self, doc_id: int, path: str) -> None:
        self._meta_token += 1
        token = self._meta_token
        self._meta_queue = None
        if not docx_meta.supports(path):
            self.extra_label.config(text="")
            return
        self.extra_label.config(text="正在读取文档属性…")
        journal: queue.Queue = queue.Queue()
        self._meta_queue = journal
        threading.Thread(
            target=self._read_meta_worker,
            args=(path, journal),
            daemon=True,
            name="easy-document-meta",
        ).start()
        self.after(100, lambda: self._poll_docx_meta(token, doc_id))

    @staticmethod
    def _read_meta_worker(path: str, journal: queue.Queue) -> None:
        try:
            journal.put(docx_meta.read_meta(path))
        except Exception:  # 属性读取失败不能影响界面
            journal.put(None)

    def _poll_docx_meta(self, token: int, doc_id: int) -> None:
        if token != self._meta_token or not self.winfo_exists():
            return
        journal = self._meta_queue
        if journal is None:
            return
        try:
            meta = journal.get_nowait()
        except queue.Empty:
            self.after(120, lambda: self._poll_docx_meta(token, doc_id))
            return
        self._meta_queue = None
        if self.current_doc_id != doc_id or meta is None or meta.is_empty:
            self.extra_label.config(text="")
            return
        self.extra_label.config(text=f"文档属性 · {meta.summary()}")

    def _set_buttons(self, enabled: bool) -> None:
        for button in self._action_buttons:
            button.state(["!disabled"] if enabled else ["disabled"])

    # -- 动作 ---------------------------------------------------------------

    def _open_current(self) -> None:
        if self.current_doc_id is not None:
            self.app.open_document(self.current_doc_id)

    def _reveal_current(self) -> None:
        if self.current_doc_id is not None:
            self.app.reveal_document(self.current_doc_id)

    def _add_record(self) -> None:
        if self.current_doc_id is None:
            messagebox.showinfo("提示", "请先在列表中选择一篇文档。", parent=self)
            return
        self.app.add_record_dialog(self.current_doc_id)

    def _selected_entry_id(self) -> int | None:
        selection = self.entries_tree.selection()
        return int(selection[0]) if selection else None

    def _edit_entry(self) -> None:
        entry_id = self._selected_entry_id()
        if entry_id is None:
            messagebox.showinfo("提示", "请先选中一条记录。", parent=self)
            return
        self.app.edit_entry_dialog(entry_id)

    def _delete_entry(self) -> None:
        entry_id = self._selected_entry_id()
        if entry_id is None:
            messagebox.showinfo("提示", "请先选中一条记录。", parent=self)
            return
        self.app.delete_entry(entry_id)

    def _relink_current(self) -> None:
        if self.current_doc_id is not None:
            self.app.relink_document(self.current_doc_id)

    def _remove_current(self) -> None:
        if self.current_doc_id is not None:
            self.app.remove_document(self.current_doc_id)

    # -- 工具 ---------------------------------------------------------------

    @staticmethod
    def _one_line(text, limit: int = 60) -> str:
        if not text:
            return ""
        flat = " ".join(str(text).split())
        return flat if len(flat) <= limit else flat[: limit - 1] + "…"

    @staticmethod
    def _doc_tooltip(row, include_note: bool = False) -> str:
        parts = [str(row["name"])]
        if "path" in row.keys() and row["path"]:
            parts.append(str(row["path"]))
        if "mtime" in row.keys() and row["mtime"]:
            parts.append(f"修改：{dates.short_time(row['mtime'])}")
        if "status" in row.keys() and row["status"] == "missing":
            parts.append("状态：文件缺失")
        if include_note and "note" in row.keys() and row["note"]:
            parts.append(f"备注：{row['note']}")
        return "\n".join(parts)


class PeriodTab(ttk.Frame):
    """“时间段”页签：上方列出多日分组，下方按所选时间段过滤文档。"""

    def __init__(self, master: tk.Misc, app) -> None:
        super().__init__(master)
        self.app = app
        self.conn = app.conn
        self._rows: dict[str, tuple[str, int]] = {}
        self._build()

    def _build(self) -> None:
        self.rowconfigure(0, weight=3)
        self.rowconfigure(3, weight=2)
        self.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            self,
            columns=("name", "range", "days", "docs"),
            show="headings",
            selectmode="browse",
            height=5,
        )
        for col, label, width in (
            ("name", "时间段", 150),
            ("range", "起止", 150),
            ("days", "天数", 60),
            ("docs", "文档数", 70),
        ):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, anchor="w", stretch=col == "range")
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<Double-1>", lambda _e: self._view())
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._load_documents())

        buttons = ttk.Frame(self)
        buttons.grid(row=1, column=0, columnspan=2, sticky="we", pady=(4, 0))
        ttk.Button(buttons, text="新建…", command=self._create).pack(side="left")
        ttk.Button(buttons, text="编辑…", command=self._edit).pack(side="left", padx=(4, 0))
        ttk.Button(buttons, text="删除", command=self._delete).pack(side="left", padx=(4, 0))
        ttk.Button(buttons, text="查看该时段", command=self._view).pack(side="left", padx=(4, 0))

        self.docs_caption = ttk.Label(
            self, text="选中上方时间段，查看其中的文档", foreground=COLOR_MUTED, font=FONT_SMALL
        )
        self.docs_caption.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 2))

        self.docs_tree = ttk.Treeview(
            self,
            columns=("day", "name", "note"),
            show="headings",
            selectmode="browse",
            height=5,
        )
        for col, label, width in (
            ("day", "日期", 90),
            ("name", "文件名", 170),
            ("note", "备注摘要", 200),
        ):
            self.docs_tree.heading(col, text=label)
            self.docs_tree.column(col, width=width, anchor="w", stretch=col == "note")
        docs_scroll = ttk.Scrollbar(self, orient="vertical", command=self.docs_tree.yview)
        self.docs_tree.configure(yscrollcommand=docs_scroll.set)
        self.docs_tree.grid(row=3, column=0, sticky="nsew")
        docs_scroll.grid(row=3, column=1, sticky="ns")
        self.docs_tree.bind("<Double-1>", self._open_document)

        self.empty_label = ttk.Label(
            self,
            text="还没有时间段。\n在日历上 Shift 连选日期后右键即可创建。",
            foreground="#9aa0a6",
            justify="center",
        )

    def reload(self) -> None:
        selected = self._selected_id()
        self.tree.delete(*self.tree.get_children())
        periods = repo.list_periods(self.conn, folder_id=self.app.current_folder_id)
        for period in periods:
            color = period["color"] or "#6b7280"
            tag = f"color{color}"
            self.tree.tag_configure(tag, foreground=color)
            first = period["first_day"] or ""
            last = period["last_day"] or ""
            span = (
                f"{dates.short_day(first)} ~ {dates.short_day(last)}"
                if first and last
                else "（未设置日期）"
            )
            self.tree.insert(
                "",
                "end",
                iid=str(period["id"]),
                values=(
                    f"● {period['name']}",
                    span,
                    int(period["day_count"]),
                    int(period["doc_count"]),
                ),
                tags=(tag,),
            )
        if periods:
            self.empty_label.place_forget()
        else:
            self.empty_label.place(relx=0.5, rely=0.25, anchor="center")
        if selected is not None and self.tree.exists(str(selected)):
            self.tree.selection_set(str(selected))
        self._load_documents()

    def _selected_id(self) -> int | None:
        selection = self.tree.selection()
        return int(selection[0]) if selection else None

    def _load_documents(self) -> None:
        self._rows = {}
        self.docs_tree.delete(*self.docs_tree.get_children())
        period_id = self._selected_id()
        if period_id is None:
            self.docs_caption.config(text="选中上方时间段，查看其中的文档")
            return
        days = repo.period_days(self.conn, period_id)
        rows = repo.entries_in_days(
            self.conn, days, folder_id=self.app.current_folder_id
        )
        for row in rows:
            iid = f"e{row['entry_id']}"
            note = self._one_line(row["note"], 40)
            self.docs_tree.insert(
                "",
                "end",
                iid=iid,
                values=(row["day"], row["name"], note or "（无备注）"),
            )
            self._rows[iid] = (str(row["day"]), int(row["doc_id"]))
        period = repo.get_period(self.conn, period_id)
        name = period["name"] if period is not None else ""
        self.docs_caption.config(
            text=f"时间段「{name}」中的记录 · {len(rows)} 条（双击跳转到该日）"
        )

    def _create(self) -> None:
        self.app.create_period_from_days([])

    def _edit(self) -> None:
        period_id = self._selected_id()
        if period_id is None:
            messagebox.showinfo("提示", "请先选择一个时间段。", parent=self)
            return
        self.app.edit_period_dialog(period_id)

    def _delete(self) -> None:
        period_id = self._selected_id()
        if period_id is None:
            messagebox.showinfo("提示", "请先选择一个时间段。", parent=self)
            return
        self.app.delete_period(period_id)

    def _view(self) -> None:
        period_id = self._selected_id()
        if period_id is None:
            messagebox.showinfo("提示", "请先选择一个时间段。", parent=self)
            return
        days = repo.period_days(self.conn, period_id)
        if not days:
            messagebox.showinfo("提示", "该时间段还没有日期。", parent=self)
            return
        self.app.jump_to_day(days[0])

    def _open_document(self, event) -> None:
        iid = self.docs_tree.identify_row(event.y)
        info = self._rows.get(iid)
        if info is not None:
            self.app.jump_to_day(info[0], info[1])

    @staticmethod
    def _one_line(text, limit: int = 40) -> str:
        if not text:
            return ""
        flat = " ".join(str(text).split())
        return flat if len(flat) <= limit else flat[: limit - 1] + "…"
