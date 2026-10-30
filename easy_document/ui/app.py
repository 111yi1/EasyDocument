"""主窗口：工具栏、状态栏、扫描线程与全部业务动作。"""

from __future__ import annotations

import os
import queue
import re
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .. import db, dates, exporter, fileops, repo, scanner, storage
from ..config import (
    APP_TITLE,
    APP_VERSION,
    COLOR_MUTED,
    FONT_SMALL,
    FONT_UI,
    PERIOD_COLORS,
    normalize_extensions,
)
from .calendar_view import CalendarView
from .dialogs import (
    DataLocationDialog,
    DropMoveDialog,
    FolderDialog,
    FolderManagerDialog,
    PeriodDialog,
    PeriodManagerDialog,
    RecordDialog,
    SettingsDialog,
)
from .doc_panel import DocPanel
from .drop_target import WindowsDropTarget, normalize_dropped_paths
from .graph_view import GraphView
from .import_dialog import ImportDialog
from .list_view import ListView


def build_file_types(extensions: tuple[str, ...]) -> list[tuple[str, str]]:
    """重新链接对话框的文件类型：与当前受管扩展名保持一致。"""
    patterns = " ".join(f"*{ext}" for ext in extensions)
    return [("文档", patterns), ("所有文件", "*.*")]


