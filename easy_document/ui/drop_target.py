"""Windows 文件拖放接入：用标准库 ctypes 处理 WM_DROPFILES。

Tkinter 本身不支持从资源管理器拖入文件；这里不引入第三方依赖，
直接把 Tk 顶层窗口的 WndProc 子类化，接收 WM_DROPFILES 后把路径
入队，再由 Tk 主线程定时轮询取出处理。仅 Windows 生效，其它平台
install() 返回 False。

注意：WndProc 运行在 Tcl 事件循环内部，绝不能直接调用 Tk
（包括 after_idle），否则会触发 PyEval_RestoreThread/GIL 致命错误。
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path
from typing import Callable, Iterable

WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4
GA_ROOT = 2

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    _WNDPROC = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )
    _get_ancestor = _user32.GetAncestor
    _get_ancestor.argtypes = [wintypes.HWND, wintypes.UINT]
    _get_ancestor.restype = wintypes.HWND

    if hasattr(_user32, "SetWindowLongPtrW"):
        _set_window_long = _user32.SetWindowLongPtrW
        _set_window_long.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_void_p,
        ]
        _set_window_long.restype = ctypes.c_void_p
    else:  # 32 位 Python 上只有 SetWindowLongW
        _set_window_long = _user32.SetWindowLongW
        _set_window_long.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_void_p,
        ]
        _set_window_long.restype = ctypes.c_void_p

    _call_window_proc = _user32.CallWindowProcW
    _call_window_proc.argtypes = [
        ctypes.c_void_p,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    _call_window_proc.restype = ctypes.c_ssize_t

    _drag_accept_files = _shell32.DragAcceptFiles
    _drag_accept_files.argtypes = [wintypes.HWND, wintypes.BOOL]
    _drag_accept_files.restype = None

    _drag_query_file = _shell32.DragQueryFileW
    _drag_query_file.argtypes = [
        wintypes.HANDLE,
        wintypes.UINT,
        wintypes.LPWSTR,
        wintypes.UINT,
    ]
    _drag_query_file.restype = wintypes.UINT

    _drag_finish = _shell32.DragFinish
    _drag_finish.argtypes = [wintypes.HANDLE]
    _drag_finish.restype = None


class WindowsDropTarget:
    """挂在 Tk 顶层窗口上的拖放接收器。

    用法：
        target = WindowsDropTarget(root, lambda paths: ...)
        target.install()
        ...
        target.close()
    """

    def __init__(self, root, on_drop: Callable[[list[str]], None]) -> None:
        self.root = root
        self.on_drop = on_drop
        self._hwnd = 0
        self._old_proc: int | None = None
        self._proc = None
        self._installed = False
        # WndProc 里不能直接调用 Tk（会重入 Tcl 事件循环并破坏 GIL 状态），
        # 只把路径入队；由 Tk 主线程的轮询定时器取出处理。
        self._pending: list[list[str]] = []
        self._poll_id: str | None = None

    def install(self) -> bool:
        if not _IS_WINDOWS:
            return False
        if self._installed:
            return True
        try:
            hwnd = int(self.root.winfo_id())
            root_hwnd = int(_get_ancestor(wintypes.HWND(hwnd), GA_ROOT)) or hwnd
            self._proc = _WNDPROC(self._wndproc)
            old = _set_window_long(
                wintypes.HWND(root_hwnd),
                GWLP_WNDPROC,
                ctypes.cast(self._proc, ctypes.c_void_p),
            )
            if not old:
                raise ctypes.WinError(ctypes.get_last_error())
            self._old_proc = int(old)
            self._hwnd = root_hwnd
            _drag_accept_files(wintypes.HWND(root_hwnd), True)
            self._installed = True
            self._schedule_poll()
            return True
        except Exception:
            self._old_proc = None
            self._proc = None
            self._hwnd = 0
            self._installed = False
            raise

    def close(self) -> None:
        if not _IS_WINDOWS or not self._installed:
            return
        if self._poll_id is not None:
            try:
                self.root.after_cancel(self._poll_id)
            except Exception:
                pass
            self._poll_id = None
        try:
            if self._hwnd:
                _drag_accept_files(wintypes.HWND(self._hwnd), False)
            if self._old_proc is not None and self._hwnd:
                _set_window_long(
                    wintypes.HWND(self._hwnd),
                    GWLP_WNDPROC,
                    ctypes.c_void_p(self._old_proc),
                )
        except Exception:
            pass
        finally:
            self._installed = False
            self._old_proc = None
            self._proc = None
            self._hwnd = 0

    # -- 内部 ---------------------------------------------------------------

    def _schedule_poll(self) -> None:
        try:
            self._poll_id = self.root.after(120, self._poll)
        except Exception:
            self._poll_id = None

    def _poll(self) -> None:
        self._poll_id = None
        if not self._installed:
            return
        if self._pending:
            paths = self._pending.pop(0)
            try:
                self.on_drop(paths)
            finally:
                # 即使回调出错也继续轮询，避免一次失败后拖放永久失效。
                self._schedule_poll()
        else:
            self._schedule_poll()

    def _wndproc(self, hwnd, message, wparam, lparam):
        try:
            if message == WM_DROPFILES:
                self._handle_drop(wparam)
                return 0
        except Exception:
            # 绝不能让异常穿过 ctypes 回调
            pass
        return _call_window_proc(
            ctypes.c_void_p(self._old_proc),
            hwnd,
            message,
            wparam,
            lparam,
        )

    def _handle_drop(self, hdrop_value) -> None:
        hdrop = wintypes.HANDLE(hdrop_value)
        try:
            count = int(_drag_query_file(hdrop, 0xFFFFFFFF, None, 0))
            paths: list[str] = []
            for index in range(count):
                length = int(_drag_query_file(hdrop, index, None, 0))
                buffer = ctypes.create_unicode_buffer(length + 1)
                _drag_query_file(hdrop, index, buffer, length + 1)
                if buffer.value:
                    paths.append(buffer.value)
        finally:
            _drag_finish(hdrop)
        if paths:
            # 只入队；绝不能在 WndProc 里调用 Tk/after_idle。
            self._pending.append(paths)


def normalize_dropped_paths(paths: Iterable[str]) -> list[Path]:
    """把拖放回调里的字符串转成存在的文件路径（目录单独忽略）。"""
    result: list[Path] = []
    seen: set[str] = set()
    for raw in paths:
        try:
            path = Path(raw)
        except (TypeError, ValueError):
            continue
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            result.append(path)
    return result