class App(tk.Tk):
    def __init__(self, paths, conn, log, data_source: str = "") -> None:
        super().__init__()
        self.paths = paths
        self.conn = conn
        self.log = log
        self.data_source = data_source
        self.extensions = normalize_extensions(
            repo.get_setting(self.conn, "doc.extensions", "") or ""
        )
        self.date_source = (
            repo.get_setting(self.conn, "default.date_source", "mtime") or "mtime"
        )
        self.search_var = tk.StringVar()
        saved_view = repo.get_setting(self.conn, "view.default", "calendar") or "calendar"
        self.view_var = tk.StringVar(
            value=saved_view if saved_view in ("calendar", "list", "graph") else "calendar"
        )
        self.view_mode = self.view_var.get()
        self.current_folder_id: int | None = self._resolve_initial_folder()

        self._scan_running = False
        self._scan_queue: queue.Queue | None = None
        self._scan_results: list[tuple] = []
        self._scan_error: str | None = None
        self._last_scan_summary: str | None = None
        self._offline_paths: list[str] = []
        self._drop_target: WindowsDropTarget | None = None

        self._configure_window()
        self._build_menu()
        self._build_toolbar()
        self._build_status()
        self._build_body()

        self.bind("<F5>", lambda _e: self.start_scan())
        self.bind("<Control-f>", self._focus_search)
        self.bind("<Return>", self._on_enter)
        self.bind("<Control-Return>", self._on_ctrl_enter)
        self.bind("<Control-z>", self._on_undo_shortcut)
        self.bind("<Escape>", self._on_escape)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.after(80, self._initial_refresh)
        self.after(300, self._install_drop_target)
        self.after(400, self._first_run_hint)
        self._update_title()
        self.log.info("界面就绪，当前文件夹：%s", self._current_folder_label())

    # -- 构建 ---------------------------------------------------------------

    def _configure_window(self) -> None:
        self.title(f"{APP_TITLE}  v{APP_VERSION}")
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=23, font=FONT_UI)
        style.configure("TNotebook.Tab", padding=(10, 4))
        try:
            self.geometry(self._desired_geometry())
        except tk.TclError:
            self.geometry("1180x760")
        self.minsize(960, 620)

    def _desired_geometry(self) -> str:
        """恢复上次窗口位置，但按当前屏幕裁剪，避免窗口跑到屏幕外。"""
        saved = repo.get_setting(self.conn, "window.geometry", "") or ""
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        max_w = max(900, screen_w - 80)
        max_h = max(600, screen_h - 100)

        match = re.match(r"^(\d+)x(\d+)([+-]\d+)([+-]\d+)$", saved)
        if match and int(match.group(1)) >= 800 and int(match.group(2)) >= 500:
            width = min(int(match.group(1)), max_w)
            height = min(int(match.group(2)), max_h)
            x, y = int(match.group(3)), int(match.group(4))
            if x < 0 or x + width > screen_w:
                x = max(0, (screen_w - width) // 2)
            if y < 0 or y + height > screen_h:
                y = max(0, (screen_h - height) // 2)
            return f"{width}x{height}+{x}+{y}"

        width = min(1240, max_w)
        height = min(820, max_h)
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2 - 20)
        return f"{width}x{height}+{x}+{y}"

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="添加文件夹…", command=self.add_folder_dialog)
        file_menu.add_command(label="管理文件夹…", command=self.manage_folders)
        file_menu.add_separator()
        file_menu.add_command(label="扫描当前文件夹 (F5)", command=self.start_scan)
        file_menu.add_command(
            label="扫描全部文件夹", command=lambda: self.start_scan(scan_all=True)
        )
        file_menu.add_separator()
        file_menu.add_command(label="导出 JSON（全部数据）…", command=self.export_json_dialog)
        file_menu.add_command(label="导出 CSV（全部记录）…", command=self.export_csv_dialog)
        file_menu.add_separator()
        file_menu.add_command(label="立即备份数据库", command=self.backup_now)
        file_menu.add_command(label="打开数据目录", command=self.open_data_dir)
        file_menu.add_command(label="数据位置…", command=self.open_data_location)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.on_close)
        menubar.add_cascade(label="文件", menu=file_menu)

        view_menu = tk.Menu(menubar, tearoff=0)
        view_menu.add_command(label="日历视图", command=lambda: self._apply_view("calendar"))
        view_menu.add_command(label="列表视图", command=lambda: self._apply_view("list"))
        view_menu.add_command(label="关系图", command=lambda: self._apply_view("graph"))
        view_menu.add_separator()
        view_menu.add_command(label="时间段管理…", command=self.open_period_manager)
        view_menu.add_separator()
        view_menu.add_command(label="撤销关系图操作 (Ctrl+Z)", command=self.undo)
        view_menu.add_command(label="刷新", command=self.refresh_all)
        view_menu.add_command(label="回到今天", command=self._go_today)
        view_menu.add_command(label="清除搜索", command=self.clear_search)
        view_menu.add_separator()
        view_menu.add_command(label="设置…", command=self.open_settings)
        menubar.add_cascade(label="视图", menu=view_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="快捷键与使用提示", command=self._show_shortcuts)
        help_menu.add_command(label="关于", command=self._about)
        menubar.add_cascade(label="帮助", menu=help_menu)

        self.config(menu=menubar)

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, padding=(6, 4))
        bar.pack(fill="x")

        ttk.Label(bar, text="文件夹：").pack(side="left")
        self.folder_combo = ttk.Combobox(bar, state="readonly", width=16)
        self.folder_combo.pack(side="left")
        self.folder_combo.bind("<<ComboboxSelected>>", self._on_folder_combo_selected)

        self.btn_folder = ttk.Button(bar, text="添加", command=self.add_folder_dialog)
        self.btn_folder.pack(side="left", padx=(4, 0))
        self.btn_scan = ttk.Button(bar, text="扫描", command=self.start_scan)
        self.btn_scan.pack(side="left", padx=(4, 0))
        ttk.Button(bar, text="管理", command=self.manage_folders).pack(
            side="left", padx=(4, 0)
        )
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)

        ttk.Label(bar, text="视图：").pack(side="left")
        for mode, text in (("calendar", "日历"), ("list", "列表"), ("graph", "关系图")):
            ttk.Radiobutton(
                bar,
                text=text,
                value=mode,
                variable=self.view_var,
                style="Toolbutton",
                command=self._on_view_change,
            ).pack(side="left")
        ttk.Button(bar, text="时间段", command=self.open_period_manager).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(bar, text="＋记录", command=self.add_record_for_current).pack(
            side="left", padx=(6, 0)
        )
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)

        ttk.Label(bar, text="搜索：").pack(side="left")
        self.search_entry = ttk.Entry(bar, textvariable=self.search_var, width=14)
        self.search_entry.pack(side="left")
        self.search_entry.bind("<Return>", self.do_search)
        self.search_entry.bind("<Escape>", lambda _e: self.clear_search())
        ttk.Button(bar, text="搜索", command=self.do_search).pack(side="left", padx=(4, 0))
        self._refresh_folder_combo()

    def _build_status(self) -> None:
        status = ttk.Frame(self, padding=(8, 3))
        status.pack(fill="x", side="bottom")
        self.status_left = ttk.Label(status, text="", foreground=COLOR_MUTED, font=FONT_SMALL)
        self.status_left.pack(side="left")
        self.status_right = ttk.Label(status, text="", foreground=COLOR_MUTED, font=FONT_SMALL)
        self.status_right.pack(side="right")

    def _build_body(self) -> None:
        self.paned = ttk.PanedWindow(self, orient="horizontal")
        self.paned.pack(fill="both", expand=True, padx=6, pady=(0, 4))
        self.left_container = ttk.Frame(self.paned)
        self.calendar = CalendarView(self.left_container, self, on_select=self.on_day_selected)
        self.list_view = ListView(self.left_container, self)
        self.doc_panel = DocPanel(self.paned, self)
        self.graph_view = GraphView(self.left_container, self)
        self.paned.add(self.left_container, weight=0)
        self.paned.add(self.doc_panel, weight=1)
        self._apply_view(self.view_mode, persist=False)
        self.after(120, self._set_initial_sash)

    def _apply_view(self, mode: str, persist: bool = True) -> None:
        """在日历 / 列表 / 关系图之间切换。"""
        target = mode if mode in ("list", "graph") else "calendar"
        previous = self.view_mode
        if target == "graph" and previous != "graph":
            try:
                self._sash_before_graph = self.paned.sashpos(0)
            except tk.TclError:
                pass
        self.view_mode = target
        self.view_var.set(self.view_mode)
        self.calendar.pack_forget()
        self.list_view.pack_forget()
        self.graph_view.pack_forget()
        if self.view_mode == "list":
            self.list_view.reload()
            self.list_view.pack(fill="both", expand=True)
        elif self.view_mode == "graph":
            self.graph_view.reload()
            self.graph_view.pack(fill="both", expand=True)
            self.after(150, self._ensure_graph_width)
        else:
            self.calendar.pack(fill="both", expand=True)
        if previous == "graph" and target != "graph":
            try:
                self.paned.sashpos(0, getattr(self, "_sash_before_graph", 500))
            except tk.TclError:
                pass
        if persist:
            repo.set_setting(self.conn, "view.default", self.view_mode)
            self.log.info("切换视图：%s", self.view_mode)

    def _ensure_graph_width(self) -> None:
        """关系图需要更宽的画布；左侧太窄时自动放大一次（用户仍可拖动）。"""
        if self.view_mode != "graph":
            return
        try:
            if not self.paned.winfo_exists():
                return
            total = self.paned.winfo_width()
            if total < 600:
                return
            if self.paned.sashpos(0) < 760:
                self.paned.sashpos(0, min(980, int(total * 0.68)))
        except tk.TclError:
            pass

    def _on_view_change(self) -> None:
        self._apply_view(self.view_var.get())

    def _set_initial_sash(self) -> None:
        try:
            self.paned.sashpos(0, 500)
        except tk.TclError:
            pass
        if self.view_mode == "graph":
            self.after(150, self._ensure_graph_width)

    # -- 文件夹作用域 -------------------------------------------------------

    def _resolve_initial_folder(self) -> int | None:
        saved = repo.get_setting(self.conn, "current.folder_id", "") or ""
        folders = repo.list_folders(self.conn)
        if saved == "all":
            return None
        if saved.isdigit():
            folder_id = int(saved)
            if any(int(f["id"]) == folder_id for f in folders):
                return folder_id
        if folders:
            return int(folders[0]["id"])
        return None

    def _folder_choices(self) -> list[tuple[str, int | None]]:
        choices: list[tuple[str, int | None]] = [("全部文件夹", None)]
        seen: dict[str, int] = {}
        for folder in repo.list_folders(self.conn):
            name = str(folder["name"])
            seen[name] = seen.get(name, 0) + 1
            label = name if seen[name] == 1 else f"{name} ({seen[name]})"
            choices.append((label, int(folder["id"])))
        return choices

    def _refresh_folder_combo(self) -> None:
        choices = self._folder_choices()
        self._folder_choices_cache = choices
        self.folder_combo.configure(values=[label for label, _ in choices])
        current = "全部文件夹"
        for label, folder_id in choices:
            if folder_id == self.current_folder_id:
                current = label
                break
        self.folder_combo.set(current)

    def _on_folder_combo_selected(self, _event=None) -> None:
        index = self.folder_combo.current()
        choices = getattr(self, "_folder_choices_cache", [])
        if 0 <= index < len(choices):
            self.switch_folder(choices[index][1])

    def switch_folder(self, folder_id: int | None) -> None:
        if folder_id == self.current_folder_id:
            return
        self._apply_folder(folder_id)

    def _apply_folder(self, folder_id: int | None) -> None:
        self.current_folder_id = folder_id
        repo.set_setting(
            self.conn,
            "current.folder_id",
            "all" if folder_id is None else str(folder_id),
        )
        self._refresh_folder_combo()
        self.search_var.set("")
        self.doc_panel.reset_for_folder()
        self.calendar.reload()
        self._reload_active_view()
        selected = self.calendar.selected
        if selected and repo.documents_for_day(
            self.conn, selected, folder_id=folder_id
        ):
            self.doc_panel.show_day(selected)
        else:
            if selected:
                self.calendar.clear_selection()
            self.doc_panel.show_recent()
        self._update_title()
        self.update_status()
        self.log.info("切换文件夹作用域：%s", self._current_folder_label())

    def _current_folder(self):
        if self.current_folder_id is None:
            return None
        return repo.get_folder(self.conn, self.current_folder_id)

    def _current_folder_label(self) -> str:
        folder = self._current_folder()
        return str(folder["name"]) if folder else "全部文件夹"

    def current_folder_label(self) -> str:
        """当前作用域名称（供子视图显示）。"""
        return self._current_folder_label()

    def _update_title(self) -> None:
        self.title(f"{APP_TITLE}  v{APP_VERSION}  —  {self._current_folder_label()}")

    # -- 状态与刷新 ---------------------------------------------------------

    def _initial_refresh(self) -> None:
        if not self.winfo_exists():
            return
        day = repo.get_setting(self.conn, "last.selected_day", "") or ""
        if (
            day
            and dates.try_parse_day(day)
            and repo.documents_for_day(
                self.conn, day, folder_id=self.current_folder_id
            )
        ):
            self.calendar.select_day(day, notify=False)
            self.doc_panel.show_day(day)
        self.update_status()

    def update_status(self) -> None:
        stats = repo.stats(self.conn, folder_id=self.current_folder_id)
        folder = self._current_folder()
        if folder is None:
            text = f"{stats['folders']} 个文件夹 · {stats['documents']} 篇文档"
        else:
            text = f"「{folder['name']}」 {stats['documents']} 篇文档"
        if stats["missing"]:
            text += f" · {stats['missing']} 篇缺失"
        if self._offline_paths:
            text += f" · {len(self._offline_paths)} 个文件夹离线"
        self.status_left.config(text=text)
        if not self._scan_running:
            last = repo.last_scan_time(self.conn)
            self.status_right.config(
                text=f"上次扫描：{dates.short_time(last)}" if last else "尚未扫描"
            )

    def refresh_all(self) -> None:
        if not self.winfo_exists():
            return
        self.calendar.reload()
        self._reload_active_view()
        self.doc_panel.refresh_all()
        self.update_status()

    def _reload_active_view(self) -> None:
        if self.view_mode == "list":
            self.list_view.reload()
        elif self.view_mode == "graph":
            self.graph_view.reload()

    def on_day_selected(self, day: str) -> None:
        self.doc_panel.show_day(day)

    def jump_to_day(self, day: str, doc_id: int | None = None) -> None:
        self.calendar.select_day(day, notify=False)
        self.doc_panel.show_day(day, select_doc_id=doc_id)

    def _go_today(self) -> None:
        self.calendar.go_today()

    def _first_run_hint(self) -> None:
        if not self.winfo_exists():
            return
        if repo.list_folders(self.conn):
            return
        if messagebox.askyesno(
            "欢迎使用 EasyDocument",
            "还没有添加管理文件夹。\n\n现在选择一个存放文档（Word / PPT / Markdown）的文件夹开始吗？",
        ):
            self.add_folder_dialog()

    # -- 搜索 ---------------------------------------------------------------

    def _focus_search(self, _event=None):
        self.search_entry.focus_set()
        self.search_entry.select_range(0, "end")
        return "break"

    def _is_typing(self) -> bool:
        """焦点在输入框里时，全局快捷键不应抢占按键。"""
        widget = self.focus_get()
        if widget is None:
            return False
        try:
            return widget.winfo_class() in {
                "Entry",
                "TEntry",
                "Text",
                "TCombobox",
                "Spinbox",
                "TSpinbox",
            }
        except tk.TclError:
            return False

    def _on_enter(self, _event=None):
        if self._is_typing():
            return None
        if self.doc_panel.current_doc_id is not None:
            self.open_document(self.doc_panel.current_doc_id)
        return None

    def _on_ctrl_enter(self, _event=None):
        if self._is_typing():
            return None
        if self.doc_panel.current_doc_id is not None:
            self.add_record_dialog(self.doc_panel.current_doc_id)
        return None

    def _on_escape(self, _event=None):
        if self.search_var.get().strip():
            self.clear_search()
        elif self.calendar.selected or self.calendar.multi:
            self.calendar.clear_selection()
            self.doc_panel.show_recent()
        return None

    def _on_undo_shortcut(self, _event=None):
        if self._is_typing():
            return None
        if self.view_mode != "graph":
            return None
        return self.graph_view.undo()

    def undo(self) -> None:
        """菜单入口：关系图撤销。"""
        if self.view_mode != "graph":
            messagebox.showinfo(
                "撤销", "当前只在关系图视图支持撤销（切换到关系图后再试）。"
            )
            return
        self.graph_view.undo()

    def do_search(self, _event=None) -> None:
        text = self.search_var.get().strip()
        if not text:
            self.clear_search()
            return
        rows = repo.search(self.conn, text, folder_id=self.current_folder_id)
        self.doc_panel.show_search(text, rows)
        self.status_right.config(
            text=f"{self._current_folder_label()} 中搜索「{text}」：{len(rows)} 条记录"
        )

    def clear_search(self) -> None:
        self.search_var.set("")
        self.doc_panel.reset_search()
        self.update_status()

    # -- 扫描 ---------------------------------------------------------------

    def start_scan(self, scan_all: bool = False) -> None:
        if self._scan_running:
            return
        folders = repo.list_folders(self.conn)
        if not folders:
            if messagebox.askyesno("提示", "还没有添加管理文件夹，现在添加吗？"):
                self.add_folder_dialog()
            return

        if scan_all or self.current_folder_id is None:
            targets = folders
        else:
            targets = [
                f for f in folders if int(f["id"]) == self.current_folder_id
            ] or folders

        self._scan_running = True
        self._scan_results = []
        self._scan_error = None
        self._scan_queue = queue.Queue()
        self._set_scan_buttons(False)
        scope = "全部文件夹" if len(targets) != 1 else str(targets[0]["name"])
        self.status_right.config(text=f"正在扫描 {scope}…")

        snapshot = [
            (int(f["id"]), f["path"], bool(f["recursive"]), bool(f["auto_add"]))
            for f in targets
        ]
        threading.Thread(
            target=self._scan_worker,
            args=(snapshot, self.extensions, self._scan_queue),
            daemon=True,
            name="easy-document-scan",
        ).start()
        self.after(120, self._poll_scan)
        self.log.info("开始扫描 %d 个文件夹（%s）", len(snapshot), scope)

    @staticmethod
    def _scan_worker(snapshot, extensions, journal: queue.Queue) -> None:
        try:
            for folder_id, path, recursive, _auto_add in snapshot:
                accessible = scanner.folder_accessible(path)
                files = scanner.scan_folder(path, extensions, recursive) if accessible else []
                journal.put(("folder", folder_id, accessible, files))
            journal.put(("done",))
        except Exception as exc:  # 线程内异常必须回传主线程
            journal.put(("error", str(exc)))

    def _poll_scan(self) -> None:
        if self._scan_queue is None:
            return
        try:
            while True:
                message = self._scan_queue.get_nowait()
                kind = message[0]
                if kind == "folder":
                    self._scan_results.append((message[1], message[2], message[3]))
                elif kind == "error":
                    self._scan_error = message[1]
                elif kind == "done":
                    self._finish_scan()
                    return
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(120, self._poll_scan)

    def _finish_scan(self) -> None:
        self._scan_running = False
        self._scan_queue = None
        self._set_scan_buttons(True)
        if self._scan_error:
            self.log.error("扫描失败：%s", self._scan_error)
            messagebox.showerror("扫描失败", self._scan_error)
            self.status_right.config(text="扫描失败")
        else:
            try:
                self._apply_scan_results()
            except Exception as exc:
                self.log.exception("处理扫描结果失败")
                messagebox.showerror("处理扫描结果失败", str(exc))
            self.refresh_all()
            if self._last_scan_summary:
                self.status_right.config(text=self._last_scan_summary)
                self._last_scan_summary = None

    def _apply_scan_results(self) -> None:
        added = missing = updated = relinked = backfilled = 0
        inaccessible: list[str] = []
        new_files: list[tuple] = []
        missing_docs: list = []
        changed_docs: list[tuple[int, str, str]] = []
        known = {scanner.path_key(d["path"]) for d in repo.list_documents(self.conn)}

        for folder_id, accessible, files in self._scan_results:
            folder = repo.get_folder(self.conn, folder_id)
            if folder is None:
                continue
            repo.touch_folder_scan(self.conn, folder_id)
            if not accessible:
                # 盘符离线 / 权限拒绝：只算文件夹离线，不能把里面的文档标成缺失
                inaccessible.append(folder["path"])
                continue

            existing = {
                scanner.path_key(d["path"]): d
                for d in repo.list_documents(self.conn, folder_id=folder_id)
            }
            current = {scanner.path_key(fi.path): fi for fi in files}

            for key, fi in current.items():
                doc = existing.get(key)
                if doc is None:
                    if key not in known:
                        new_files.append((folder, fi))
                        known.add(key)
                    continue
                if doc["status"] == "missing":
                    repo.set_document_status(self.conn, int(doc["id"]), "active", None)
                if doc["mtime"] != fi.mtime or int(doc["size"]) != fi.size:
                    repo.update_document(
                        self.conn,
                        int(doc["id"]),
                        mtime=fi.mtime,
                        ctime=fi.ctime,
                        size=fi.size,
                        file_id=fi.file_id,
                    )
                    updated += 1
                    if doc["mtime"] != fi.mtime:
                        changed_docs.append(
                            (int(doc["id"]), fi.mtime_day, str(doc["name"]))
                        )

            for key, doc in existing.items():
                if key in current:
                    continue
                if doc["status"] == "active":
                    missing_docs.append(doc)

        # 消失的路径 + 新出现的路径，若文件索引号(file_id)相同 → 同一文件被移动/改名
        folder_of = {id(fi): folder for folder, fi in new_files}
        candidates = [fi for _folder, fi in new_files]
        matched_paths: set[str] = set()
        relinked_ids: set[int] = set()
        for doc, fi in scanner.match_by_file_id(missing_docs, candidates):
            folder = folder_of.get(id(fi))
            try:
                repo.relink_document(
                    self.conn,
                    int(doc["id"]),
                    path=str(fi.path),
                    rel_path=fi.rel_path,
                    name=fi.name,
                    ext=fi.ext,
                    size=fi.size,
                    mtime=fi.mtime,
                    ctime=fi.ctime,
                    file_id=fi.file_id,
                    folder_id=int(folder["id"]) if folder is not None else None,
                )
            except Exception:
                self.log.exception("自动重连失败：%s", fi.path)
                continue
            relinked += 1
            relinked_ids.add(int(doc["id"]))
            matched_paths.add(scanner.path_key(fi.path))
            self.log.info("自动重连（移动/改名）：%s -> %s", doc["path"], fi.path)

        if matched_paths:
            new_files = [
                (folder, fi)
                for folder, fi in new_files
                if scanner.path_key(fi.path) not in matched_paths
            ]

        for doc in missing_docs:
            if int(doc["id"]) in relinked_ids:
                continue
            repo.set_document_status(
                self.conn, int(doc["id"]), "missing", dates.iso_now()
            )
            missing += 1

        # mtime 变化：提示在修改当天补一条记录（默认只提示，不自动写）
        if changed_docs and repo.get_bool(self.conn, "scan.prompt_mtime", True):
            pending: list[tuple[int, str, str]] = []
            seen: set[tuple[int, str]] = set()
            for doc_id, day, name in changed_docs:
                if not day or (doc_id, day) in seen:
                    continue
                seen.add((doc_id, day))
                if not repo.has_entry_on_day(self.conn, doc_id, day):
                    pending.append((doc_id, day, name))
            if pending:
                preview = "\n".join(
                    f"· {name}（{day}）" for _doc_id, day, name in pending[:8]
                )
                if len(pending) > 8:
                    preview += f"\n…共 {len(pending)} 篇"
                if messagebox.askyesno(
                    "文档有更新",
                    f"扫描发现 {len(pending)} 篇文档的修改日期有变化：\n\n{preview}\n\n"
                    "是否在修改当天各补一条「文件有更新」记录？\n"
                    "（只写本工具的记录，不会改动文档）",
                ):
                    for doc_id, day, _name in pending:
                        try:
                            repo.add_entry(
                                self.conn, doc_id, day, "文件有更新", "scan"
                            )
                            backfilled += 1
                        except ValueError:
                            pass
                    self.log.info("补记 mtime 变化记录：%d 条", backfilled)

        auto_files = [(folder, fi) for folder, fi in new_files if folder["auto_add"]]
        manual_files = [(folder, fi) for folder, fi in new_files if not folder["auto_add"]]

        for folder, fi in auto_files:
            if self._insert_file(folder, fi, self._default_day(fi), ""):
                added += 1

        if manual_files:
            dialog = ImportDialog(self, manual_files, default_source=self.date_source)
            for item in dialog.result or []:
                if self._insert_file(
                    item["folder"], item["fi"], item["day"], item["note"]
                ):
                    added += 1

        self._offline_paths = inaccessible
        parts = []
        if added:
            parts.append(f"新增 {added} 篇")
        if relinked:
            parts.append(f"自动重连 {relinked} 篇")
        if missing:
            parts.append(f"缺失 {missing} 篇")
        if updated:
            parts.append(f"更新 {updated} 篇")
        if backfilled:
            parts.append(f"补记录 {backfilled} 条")
        if inaccessible:
            parts.append(f"{len(inaccessible)} 个文件夹不可访问")
        summary = "扫描完成：" + ("，".join(parts) if parts else "无变化")
        self._last_scan_summary = summary
        self.log.info(summary)

        if inaccessible:
            messagebox.showwarning(
                "文件夹不可访问",
                "以下文件夹当前无法访问，已跳过（未标记缺失）：\n\n" + "\n".join(inaccessible),
            )

    def _default_day(self, fi: scanner.FileInfo) -> str:
        if self.date_source == "added":
            return dates.today_str()
        return fi.mtime_day

    def _insert_file(self, folder, fi: scanner.FileInfo, day: str, note: str) -> bool:
        try:
            doc_id = repo.add_document(
                self.conn,
                int(folder["id"]),
                str(fi.path),
                fi.rel_path,
                fi.name,
                fi.ext,
                fi.size,
                fi.mtime,
                fi.ctime,
                fi.file_id,
            )
            repo.add_entry(self.conn, doc_id, day, note, "import")
            return True
        except Exception:
            self.log.exception("导入文件失败：%s", fi.path)
            return False

    def _set_scan_buttons(self, enabled: bool) -> None:
        state = ["!disabled"] if enabled else ["disabled"]
        self.btn_scan.state(state)
        self.btn_folder.state(state)
        try:
            self.folder_combo.configure(state="readonly" if enabled else "disabled")
        except tk.TclError:
            pass

    # -- 文档动作 -----------------------------------------------------------

    def open_document(self, doc_id: int) -> None:
        doc = repo.get_document(self.conn, doc_id)
        if doc is None:
            return
        path = Path(doc["path"])
        if doc["status"] == "missing" or not path.exists():
            if doc["status"] != "missing":
                repo.set_document_status(self.conn, doc_id, "missing", dates.iso_now())
                self.refresh_all()
            if messagebox.askyesno(
                "文件不存在",
                f"文件不在此路径：\n{path}\n\n是否重新链接到它的新位置？",
            ):
                self.relink_document(doc_id)
            return
        try:
            fileops.open_file(path)
        except fileops.FileAccessError as exc:
            messagebox.showerror("打开失败", str(exc))
            return
        self.log.info("打开文档：%s", path)
        if repo.get_bool(self.conn, "auto.log_on_open", False):
            try:
                repo.add_entry(
                    self.conn, doc_id, dates.today_str(), "打开文档", "auto"
                )
            except ValueError:
                pass
            self.refresh_all()

    def reveal_document(self, doc_id: int) -> None:
        doc = repo.get_document(self.conn, doc_id)
        if doc is None:
            return
        try:
            fileops.reveal_in_explorer(doc["path"])
        except fileops.FileAccessError as exc:
            messagebox.showerror("定位失败", str(exc))

    def add_record_for_current(self) -> None:
        doc_id = self.doc_panel.current_doc_id
        if doc_id is None:
            messagebox.showinfo("提示", "请先在右侧列表中选择一篇文档。")
            return
        self.add_record_dialog(doc_id)

    def add_record_dialog(self, doc_id: int) -> None:
        doc = repo.get_document(self.conn, doc_id)
        if doc is None:
            return
        default_day = self.doc_panel.day or dates.today_str()
        dialog = RecordDialog(
            self,
            title=f"新增记录 · {doc['name']}",
            day=default_day,
            file_mtime=doc["mtime"],
        )
        if not dialog.result:
            return
        try:
            repo.add_entry(
                self.conn,
                doc_id,
                dialog.result["day"],
                dialog.result["note"],
                "manual",
            )
        except ValueError as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        self.log.info("新增记录：doc=%s day=%s", doc_id, dialog.result["day"])
        self.refresh_all()

    def edit_entry_dialog(self, entry_id: int) -> None:
        entry = repo.get_entry(self.conn, entry_id)
        if entry is None:
            return
        doc = repo.get_document(self.conn, int(entry["document_id"]))
        dialog = RecordDialog(
            self,
            title=f"编辑记录 · {doc['name'] if doc else ''}",
            day=entry["day"],
            note=entry["note"],
            file_mtime=doc["mtime"] if doc else None,
        )
        if not dialog.result:
            return
        try:
            repo.update_entry(
                self.conn,
                entry_id,
                day=dialog.result["day"],
                note=dialog.result["note"],
            )
        except ValueError as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        self.log.info("修改记录：entry=%s", entry_id)
        self.refresh_all()

    def delete_entry(self, entry_id: int) -> None:
        entry = repo.get_entry(self.conn, entry_id)
        if entry is None:
            return
        preview = (entry["note"] or "").strip().splitlines()
        preview_text = preview[0][:40] if preview else "（无备注）"
        if not messagebox.askyesno(
            "删除记录",
            f"确定删除这条记录吗？\n\n{entry['day']}  {preview_text}\n\n"
            "只删除记录，不会影响磁盘上的文档文件。",
        ):
            return
        repo.delete_entry(self.conn, entry_id)
        self.log.info("删除记录：entry=%s", entry_id)
        self.refresh_all()

    def relink_document(self, doc_id: int) -> None:
        doc = repo.get_document(self.conn, doc_id)
        if doc is None:
            return
        chosen = filedialog.askopenfilename(
            parent=self,
            title="选择文件的新位置",
            filetypes=build_file_types(self.extensions),
        )
        if not chosen:
            return
        new_path = Path(chosen)
        if not new_path.is_file():
            messagebox.showerror("路径无效", f"文件不存在：\n{new_path}")
            return
        existing = repo.get_document_by_path(self.conn, str(new_path))
        if existing is not None and int(existing["id"]) != doc_id:
            messagebox.showerror(
                "无法重新链接",
                f"该文件已经以另一条索引存在：\n{existing['path']}",
            )
            return
        folder = repo.get_folder(self.conn, int(doc["folder_id"]))
        rel_path = new_path.name
        if folder is not None:
            try:
                rel_path = os.path.relpath(new_path, folder["path"])
            except ValueError:
                rel_path = new_path.name
        try:
            stat = new_path.stat()
        except OSError as exc:
            messagebox.showerror("读取失败", str(exc))
            return
        inode = getattr(stat, "st_ino", 0)
        file_id = f"{getattr(stat, 'st_dev', 0)}:{inode}" if inode else None
        repo.relink_document(
            self.conn,
            doc_id,
            path=str(new_path),
            rel_path=rel_path,
            name=new_path.name,
            ext=new_path.suffix.lower(),
            size=stat.st_size,
            mtime=dates.timestamp_iso(stat.st_mtime),
            ctime=dates.timestamp_iso(stat.st_ctime),
            file_id=file_id,
        )
        self.log.info("重新链接：doc=%s -> %s", doc_id, new_path)
        self.refresh_all()

    def remove_document(self, doc_id: int) -> None:
        doc = repo.get_document(self.conn, doc_id)
        if doc is None:
            return
        if not messagebox.askyesno(
            "移除索引",
            f"从索引中移除该文档及其日期记录？\n\n{doc['name']}\n{doc['path']}\n\n"
            "只修改数据库，不会删除磁盘上的文档文件。",
        ):
            return
        repo.remove_document(self.conn, doc_id)
        self.log.info("移除索引：doc=%s path=%s", doc_id, doc["path"])
        self.refresh_all()

    # -- 拖入文件移动 -------------------------------------------------------

    def _install_drop_target(self) -> None:
        """窗口显示后挂上 WM_DROPFILES；失败只记日志，不影响其它功能。"""
        if not self.winfo_exists():
            return
        try:
            target = WindowsDropTarget(self, self.on_files_dropped)
            if target.install():
                self._drop_target = target
                self.log.info("已启用文件拖放：拖入窗口可移动到管理文件夹")
        except Exception:
            self.log.exception("启用文件拖放失败（应用其它功能不受影响）")

    def on_files_dropped(self, raw_paths: list[str]) -> None:
        files = normalize_dropped_paths(raw_paths)
        if not files:
            messagebox.showinfo(
                "没有可移动的文件",
                "拖入的内容中没有文件。暂不支持拖入文件夹。",
            )
            return
        allowed = [
            path for path in files if path.suffix.lower() in self.extensions
        ]
        ignored = [path for path in files if path not in allowed]
        if not allowed:
            preview = "\n".join(f"· {path.name}" for path in ignored[:8])
            more = f"\n…共 {len(ignored)} 个" if len(ignored) > 8 else ""
            messagebox.showwarning(
                "不支持的文件类型",
                "拖入的文件不在当前管理的文件类型中：\n\n"
                f"{preview}{more}\n\n"
                "可在「视图 → 设置…」里调整管理的文件类型。",
            )
            return

        folders = repo.list_folders(self.conn)
        if not folders:
            messagebox.showinfo(
                "没有目标文件夹",
                "还没有添加管理文件夹，无法确定移动到哪里。",
            )
            return
        # 单个文件且当前作用域就是具体文件夹：按需求“拖入即可移动”，直接处理；
        # 多个文件或当前是「全部文件夹」时才弹选择框。
        if len(allowed) == 1 and self.current_folder_id is not None:
            folder = repo.get_folder(self.conn, self.current_folder_id)
            if folder is not None:
                target_dir = Path(folder["path"])
                if not scanner.folder_accessible(target_dir):
                    messagebox.showerror(
                        "目标文件夹不可用",
                        f"无法访问目标文件夹（盘符离线或权限不足）：\n{target_dir}",
                    )
                    return
                self._move_dropped_files(
                    [str(allowed[0])], folder, ignored, quiet=True
                )
                return

        initial = self.current_folder_id
        if initial is None:
            initial = int(folders[0]["id"])
        dialog = DropMoveDialog(
            self,
            [str(path) for path in allowed],
            folders,
            initial_folder_id=initial,
        )
        if not dialog.result:
            return
        folder = repo.get_folder(self.conn, int(dialog.result["folder_id"]))
        if folder is None:
            return
        target_dir = Path(folder["path"])
        if not scanner.folder_accessible(target_dir):
            messagebox.showerror(
                "目标文件夹不可用",
                f"无法访问目标文件夹（盘符离线或权限不足）：\n{target_dir}",
            )
            return
        self._move_dropped_files(dialog.result["paths"], folder, ignored)

    def _move_dropped_files(
        self,
        raw_paths: list[str],
        folder,
        ignored: list[Path],
        *,
        quiet: bool = False,
    ) -> None:
        target_dir = Path(folder["path"])
        reserved = {
            scanner.path_key(doc["path"]) for doc in repo.list_documents(self.conn)
        }
        moved = 0
        relinked = 0
        indexed = 0
        skipped = 0
        errors: list[str] = []
        for raw in raw_paths:
            source = Path(raw)
            try:
                if not source.is_file():
                    raise fileops.FileAccessError("源文件已不存在")
                if scanner.path_key(source.parent) == scanner.path_key(target_dir):
                    skipped += 1
                    continue
                document = repo.get_document_by_path(self.conn, str(source))
                destination = fileops.unique_destination(
                    target_dir, source.name, reserved
                )
                fileops.move_file(source, destination)
                reserved.add(scanner.path_key(destination))
                moved += 1
                if document is not None:
                    self._relink_moved_document(int(document["id"]), destination, folder)
                    relinked += 1
                else:
                    info = scanner.make_file_info(destination, target_dir)
                    if self._insert_file(
                        folder, info, self._default_day(info), "拖入移动"
                    ):
                        indexed += 1
                self.log.info("拖入移动：%s -> %s", source, destination)
            except Exception as exc:
                self.log.exception("拖入移动失败：%s", source)
                errors.append(f"{source.name}：{exc}")

        self.refresh_all()
        parts = []
        if moved:
            parts.append(f"移动 {moved} 个")
        if relinked:
            parts.append(f"保留历史索引 {relinked} 个")
        if indexed:
            parts.append(f"新增索引 {indexed} 个")
        if skipped:
            parts.append(f"{skipped} 个已在目标文件夹、未移动")
        if ignored:
            parts.append(f"跳过不支持的类型 {len(ignored)} 个")
        summary = "；".join(parts) if parts else "没有文件被移动"
        self.status_right.config(text=f"拖入移动：{summary}")
        if errors:
            preview = "\n".join(f"· {item}" for item in errors[:8])
            more = f"\n…共 {len(errors)} 个失败" if len(errors) > 8 else ""
            messagebox.showerror(
                "部分文件未能移动",
                f"已移动到「{folder['name']}」：{summary}\n\n"
                f"以下文件失败：\n{preview}{more}\n\n"
                "文件被占用或没有权限时，请关闭占用程序后重试。",
            )
        elif not quiet:
            messagebox.showinfo(
                "移动完成",
                f"已移动到「{folder['name']}」：\n\n{summary}\n\n"
                "只移动了文件位置，日期记录历史随索引保留。",
            )

    def _relink_moved_document(self, doc_id: int, destination: Path, folder) -> None:
        target_dir = Path(folder["path"])
        try:
            rel_path = os.path.relpath(destination, target_dir)
        except ValueError:
            rel_path = destination.name
        stat = destination.stat()
        inode = getattr(stat, "st_ino", 0)
        file_id = f"{getattr(stat, 'st_dev', 0)}:{inode}" if inode else None
        repo.relink_document(
            self.conn,
            doc_id,
            path=str(destination),
            rel_path=rel_path,
            name=destination.name,
            ext=destination.suffix.lower(),
            size=stat.st_size,
            mtime=dates.timestamp_iso(stat.st_mtime),
            ctime=dates.timestamp_iso(stat.st_ctime),
            file_id=file_id,
            folder_id=int(folder["id"]),
        )

    # -- 时间段（多日分组） -------------------------------------------------

    def open_period_manager(self) -> None:
        dialog = PeriodManagerDialog(self, self)
        self.wait_window(dialog)
        self.refresh_all()

    def create_period_from_days(self, days: list[str]) -> None:
        dialog = PeriodDialog(
            self,
            title="新建时间段",
            days=days,
            color=PERIOD_COLORS[0] if PERIOD_COLORS else None,
        )
        if not dialog.result:
            return
        data = dialog.result
        try:
            repo.create_period(
                self.conn,
                data["name"],
                data["days"],
                color=data["color"],
                note=data["note"],
            )
        except ValueError as exc:
            if not messagebox.askyesno(
                "日期冲突", f"{exc}\n\n要把这些日期移到新时间段「{data['name']}」吗？"
            ):
                return
            try:
                repo.create_period(
                    self.conn,
                    data["name"],
                    data["days"],
                    color=data["color"],
                    note=data["note"],
                    move=True,
                )
            except ValueError as exc2:
                messagebox.showerror("创建失败", str(exc2))
                return
        self.calendar.clear_multi()
        self.log.info("新建时间段：%s（%d 天）", data["name"], len(data["days"]))
        self.refresh_all()

    def edit_period_dialog(self, period_id: int) -> None:
        period = repo.get_period(self.conn, period_id)
        if period is None:
            return
        dialog = PeriodDialog(
            self,
            title=f"编辑时间段 · {period['name']}",
            name=str(period["name"]),
            color=period["color"],
            note=str(period["note"] or ""),
            days=repo.period_days(self.conn, period_id),
        )
        if not dialog.result:
            return
        data = dialog.result
        try:
            repo.set_period_days(self.conn, period_id, data["days"])
        except ValueError as exc:
            if not messagebox.askyesno(
                "日期冲突",
                f"{exc}\n\n要把这些日期移到「{data['name']}」吗？",
            ):
                return
            try:
                repo.set_period_days(self.conn, period_id, data["days"], move=True)
            except ValueError as exc2:
                messagebox.showerror("保存失败", str(exc2))
                return
        repo.update_period(
            self.conn,
            period_id,
            name=data["name"],
            color=data["color"],
            note=data["note"],
        )
        self.log.info("编辑时间段：id=%s（%d 天）", period_id, len(data["days"]))
        self.refresh_all()

    def delete_period(self, period_id: int) -> None:
        period = repo.get_period(self.conn, period_id)
        if period is None:
            return
        days = repo.period_days(self.conn, period_id)
        if not messagebox.askyesno(
            "删除时间段",
            f"删除时间段「{period['name']}」？\n\n"
            f"包含 {len(days)} 天。只解除日期归属，文档与日期记录都不受影响。",
        ):
            return
        repo.delete_period(self.conn, period_id)
        self.log.info("删除时间段：id=%s name=%s", period_id, period["name"])
        self.refresh_all()

    def add_days_to_period(self, period_id: int, days: list[str]) -> None:
        period = repo.get_period(self.conn, period_id)
        if period is None:
            return
        try:
            repo.add_period_days(self.conn, period_id, days)
        except ValueError as exc:
            if not messagebox.askyesno(
                "日期冲突",
                f"{exc}\n\n要把这些日期移到「{period['name']}」吗？",
            ):
                return
            try:
                repo.add_period_days(self.conn, period_id, days, move=True)
            except ValueError as exc2:
                messagebox.showerror("加入失败", str(exc2))
                return
        self.calendar.clear_multi()
        self.log.info("加入时间段：id=%s days=%d", period_id, len(days))
        self.refresh_all()

    def remove_days_from_period(self, days: list[str]) -> None:
        day_map = repo.period_day_map(self.conn)
        targets: dict[int, list[str]] = {}
        for day in days:
            info = day_map.get(day)
            if info is not None:
                targets.setdefault(int(info["period_id"]), []).append(day)
        if not targets:
            messagebox.showinfo("提示", "所选日期不属于任何时间段。")
            return
        total = sum(len(items) for items in targets.values())
        if not messagebox.askyesno(
            "移出时间段",
            f"把 {total} 天从所属时间段移出？\n\n只解除分组，文档与日期记录都不受影响。",
        ):
            return
        for period_id, items in targets.items():
            repo.remove_period_days(self.conn, period_id, items)
        self.calendar.clear_multi()
        self.log.info("从时间段移出日期：%d 天", total)
        self.refresh_all()

    # -- 文件夹与设置 -------------------------------------------------------

    def add_folder_dialog(self) -> None:
        dialog = FolderDialog(self)
        if not dialog.result:
            return
        try:
            folder_id = repo.add_folder(
                self.conn,
                dialog.result["path"],
                recursive=dialog.result["recursive"],
                auto_add=dialog.result["auto_add"],
            )
        except ValueError as exc:
            messagebox.showerror("无法添加", str(exc))
            return
        self.log.info("添加文件夹：id=%s path=%s", folder_id, dialog.result["path"])
        self.switch_folder(folder_id)
        self.start_scan()

    def remove_folder(self, folder_id: int, confirm: bool = True) -> bool:
        folder = repo.get_folder(self.conn, folder_id)
        if folder is None:
            return False
        count = repo.document_counts_by_folder(self.conn).get(folder_id, 0)
        if confirm and not messagebox.askyesno(
            "移除文件夹",
            f"将移除对该文件夹的索引（不会删除任何文件）。\n\n{folder['path']}\n"
            f"共 {count} 篇文档及它们的日期记录。\n\n确定继续？",
        ):
            return False
        repo.remove_folder(self.conn, folder_id)
        self.log.info("移除文件夹：id=%s path=%s", folder_id, folder["path"])
        if self.current_folder_id == folder_id:
            remaining = repo.list_folders(self.conn)
            target = int(remaining[0]["id"]) if remaining else None
            self.current_folder_id = None  # 强制重新应用作用域
            self._apply_folder(target)
        else:
            self.refresh_all()
            self._refresh_folder_combo()
        return True

    def manage_folders(self) -> None:
        dialog = FolderManagerDialog(self, self)
        self.wait_window(dialog)
        self._refresh_folder_combo()
        self.refresh_all()

    def open_settings(self) -> None:
        dialog = SettingsDialog(self, self.conn)
        if not dialog.result:
            return
        for key, value in dialog.result.items():
            repo.set_setting(self.conn, key, value)
        self.extensions = normalize_extensions(
            repo.get_setting(self.conn, "doc.extensions", "") or ""
        )
        self.date_source = (
            repo.get_setting(self.conn, "default.date_source", "mtime") or "mtime"
        )
        self.calendar.refresh_settings()
        self._apply_view(dialog.result.get("view.default", self.view_mode), persist=False)
        self.log.info("设置已更新：%s", dialog.result)
        self.refresh_all()

    # -- 导出与备份 ---------------------------------------------------------

    def export_json_dialog(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出 JSON（全部数据）",
            defaultextension=".json",
            initialfile=f"EasyDocument-导出-{datetime.now():%Y%m%d-%H%M}.json",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            stats = exporter.export_json(self.conn, path)
        except Exception as exc:
            self.log.exception("导出 JSON 失败")
            messagebox.showerror("导出失败", str(exc))
            return
        self.log.info("导出 JSON：%s（%s）", path, stats)
        messagebox.showinfo(
            "导出完成",
            f"已导出到：\n{path}\n\n"
            f"文件夹 {stats['folders']} 个 · 文档 {stats['documents']} 篇 · "
            f"记录 {stats['entries']} 条 · 时间段 {stats['periods']} 个 · "
            f"图板节点 {stats['nodes']} / 连线 {stats['edges']} / 注释 {stats['notes']}",
        )

    def export_csv_dialog(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出 CSV（全部记录）",
            defaultextension=".csv",
            initialfile=f"EasyDocument-记录-{datetime.now():%Y%m%d-%H%M}.csv",
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            stats = exporter.export_csv(self.conn, path)
        except Exception as exc:
            self.log.exception("导出 CSV 失败")
            messagebox.showerror("导出失败", str(exc))
            return
        self.log.info("导出 CSV：%s（%s 条）", path, stats["rows"])
        messagebox.showinfo(
            "导出完成",
            f"已导出到：\n{path}\n\n共 {stats['rows']} 条记录。\n"
            "文件为 UTF-8（带 BOM），可直接用 Excel 打开。",
        )

    def backup_now(self) -> None:
        target = self.paths.backups_dir / f"manual-{datetime.now():%Y%m%d-%H%M%S}.db"
        try:
            db.backup_to(self.conn, target)
        except Exception as exc:
            self.log.exception("手动备份失败")
            messagebox.showerror("备份失败", str(exc))
            return
        self.log.info("手动备份：%s", target)
        messagebox.showinfo("备份完成", f"数据库已备份到：\n{target}")

    def open_data_dir(self) -> None:
        try:
            os.startfile(str(self.paths.data_dir))  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror("打开失败", str(exc))

    def open_data_location(self) -> None:
        DataLocationDialog(self, self)

    def change_data_location(self, target: Path, copy: bool, delete_old: bool) -> bool:
        """切换数据目录；成功后会退出程序，要求用户重新启动。"""
        try:
            new_paths, warnings = storage.switch_data(
                self.conn, self.paths, target, copy=copy
            )
        except Exception as exc:
            self.log.exception("更改数据位置失败")
            messagebox.showerror("更改数据位置失败", str(exc))
            return False

        self.log.info(
            "数据位置切换：%s -> %s（复制=%s）",
            self.paths.data_dir,
            new_paths.data_dir,
            copy,
        )
        if warnings:
            messagebox.showwarning(
                "部分文件未复制",
                "数据库已迁移，但以下文件复制失败（原文件仍保留）：\n\n"
                + "\n".join(warnings[:8]),
            )

        if delete_old:
            try:
                self.conn.close()
            except Exception:
                pass
            errors = storage.delete_data_files(self.paths)
            if errors:
                messagebox.showwarning(
                    "部分文件未删除",
                    "以下文件未能删除，可稍后手动清理：\n\n" + "\n".join(errors[:8]),
                )

        messagebox.showinfo(
            "数据位置已更改",
            f"新位置：\n{new_paths.data_dir}\n\n程序将退出，请重新启动 EasyDocument。",
        )
        self.destroy()
        return True

    def _show_shortcuts(self) -> None:
        messagebox.showinfo(
            "快捷键与使用提示",
            "F5\t重新扫描当前文件夹\n"
            "Ctrl+F\t聚焦搜索框\n"
            "Enter\t打开当前选中文档\n"
            "Ctrl+Enter\t为当前文档新增记录\n"
            "Ctrl+Z\t撤销关系图操作（移动/添加/删除/注释/染色等）\n"
            "Ctrl+鼠标滚轮\t关系图缩放（向上滚放大）\n"
            "Esc\t清空搜索 / 取消日期选择 / 取消连线起点\n"
            "← → ↑ ↓\t在日历中移动选中日\n"
            "PageUp / PageDown\t切换月份\n\n"
            "日历：按住 Shift 连选多日，右键可批量设为时间段。\n"
            "列表视图：单击行跳到该日，双击打开文件，右键更多操作。\n"
            "关系图：拖动节点排版并自动保存；开启「连线模式」后依次点击两个节点连线；\n"
            "Ctrl+点击多选后右键可给多个文件框添加相同节点注释，或批量染色/移除。\n"
            "拖入文件：从资源管理器把文件拖进窗口，确认后移动到当前/所选文件夹。\n"
            "导出：文件 → 导出 JSON / CSV。",
        )

    def _about(self) -> None:
        messagebox.showinfo(
            "关于 EasyDocument",
            f"EasyDocument v{APP_VERSION}\n\n"
            "本地文档索引与工作日志工具（Word / PowerPoint / Markdown）。\n"
            "所有数据保存在本机，不联网。\n\n"
            f"数据目录：\n{self.paths.data_dir}",
        )

    def on_close(self) -> None:
        if self._scan_running:
            if not messagebox.askyesno("正在扫描", "扫描尚未完成，确定退出吗？"):
                return
        try:
            if self.winfo_ismapped() and self.winfo_width() > 200:
                repo.set_setting(self.conn, "window.geometry", self.geometry())
            repo.set_setting(
                self.conn, "last.selected_day", self.calendar.selected or ""
            )
            try:
                split = self.doc_panel.vsplit.sashpos(0)
                if split > 80:
                    repo.set_setting(self.conn, "doc.split", str(split))
            except Exception:
                pass
        except Exception:
            pass
        if self._drop_target is not None:
            try:
                self._drop_target.close()
            except Exception:
                pass
            self._drop_target = None
        self.log.info("关闭窗口")
        self.destroy()
