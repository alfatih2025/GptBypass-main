import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.messagebox as messagebox
from typing import Any, Dict, Optional

import customtkinter as ctk
import httpx
import uvicorn
from app_defaults import DEFAULT_CONFIG

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:
    pystray = None
    Image = None
    ImageDraw = None

if os.name == "nt":
    try:
        import win32api  # type: ignore
        import win32con  # type: ignore
        import win32gui  # type: ignore
    except Exception:
        win32api = None
        win32con = None
        win32gui = None
else:
    win32api = None
    win32con = None
    win32gui = None


ctk.set_appearance_mode("light")
ctk.set_default_color_theme("green")


PALETTE = {
    "sidebar": "#eef5f7",
    "sidebar_card": "#f9fcfd",
    "main_bg": "#f5f8fc",
    "surface": "#ffffff",
    "surface_alt": "#fcfeff",
    "surface_soft": "#edf5f7",
    "hero": "#f7fbfc",
    "border": "#dbe7ea",
    "text": "#1f3442",
    "text_muted": "#708793",
    "primary": "#79c6b2",
    "primary_hover": "#65b8a2",
    "secondary": "#e9f1f4",
    "secondary_hover": "#dce9ee",
    "danger": "#ee7d8b",
    "danger_hover": "#e46876",
    "warning": "#f0c56a",
    "log_bg": "#fbfdff",
    "log_text": "#27414c",
    "accent": "#4e9b8c",
    "accent_soft": "#eaf7f3",
    "chip": "#eef7f4",
    "info": "#eef5ff",
    "info_text": "#506d8a",
}

FONT_FAMILY = "Microsoft YaHei UI"


def get_runtime_base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(os.path.dirname(__file__))


def get_resource_base_dir() -> str:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return getattr(sys, "_MEIPASS")
    return os.path.abspath(os.path.dirname(__file__))


def resolve_resource_path(filename: str) -> str:
    candidates = [
        os.path.join(get_resource_base_dir(), filename),
        os.path.join(get_runtime_base_dir(), filename),
        os.path.join(os.getcwd(), filename),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]


def resolve_config_path() -> str:
    return os.path.join(get_runtime_base_dir(), "config.json")


def ensure_local_config_exists(default_config: Optional[Dict[str, Any]] = None) -> str:
    config_path = resolve_config_path()
    if os.path.exists(config_path):
        return config_path

    source_candidates = [
        os.path.join(os.path.abspath(os.path.dirname(__file__)), "config.json"),
        os.path.join(os.getcwd(), "config.json"),
    ]

    os.makedirs(os.path.dirname(config_path), exist_ok=True)

    for source_path in source_candidates:
        if os.path.exists(source_path) and os.path.abspath(source_path) != os.path.abspath(config_path):
            shutil.copy2(source_path, config_path)
            return config_path

    if default_config is not None:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(default_config, f, ensure_ascii=False, indent=4)

    return config_path


def resolve_log_path() -> str:
    return os.path.join(get_runtime_base_dir(), "proxy.log")


def merge_dict(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = json.loads(json.dumps(base, ensure_ascii=False))
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_dict(result[key], value)
        else:
            result[key] = value
    return result


class WindowsTrayIcon:
    WM_TRAYICON = (win32con.WM_USER + 1) if win32con is not None else 0
    ID_SHOW = 1001
    ID_START = 1002
    ID_STOP = 1003
    ID_EXIT = 1004

    def __init__(self, owner: "ProxyGuiApp") -> None:
        self.owner = owner
        self.hwnd = None
        self.thread = None
        self.class_name = f"GPT54JMPTray_{id(self)}"
        self.icon_added = False
        self.hicon = None

    def start(self) -> None:
        if win32gui is None or win32con is None:
            return
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if win32gui is None or self.hwnd is None:
            return
        try:
            win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception:
            pass
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
        self.thread = None
        self.hwnd = None

    def _run(self) -> None:
        message_map = {
            self.WM_TRAYICON: self._on_tray_notify,
            win32con.WM_COMMAND: self._on_command,
            win32con.WM_DESTROY: self._on_destroy,
            win32con.WM_CLOSE: self._on_close,
        }
        wc = win32gui.WNDCLASS()
        wc.hInstance = win32api.GetModuleHandle(None)
        wc.lpszClassName = self.class_name
        wc.lpfnWndProc = message_map
        class_atom = win32gui.RegisterClass(wc)
        self.hwnd = win32gui.CreateWindow(
            class_atom,
            self.class_name,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            wc.hInstance,
            None,
        )
        self.hicon = self._load_tray_icon()
        self._add_icon()
        win32gui.PumpMessages()

    def _load_tray_icon(self):
        if win32gui is None or win32con is None:
            return None
        icon_path = resolve_resource_path("app_icon.ico")
        if os.path.exists(icon_path):
            try:
                return win32gui.LoadImage(
                    0,
                    icon_path,
                    win32con.IMAGE_ICON,
                    0,
                    0,
                    win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE,
                )
            except Exception:
                pass
        try:
            return win32gui.LoadIcon(0, win32con.IDI_APPLICATION)
        except Exception:
            return None

    def _add_icon(self) -> None:
        if self.hwnd is None or self.hicon is None:
            return
        flags = win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP
        nid = (self.hwnd, 0, flags, self.WM_TRAYICON, self.hicon, "GPT5.4 中转代理")
        win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, nid)
        self.icon_added = True

    def _show_menu(self) -> None:
        if self.hwnd is None:
            return
        menu = win32gui.CreatePopupMenu()
        win32gui.AppendMenu(menu, win32con.MF_STRING, self.ID_SHOW, "显示主界面")
        win32gui.AppendMenu(menu, win32con.MF_STRING, self.ID_START, "启动代理")
        win32gui.AppendMenu(menu, win32con.MF_STRING, self.ID_STOP, "停止代理")
        win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, None)
        win32gui.AppendMenu(menu, win32con.MF_STRING, self.ID_EXIT, "退出程序")
        win32gui.SetForegroundWindow(self.hwnd)
        x, y = win32gui.GetCursorPos()
        win32gui.TrackPopupMenu(
            menu,
            win32con.TPM_LEFTALIGN | win32con.TPM_BOTTOMALIGN,
            x,
            y,
            0,
            self.hwnd,
            None,
        )
        win32gui.PostMessage(self.hwnd, win32con.WM_NULL, 0, 0)
        win32gui.DestroyMenu(menu)

    def _on_tray_notify(self, hwnd, msg, wparam, lparam):
        if lparam == win32con.WM_LBUTTONDBLCLK:
            self.owner._enqueue_ui_action("show")
        elif lparam in (win32con.WM_RBUTTONUP, getattr(win32con, "WM_CONTEXTMENU", 0x007B)):
            self._show_menu()
        return 0

    def _on_command(self, hwnd, msg, wparam, lparam):
        command = win32api.LOWORD(wparam)
        if command == self.ID_SHOW:
            self.owner._enqueue_ui_action("show")
        elif command == self.ID_START:
            self.owner._enqueue_ui_action("start")
        elif command == self.ID_STOP:
            self.owner._enqueue_ui_action("stop")
        elif command == self.ID_EXIT:
            self.owner._enqueue_ui_action("exit")
        return 0

    def _remove_icon(self) -> None:
        if self.icon_added and self.hwnd is not None:
            try:
                win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (self.hwnd, 0))
            except Exception:
                pass
            self.icon_added = False
        if self.hicon is not None and win32gui is not None:
            try:
                win32gui.DestroyIcon(self.hicon)
            except Exception:
                pass
            self.hicon = None

    def _on_close(self, hwnd, msg, wparam, lparam):
        win32gui.DestroyWindow(hwnd)
        return 0

    def _on_destroy(self, hwnd, msg, wparam, lparam):
        self._remove_icon()
        win32gui.PostQuitMessage(0)
        return 0


class ProxyGuiApp:
    def __init__(self) -> None:
        self.root = ctk.CTk()
        self.root.title("GPT 道德限制优化工具 v0.3")
        self.root.geometry("1360x840")
        self.root.minsize(1180, 740)
        self.root.configure(fg_color=PALETTE["main_bg"])
        self.window_icon_image: Optional[tk.PhotoImage] = None
        self._set_window_icon()
        self._center_window(1360, 840)

        self.config_path = ensure_local_config_exists(DEFAULT_CONFIG)
        self.log_path = resolve_log_path()
        self._truncate_runtime_log_file()
        self.process: Optional[subprocess.Popen] = None
        self.log_pos = 0
        self.tray_icon = None
        self.hidden_to_tray = False
        self.app_closing = False
        self.ui_action_queue: queue.SimpleQueue[str] = queue.SimpleQueue()

        self.host_var = ctk.StringVar(value="127.0.0.1")
        self.port_var = ctk.StringVar(value="8999")
        self.status_var = ctk.StringVar(value="状态：未启动")
        self.config_status_var = ctk.StringVar(value=f"配置文件：{self.config_path}")
        self.summary_model_var = ctk.StringVar(value="未设置")
        self.summary_endpoint_var = ctk.StringVar(value="127.0.0.1:8999")
        self.summary_strategy_var = ctk.StringVar(value="responses / high")
        self.summary_config_var = ctk.StringVar(value=os.path.basename(self.config_path))

        self.target_vars: Dict[str, ctk.StringVar] = {}
        self.optimization_vars: Dict[str, ctk.StringVar] = {}
        self.optimization_bool_var = ctk.BooleanVar(value=True)
        self.only_main_var = ctk.BooleanVar(value=True)
        self.secret_entries: Dict[str, ctk.CTkEntry] = {}
        self.secret_visible: Dict[str, bool] = {}
        self.secret_buttons: Dict[str, ctk.CTkButton] = {}
        self.header_status_badge: Optional[ctk.CTkLabel] = None
        self.sidebar_status: Optional[ctk.CTkLabel] = None
        self.logo_images: Dict[tuple[int, int], ctk.CTkImage] = {}
        self.target_test_button = None
        self.optimization_test_button = None

        self.target_field_labels = {
            "model": "目标模型名称",
            "message_type": "消息接口类型",
            "reasoning_depth": "推理深度",
            "baseurl": "目标接口地址",
            "apikey": "目标接口密钥",
        }
        self.optimization_field_labels = {
            "model": "改写模型名称",
            "baseurl": "改写接口地址",
            "apikey": "改写接口密钥",
        }

        self._build_ui()
        self._bind_dashboard_traces()
        self._load_config_to_form()
        self._refresh_dashboard()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close_clicked)
        self.root.bind("<Unmap>", self._on_window_unmap)
        self.root.after(120, self._poll_ui_actions)
        self.root.after(800, self._poll_process)
        self.root.after(800, self._poll_log_file)

    def _center_window(self, width: int, height: int) -> None:
        self.root.update_idletasks()
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = max((screen_width - width) // 2, 0)
        y = max((screen_height - height) // 2, 0)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _truncate_runtime_log_file(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
            with open(self.log_path, "w", encoding="utf-8"):
                pass
        except Exception:
            pass

    def _build_ui(self) -> None:
        self.root.grid_columnconfigure(0, weight=0, minsize=296)
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        self.sidebar = ctk.CTkFrame(self.root, width=296, corner_radius=0, fg_color=PALETTE["sidebar"])
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)
        self.sidebar.grid_columnconfigure(0, weight=1)
        self.sidebar.grid_rowconfigure(3, weight=1)

        self.main = ctk.CTkFrame(self.root, corner_radius=0, fg_color=PALETTE["main_bg"])
        self.main.grid(row=0, column=1, sticky="nsew")
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_editor_area()

    def _build_sidebar(self) -> None:
        runtime_card = ctk.CTkFrame(
            self.sidebar,
            fg_color=PALETTE["sidebar_card"],
            corner_radius=24,
            border_width=1,
            border_color=PALETTE["border"],
        )
        runtime_card.grid(row=0, column=0, padx=18, pady=(18, 14), sticky="ew")
        runtime_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            runtime_card,
            text="服务监听",
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="normal"),
        ).grid(row=0, column=0, columnspan=2, padx=16, pady=(14, 8), sticky="w")
        ctk.CTkLabel(runtime_card, text="监听地址", text_color=PALETTE["text_muted"], font=ctk.CTkFont(family=FONT_FAMILY, size=12)).grid(row=1, column=0, padx=16, pady=(6, 8), sticky="w")
        self.host_entry = ctk.CTkEntry(
            runtime_card,
            textvariable=self.host_var,
            height=40,
            fg_color=PALETTE["surface_alt"],
            text_color=PALETTE["text"],
            border_color=PALETTE["border"],
            corner_radius=14,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
        )
        self.host_entry.grid(row=1, column=1, padx=(0, 16), pady=(6, 8), sticky="ew")

        ctk.CTkLabel(runtime_card, text="监听端口", text_color=PALETTE["text_muted"], font=ctk.CTkFont(family=FONT_FAMILY, size=12)).grid(row=2, column=0, padx=16, pady=(0, 16), sticky="w")
        self.port_entry = ctk.CTkEntry(
            runtime_card,
            textvariable=self.port_var,
            height=40,
            fg_color=PALETTE["surface_alt"],
            text_color=PALETTE["text"],
            border_color=PALETTE["border"],
            corner_radius=14,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
        )
        self.port_entry.grid(row=2, column=1, padx=(0, 16), pady=(0, 16), sticky="ew")

        actions_card = ctk.CTkFrame(
            self.sidebar,
            fg_color=PALETTE["sidebar_card"],
            corner_radius=24,
            border_width=1,
            border_color=PALETTE["border"],
        )
        actions_card.grid(row=1, column=0, padx=18, pady=(0, 14), sticky="ew")
        actions_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            actions_card,
            text="快捷操作",
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="normal"),
        ).grid(row=0, column=0, padx=16, pady=(14, 8), sticky="w")

        ctk.CTkButton(
            actions_card,
            text="启动代理",
            height=44,
            corner_radius=16,
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="normal"),
            fg_color=PALETTE["primary"],
            text_color="#ffffff",
            hover_color=PALETTE["primary_hover"],
            command=self.start_server,
        ).grid(row=1, column=0, padx=14, pady=(2, 8), sticky="ew")
        ctk.CTkButton(
            actions_card,
            text="保存配置",
            height=42,
            corner_radius=16,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="normal"),
            fg_color="#dff1e2",
            hover_color="#d1ead6",
            text_color=PALETTE["text"],
            command=self._save_config_from_form,
        ).grid(row=2, column=0, padx=14, pady=8, sticky="ew")
        ctk.CTkButton(
            actions_card,
            text="重新加载配置",
            height=42,
            corner_radius=16,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="normal"),
            fg_color=PALETTE["secondary"],
            hover_color=PALETTE["secondary_hover"],
            text_color=PALETTE["text"],
            command=self._load_config_to_form,
        ).grid(row=3, column=0, padx=14, pady=8, sticky="ew")
        ctk.CTkButton(
            actions_card,
            text="打开程序目录",
            height=42,
            corner_radius=16,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="normal"),
            fg_color=PALETTE["secondary"],
            hover_color=PALETTE["secondary_hover"],
            text_color=PALETTE["text"],
            command=self.open_runtime_dir,
        ).grid(row=4, column=0, padx=14, pady=8, sticky="ew")
        ctk.CTkButton(
            actions_card,
            text="停止代理",
            height=42,
            corner_radius=16,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="normal"),
            fg_color=PALETTE["danger"],
            hover_color=PALETTE["danger_hover"],
            text_color="#ffffff",
            command=self.stop_server,
        ).grid(row=5, column=0, padx=14, pady=(8, 14), sticky="ew")

        tip_card = ctk.CTkFrame(
            self.sidebar,
            fg_color=PALETTE["sidebar_card"],
            corner_radius=24,
            border_width=1,
            border_color=PALETTE["border"],
        )
        tip_card.grid(row=2, column=0, padx=18, pady=(0, 14), sticky="ew")
        ctk.CTkLabel(
            tip_card,
            text="窗口行为",
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="normal"),
        ).grid(row=0, column=0, padx=16, pady=(14, 6), sticky="w")
        ctk.CTkLabel(
            tip_card,
            text="· 点击最小化：隐藏到系统托盘\n· 双击托盘图标：恢复主界面",
            justify="left",
            text_color=PALETTE["text_muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).grid(row=1, column=0, padx=16, pady=(0, 16), sticky="w")

        self.sidebar_status = ctk.CTkLabel(
            self.sidebar,
            textvariable=self.status_var,
            fg_color=PALETTE["chip"],
            text_color=PALETTE["text_muted"],
            corner_radius=22,
            height=54,
            anchor="w",
            padx=16,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="normal"),
        )
        self.sidebar_status.grid(row=4, column=0, padx=18, pady=(0, 22), sticky="ew")

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self.main, fg_color="transparent", corner_radius=0)
        header.grid(row=0, column=0, sticky="ew", padx=26, pady=(24, 12))
        header.grid_columnconfigure(0, weight=1)

        hero = ctk.CTkFrame(
            header,
            fg_color=PALETTE["hero"],
            corner_radius=30,
            border_width=1,
            border_color=PALETTE["border"],
        )
        hero.grid(row=0, column=0, sticky="ew")
        hero.grid_columnconfigure(0, weight=1)
        hero.grid_columnconfigure(1, weight=0)

        ctk.CTkLabel(
            hero,
            text="配置编辑中心",
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=30, weight="normal"),
        ).grid(row=0, column=0, sticky="w", padx=24, pady=(20, 4))

        ctk.CTkLabel(
            hero,
            text="聚焦配置、状态与日志，采用更轻盈的桌面布局与更柔和的浅色视觉层次。",
            text_color=PALETTE["text_muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
        ).grid(row=1, column=0, sticky="w", padx=24, pady=(0, 10))

        ctk.CTkLabel(
            hero,
            textvariable=self.config_status_var,
            text_color=PALETTE["text_muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).grid(row=2, column=0, sticky="w", padx=24, pady=(0, 20))

        badge_wrap = ctk.CTkFrame(hero, fg_color="transparent")
        badge_wrap.grid(row=0, column=1, rowspan=3, padx=(0, 24), pady=20, sticky="e")

        self.header_status_badge = ctk.CTkLabel(
            badge_wrap,
            textvariable=self.status_var,
            fg_color=PALETTE["chip"],
            corner_radius=16,
            text_color=PALETTE["text_muted"],
            padx=16,
            pady=10,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="normal"),
        )
        self.header_status_badge.pack(anchor="e")

        metrics = ctk.CTkFrame(header, fg_color="transparent")
        metrics.grid(row=1, column=0, sticky="ew", pady=(14, 0))
        for index in range(3):
            metrics.grid_columnconfigure(index, weight=1)

        self._make_metric_card(metrics, 0, "监听地址", self.summary_endpoint_var)
        self._make_metric_card(metrics, 1, "目标模型", self.summary_model_var)
        self._make_metric_card(metrics, 2, "消息策略", self.summary_strategy_var)

    def _build_editor_area(self) -> None:
        container = ctk.CTkFrame(
            self.main,
            fg_color="transparent",
            corner_radius=0,
            border_width=0,
        )
        container.grid(row=0, column=0, sticky="nsew", padx=26, pady=24)
        container.grid_columnconfigure(0, weight=1)
        container.grid_rowconfigure(0, weight=1)

        workspace = ctk.CTkFrame(
            container,
            fg_color=PALETTE["surface_soft"],
            corner_radius=30,
            border_width=1,
            border_color=PALETTE["border"],
        )
        workspace.grid(row=0, column=0, sticky="nsew")
        workspace.grid_columnconfigure(0, weight=1)
        workspace.grid_rowconfigure(0, weight=1)

        self.tabs = ctk.CTkTabview(
            workspace,
            fg_color="transparent",
            segmented_button_fg_color=PALETTE["surface_alt"],
            segmented_button_selected_color=PALETTE["primary"],
            segmented_button_selected_hover_color=PALETTE["primary_hover"],
            segmented_button_unselected_color=PALETTE["surface"],
            segmented_button_unselected_hover_color=PALETTE["secondary_hover"],
            corner_radius=28,
            border_width=0,
        )
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=16, pady=16)
        self.tabs.add("目标模型")
        self.tabs.add("改写模型")
        self.tabs.add("响应过滤")
        self.tabs.add("运行日志")
        self.tabs.add("关于")
        self.tabs._segmented_button.configure(
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="normal"),
            height=42,
            text_color=PALETTE["text"],
            selected_color=PALETTE["primary"],
            selected_hover_color=PALETTE["primary_hover"],
            unselected_color=PALETTE["surface"],
            unselected_hover_color=PALETTE["secondary_hover"],
            border_width=0,
        )

        self._build_target_tab(self.tabs.tab("目标模型"))
        self._build_optimization_tab(self.tabs.tab("改写模型"))
        self._build_filter_tab(self.tabs.tab("响应过滤"))
        self._build_log_tab(self.tabs.tab("运行日志"))
        self._build_about_tab(self.tabs.tab("关于"))

    def _set_window_icon(self) -> None:
        ico_path = resolve_resource_path("app_icon.ico")
        png_path = resolve_resource_path("ico.png")

        try:
            if os.path.exists(ico_path):
                self.root.iconbitmap(default=ico_path)
        except Exception:
            pass

        try:
            if os.path.exists(png_path):
                self.window_icon_image = tk.PhotoImage(file=png_path)
                self.root.iconphoto(True, self.window_icon_image)
        except Exception:
            self.window_icon_image = None

    def _load_logo_image(self, size: tuple[int, int]) -> Optional[ctk.CTkImage]:
        if size in self.logo_images:
            return self.logo_images[size]
        logo_path = resolve_resource_path("logo.png")
        if Image is None or not os.path.exists(logo_path):
            return None
        try:
            image = Image.open(logo_path).convert("RGBA")
            fitted = image.copy()
            fitted.thumbnail(size)
            ctk_image = ctk.CTkImage(light_image=fitted, dark_image=fitted, size=fitted.size)
            self.logo_images[size] = ctk_image
            return ctk_image
        except Exception:
            return None

    def _place_logo(self, parent, size: tuple[int, int]) -> None:
        logo_image = self._load_logo_image(size)
        if logo_image is not None:
            ctk.CTkLabel(parent, text="", image=logo_image).grid(row=0, column=0, padx=16, pady=16)
            return
        ctk.CTkLabel(
            parent,
            text="logo.png",
            text_color=PALETTE["text_muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="normal"),
        ).grid(row=0, column=0, padx=16, pady=16)
    def _build_target_tab(self, parent) -> None:
        parent.grid_columnconfigure(0, weight=5)
        parent.grid_columnconfigure(1, weight=3)
        parent.grid_rowconfigure(0, weight=1)

        card = self._make_card(parent, "目标模型配置", "这里配置最终转发到的目标模型接口。")
        card.grid(row=0, column=0, sticky="nsew", padx=(14, 10), pady=14)
        form = ctk.CTkFrame(card, fg_color="transparent")
        form.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        form.grid_columnconfigure(1, weight=1)

        for row, field in enumerate(["model", "message_type", "reasoning_depth", "baseurl", "apikey"]):
            self.target_vars[field] = ctk.StringVar()
            self._add_entry_row(
                form,
                row,
                self.target_field_labels[field],
                self.target_vars[field],
                secret=("key" in field.lower()),
                action_text="测试" if field == "baseurl" else None,
                action_command=self.test_target_interface if field == "baseurl" else None,
            )

        overview = self._make_card(parent, "部署概览", "保留右侧辅助面板，减少大面积留白并提升信息密度。")
        overview.grid(row=0, column=1, sticky="nsew", padx=(10, 14), pady=14)
        body = ctk.CTkFrame(overview, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        body.grid_columnconfigure(0, weight=1)

        self._make_info_chip(body, 0, "当前监听", self.summary_endpoint_var)
        self._make_info_chip(body, 1, "目标模型", self.summary_model_var)
        self._make_info_chip(body, 2, "消息策略", self.summary_strategy_var)
        self._make_info_chip(body, 3, "配置文件", self.summary_config_var)

        ctk.CTkLabel(
            body,
            text="使用建议",
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="normal"),
        ).grid(row=4, column=0, sticky="w", pady=(18, 8))
        ctk.CTkLabel(
            body,
            text="· 目标接口建议使用完整 v1 地址。\n· 密钥为空时适合本地调试或内网代理。\n· 修改完配置后可直接点击左侧“启动代理”。",
            justify="left",
            text_color=PALETTE["text_muted"],
            wraplength=280,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).grid(row=5, column=0, sticky="w")

        quick_actions = ctk.CTkFrame(body, fg_color="transparent")
        quick_actions.grid(row=6, column=0, sticky="ew", pady=(18, 0))
        quick_actions.grid_columnconfigure(0, weight=1)
        quick_actions.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(
            quick_actions,
            text="保存配置",
            height=40,
            corner_radius=14,
            fg_color=PALETTE["secondary"],
            hover_color=PALETTE["secondary_hover"],
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="normal"),
            command=self._save_config_from_form,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(
            quick_actions,
            text="打开目录",
            height=40,
            corner_radius=14,
            fg_color=PALETTE["secondary"],
            hover_color=PALETTE["secondary_hover"],
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="normal"),
            command=self.open_runtime_dir,
        ).grid(row=0, column=1, sticky="ew", padx=(6, 0))

    def _build_optimization_tab(self, parent) -> None:
        parent.grid_columnconfigure(0, weight=3)
        parent.grid_columnconfigure(1, weight=5)
        parent.grid_rowconfigure(0, weight=1)

        access_card = self._make_card(parent, "改写模型接入配置", "目标模型出现拒答后，会调用这里配置的改写模型。")
        access_card.grid(row=0, column=0, sticky="nsew", padx=(14, 10), pady=(14, 10))
        top_form = ctk.CTkFrame(access_card, fg_color="transparent")
        top_form.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        top_form.grid_columnconfigure(1, weight=1)

        for row, field in enumerate(["model", "baseurl", "apikey"]):
            self.optimization_vars[field] = ctk.StringVar()
            self._add_entry_row(
                top_form,
                row,
                self.optimization_field_labels[field],
                self.optimization_vars[field],
                secret=("key" in field.lower()),
                action_text="测试" if field == "baseurl" else None,
                action_command=self.test_optimization_interface if field == "baseurl" else None,
            )

        ctk.CTkLabel(
            top_form,
            text="记录完整改写",
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="normal"),
        ).grid(row=3, column=0, sticky="w", pady=(8, 0))
        ctk.CTkSwitch(
            top_form,
            text="启用（日志中记录改写前完整请求与改写后请求）",
            variable=self.optimization_bool_var,
            onvalue=True,
            offvalue=False,
            progress_color=PALETTE["primary"],
            button_color=PALETTE["accent"],
            button_hover_color="#d1ffb7",
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="normal"),
        ).grid(row=3, column=1, sticky="w", pady=(8, 0))

        ctk.CTkLabel(
            top_form,
            text="仅改写主请求",
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="normal"),
        ).grid(row=4, column=0, sticky="w", pady=(8, 0))
        ctk.CTkSwitch(
            top_form,
            text="启用（辅助请求直接透传）",
            variable=self.only_main_var,
            onvalue=True,
            offvalue=False,
            progress_color=PALETTE["primary"],
            button_color=PALETTE["accent"],
            button_hover_color="#d1ffb7",
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="normal"),
        ).grid(row=4, column=1, sticky="w", pady=(8, 0))

        prompt_card = self._make_card(parent, "改写模型 system_prompt", "这里是改写模型专用提示词，大文本框可直接编辑、复制、清空。")
        prompt_card.grid(row=0, column=1, sticky="nsew", padx=(10, 14), pady=(14, 14))
        prompt_body = ctk.CTkFrame(prompt_card, fg_color="transparent")
        prompt_body.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        prompt_body.grid_columnconfigure(0, weight=1)
        prompt_body.grid_rowconfigure(1, weight=1)

        prompt_header = ctk.CTkFrame(prompt_body, fg_color="transparent")
        prompt_header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        prompt_header.grid_columnconfigure(0, weight=1)

        # ctk.CTkLabel(
        #     prompt_header,
        #     text="system_prompt 编辑区",
        #     text_color=PALETTE["text"],
        #     font=ctk.CTkFont(family=FONT_FAMILY, size=18, weight="normal"),
        # ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            prompt_header,
            text="复制",
            width=72,
            height=34,
            fg_color=PALETTE["secondary"],
            hover_color=PALETTE["secondary_hover"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="normal"),
            command=self.copy_system_prompt,
            text_color=PALETTE["text"],
        ).grid(row=0, column=1, padx=(8, 0))
        ctk.CTkButton(
            prompt_header,
            text="清空",
            width=72,
            height=34,
            fg_color=PALETTE["danger"],
            hover_color=PALETTE["danger_hover"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="normal"),
            command=self.clear_system_prompt,
            text_color="#ffffff",
        ).grid(row=0, column=2, padx=(8, 0))

        self.system_prompt_text = ctk.CTkTextbox(
            prompt_body,
            corner_radius=16,
            fg_color=PALETTE["surface_alt"],
            text_color=PALETTE["text"],
            border_width=1,
            border_color=PALETTE["border"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="normal"),
        )
        self.system_prompt_text.grid(row=1, column=0, sticky="nsew")

    def _build_filter_tab(self, parent) -> None:
        parent.grid_columnconfigure(0, weight=5)
        parent.grid_columnconfigure(1, weight=3)
        parent.grid_rowconfigure(0, weight=1)
        card = self._make_card(parent, "响应关键词过滤", "仅检查最后一条模型回复，命中后丢弃本轮并自动重试。")
        card.grid(row=0, column=0, sticky="nsew", padx=(14, 10), pady=14)
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(body, text="拦截词列表（每行一个）", text_color=PALETTE["text"], font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="normal")).grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.denylist_text = ctk.CTkTextbox(
            body,
            corner_radius=14,
            fg_color=PALETTE["surface_alt"],
            text_color=PALETTE["text"],
            border_width=1,
            border_color=PALETTE["border"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="normal"),
        )
        self.denylist_text.grid(row=1, column=0, sticky="nsew")

        guide_card = self._make_card(parent, "过滤说明", "让拦截规则更清晰，也顺带填补右侧空白区域。")
        guide_card.grid(row=0, column=1, sticky="nsew", padx=(10, 14), pady=14)
        guide_body = ctk.CTkFrame(guide_card, fg_color="transparent")
        guide_body.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        guide_body.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            guide_body,
            text="推荐写法",
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="normal"),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            guide_body,
            text="· 每行一个关键词或短语\n· 只会检查最后一条模型回复\n· 命中后丢弃本轮并自动重试",
            justify="left",
            wraplength=280,
            text_color=PALETTE["text_muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).grid(row=1, column=0, sticky="w", pady=(8, 16))

        example_box = ctk.CTkFrame(
            guide_body,
            fg_color=PALETTE["info"],
            border_width=1,
            border_color=PALETTE["border"],
            corner_radius=18,
        )
        example_box.grid(row=2, column=0, sticky="ew")
        ctk.CTkLabel(
            example_box,
            text="示例\n拒绝回答\n抱歉我不能\n作为 AI 模型",
            justify="left",
            text_color=PALETTE["info_text"],
            wraplength=260,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).pack(anchor="w", padx=16, pady=16)

    def _build_log_tab(self, parent) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)
        card = ctk.CTkFrame(
            parent,
            fg_color=PALETTE["surface"],
            corner_radius=26,
            border_width=1,
            border_color=PALETTE["border"],
        )
        card.pack(fill="both", expand=True, padx=14, pady=14)
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(2, weight=1)

        log_header = ctk.CTkFrame(card, fg_color="transparent")
        log_header.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 8))
        log_header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(log_header, text="运行日志", text_color=PALETTE["text"], font=ctk.CTkFont(family=FONT_FAMILY, size=18, weight="normal")).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(log_header, text="复制日志", width=90, height=34, corner_radius=14, fg_color=PALETTE["secondary"], hover_color=PALETTE["secondary_hover"], text_color=PALETTE["text"], font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="normal"), command=self.copy_logs).grid(row=0, column=1, padx=(8, 0))
        ctk.CTkButton(log_header, text="清空显示", width=90, height=34, corner_radius=14, fg_color=PALETTE["danger"], hover_color=PALETTE["danger_hover"], text_color="#ffffff", font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="normal"), command=self.clear_logs).grid(row=0, column=2, padx=(8, 0))

        ctk.CTkLabel(
            card,
            text="日志会持续读取本地 proxy.log，适合排查启动失败、请求转发与过滤命中情况。",
            text_color=PALETTE["text_muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).grid(row=1, column=0, sticky="w", padx=18, pady=(0, 8))

        self.log_text = ctk.CTkTextbox(
            card,
            corner_radius=16,
            fg_color=PALETTE["log_bg"],
            text_color=PALETTE["log_text"],
            border_width=1,
            border_color=PALETTE["border"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="normal"),
        )
        self.log_text.grid(row=2, column=0, sticky="nsew", padx=18, pady=(0, 18))
        self.log_text.configure(state="disabled")

    def _build_about_tab(self, parent) -> None:
        try:
            parent.configure(fg_color=PALETTE["main_bg"])
        except Exception:
            pass
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)

        center = ctk.CTkFrame(parent, fg_color="transparent")
        center.grid(row=0, column=0, sticky="nsew", padx=14, pady=14)
        center.grid_columnconfigure(0, weight=1)
        center.grid_rowconfigure(0, weight=1)

        logo_wrap = ctk.CTkFrame(
            center,
            fg_color=PALETTE["surface"],
            corner_radius=26,
            border_width=1,
            border_color=PALETTE["border"],
        )
        logo_wrap.grid(row=0, column=0)
        logo_wrap.grid_columnconfigure(0, weight=1)
        logo_wrap.grid_rowconfigure(0, weight=1)
        self._place_logo(logo_wrap, (420, 240))

    def _make_card(self, parent, title: str, desc: str):
        card = ctk.CTkFrame(
            parent,
            fg_color=PALETTE["surface"],
            corner_radius=26,
            border_width=1,
            border_color=PALETTE["border"],
        )
        ctk.CTkLabel(card, text=title, text_color=PALETTE["text"], font=ctk.CTkFont(family=FONT_FAMILY, size=19, weight="normal")).pack(anchor="w", padx=20, pady=(18, 4))
        ctk.CTkLabel(card, text=desc, text_color=PALETTE["text_muted"], font=ctk.CTkFont(family=FONT_FAMILY, size=12)).pack(anchor="w", padx=20, pady=(0, 14))
        return card

    def _make_metric_card(self, parent, column: int, title: str, text_var) -> None:
        card = ctk.CTkFrame(
            parent,
            fg_color=PALETTE["surface"],
            corner_radius=20,
            border_width=1,
            border_color=PALETTE["border"],
        )
        card.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 6, 0 if column == 2 else 6))
        ctk.CTkLabel(
            card,
            text=title,
            text_color=PALETTE["text_muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=11, weight="normal"),
        ).pack(anchor="w", padx=16, pady=(12, 4))
        ctk.CTkLabel(
            card,
            textvariable=text_var,
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="normal"),
        ).pack(anchor="w", padx=16, pady=(0, 14))

    def _make_info_chip(self, parent, row: int, title: str, text_var) -> None:
        card = ctk.CTkFrame(
            parent,
            fg_color=PALETTE["surface_soft"],
            corner_radius=18,
            border_width=1,
            border_color=PALETTE["border"],
        )
        card.grid(row=row, column=0, sticky="ew", pady=5)
        ctk.CTkLabel(
            card,
            text=title,
            text_color=PALETTE["text_muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=11, weight="normal"),
        ).pack(anchor="w", padx=14, pady=(10, 2))
        ctk.CTkLabel(
            card,
            textvariable=text_var,
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="normal"),
        ).pack(anchor="w", padx=14, pady=(0, 10))

    def _add_entry_row(
        self,
        parent,
        row: int,
        label: str,
        var,
        secret: bool = False,
        action_text: Optional[str] = None,
        action_command=None,
    ) -> None:
        ctk.CTkLabel(parent, text=label, text_color=PALETTE["text"], font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="normal")).grid(row=row, column=0, sticky="w", pady=8, padx=(0, 16))
        row_frame = ctk.CTkFrame(parent, fg_color="transparent")
        row_frame.grid(row=row, column=1, sticky="ew", pady=8)
        row_frame.grid_columnconfigure(0, weight=1)

        entry = ctk.CTkEntry(
            row_frame,
            textvariable=var,
            height=40,
            show="*" if secret else "",
            fg_color=PALETTE["surface_alt"],
            text_color=PALETTE["text"],
            border_color=PALETTE["border"],
            corner_radius=14,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
        )
        entry.grid(row=0, column=0, sticky="ew")

        if secret:
            key = f"{parent.winfo_id()}_{row}"
            self.secret_entries[key] = entry
            self.secret_visible[key] = False
            button = ctk.CTkButton(
                row_frame,
                text="显示",
                width=70,
                height=40,
                corner_radius=14,
                fg_color=PALETTE["secondary"],
                hover_color=PALETTE["secondary_hover"],
                text_color=PALETTE["text"],
                font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="normal"),
                command=lambda k=key: self.toggle_secret_visibility(k),
            )
            button.grid(row=0, column=1, padx=(8, 0))
            self.secret_buttons[key] = button
        elif action_text and action_command:
            button = ctk.CTkButton(
                row_frame,
                text=action_text,
                width=70,
                height=40,
                corner_radius=14,
                fg_color=PALETTE["secondary"],
                hover_color=PALETTE["secondary_hover"],
                text_color=PALETTE["text"],
                font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="normal"),
                command=action_command,
            )
            button.grid(row=0, column=1, padx=(8, 0))
            if label == self.target_field_labels.get("baseurl"):
                self.target_test_button = button
            elif label == self.optimization_field_labels.get("baseurl"):
                self.optimization_test_button = button

    def test_target_interface(self) -> None:
        baseurl = self.target_vars.get("baseurl").get().strip() if self.target_vars.get("baseurl") else ""
        apikey = self.target_vars.get("apikey").get().strip() if self.target_vars.get("apikey") else ""
        model = self.target_vars.get("model").get().strip() if self.target_vars.get("model") else ""
        message_type = self.target_vars.get("message_type").get().strip() if self.target_vars.get("message_type") else ""
        reasoning_depth = self.target_vars.get("reasoning_depth").get().strip() if self.target_vars.get("reasoning_depth") else ""

        if not baseurl:
            messagebox.showerror("测试失败", "请先填写目标接口地址")
            return
        if not model:
            messagebox.showerror("测试失败", "请先填写目标模型名称")
            return

        if self.target_test_button is not None:
            self.target_test_button.configure(state="disabled", text="测试中")

        self._append_log(
            f"[界面] 开始测试目标接口：baseurl={baseurl} | model={model} | message_type={message_type or 'responses'}"
        )

        thread = threading.Thread(
            target=self._run_target_interface_test,
            args=(baseurl, apikey, model, message_type, reasoning_depth),
            daemon=True,
        )
        thread.start()

    @staticmethod
    def _extract_test_reply(response_text: str, api_type: str) -> str:
        try:
            data = json.loads(response_text)
        except Exception:
            return ""
        if api_type in {"chat.completions", "chat", "chat_completions"}:
            choices = data.get("choices")
            if isinstance(choices, list) and choices:
                msg = choices[0].get("message", {})
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    return content.strip()[:200]
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and isinstance(item.get("text"), str):
                            return item["text"].strip()[:200]
        else:
            output = data.get("output")
            if isinstance(output, list):
                for item in reversed(output):
                    if isinstance(item, dict) and item.get("role") == "assistant":
                        c = item.get("content")
                        if isinstance(c, list):
                            for sub in c:
                                if isinstance(sub, dict) and isinstance(sub.get("text"), str):
                                    return sub["text"].strip()[:200]
                        elif isinstance(c, str) and c.strip():
                            return c.strip()[:200]
            if isinstance(data.get("output_text"), str) and data["output_text"].strip():
                return data["output_text"].strip()[:200]
        return ""

    def _run_target_interface_test(
        self,
        baseurl: str,
        apikey: str,
        model: str,
        message_type: str,
        reasoning_depth: str,
    ) -> None:
        test_prompt = "请回复：模型测试成功"
        normalized_type = (message_type or "responses").strip().lower()
        base = baseurl.rstrip("/")
        headers = {"Content-Type": "application/json"}
        if apikey:
            headers["Authorization"] = apikey if apikey.lower().startswith("bearer ") else f"Bearer {apikey}"

        if normalized_type in {"chat.completions", "chat", "chat_completions"}:
            url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
            payload: Dict[str, Any] = {
                "model": model,
                "messages": [{"role": "user", "content": test_prompt}],
                "stream": False,
            }
            api_type = "chat"
        else:
            url = base if base.endswith("/responses") else f"{base}/responses"
            payload = {
                "model": model,
                "input": test_prompt,
                "stream": False,
            }
            if reasoning_depth:
                payload["reasoning"] = {"effort": reasoning_depth}
            api_type = "responses"

        try:
            with httpx.Client(timeout=30.0, trust_env=False) as client:
                response = client.post(url, headers=headers, json=payload)
            body_preview = response.text[:500].replace("\r", "\\r").replace("\n", "\\n")
            if 200 <= response.status_code < 300:
                status_code = response.status_code
                reply = self._extract_test_reply(response.text, api_type)
                if reply:
                    success_dialog = f"测试成功\n状态码：{status_code}\n模型回复：{reply}"
                else:
                    success_dialog = f"测试成功（未解析到回复文本）\n状态码：{status_code}\n接口：{url}"
                success_log = f"[界面] 目标接口测试成功 | status={status_code} | url={url} | reply={reply or '无'} | body={body_preview}"
                self.root.after(
                    0,
                    lambda dialog_text=success_dialog, log_text=success_log: self._on_target_test_result(
                        True,
                        dialog_text,
                        log_text,
                    ),
                )
                return

            status_code = response.status_code
            fail_dialog = f"测试失败\n状态码：{status_code}\n接口：{url}\n响应：{body_preview}"
            fail_log = f"[界面] 目标接口测试失败 | status={status_code} | url={url} | body={body_preview}"
            self.root.after(
                0,
                lambda dialog_text=fail_dialog, log_text=fail_log: self._on_target_test_result(
                    False,
                    dialog_text,
                    log_text,
                ),
            )
        except Exception as e:
            err_text = str(e)
            fail_dialog = f"测试失败\n接口：{url}\n错误：{err_text}"
            fail_log = f"[界面] 目标接口测试异常 | url={url} | err={err_text}"
            self.root.after(
                0,
                lambda dialog_text=fail_dialog, log_text=fail_log: self._on_target_test_result(
                    False,
                    dialog_text,
                    log_text,
                ),
            )

    def _on_target_test_result(self, success: bool, dialog_text: str, log_text: str) -> None:
        if self.target_test_button is not None:
            self.target_test_button.configure(state="normal", text="测试")
        self._append_log(log_text)
        if success:
            messagebox.showinfo("目标接口测试", dialog_text)
        else:
            messagebox.showerror("目标接口测试", dialog_text)

    def test_optimization_interface(self) -> None:
        baseurl = self.optimization_vars.get("baseurl").get().strip() if self.optimization_vars.get("baseurl") else ""
        apikey = self.optimization_vars.get("apikey").get().strip() if self.optimization_vars.get("apikey") else ""
        model = self.optimization_vars.get("model").get().strip() if self.optimization_vars.get("model") else ""

        if not baseurl:
            messagebox.showerror("测试失败", "请先填写改写接口地址")
            return
        if not model:
            messagebox.showerror("测试失败", "请先填写改写模型名称")
            return

        if self.optimization_test_button is not None:
            self.optimization_test_button.configure(state="disabled", text="测试中")

        self._append_log(f"[界面] 开始测试改写接口：baseurl={baseurl} | model={model}")

        thread = threading.Thread(
            target=self._run_optimization_interface_test,
            args=(baseurl, apikey, model),
            daemon=True,
        )
        thread.start()

    def _run_optimization_interface_test(self, baseurl: str, apikey: str, model: str) -> None:
        test_prompt = "请回复：模型测试成功"
        base = baseurl.rstrip("/")
        url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if apikey:
            headers["Authorization"] = apikey if apikey.lower().startswith("bearer ") else f"Bearer {apikey}"

        payload: Dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": test_prompt}],
            "stream": False,
        }

        try:
            with httpx.Client(timeout=30.0, trust_env=False) as client:
                response = client.post(url, headers=headers, json=payload)
            body_preview = response.text[:500].replace("\r", "\\r").replace("\n", "\\n")
            if 200 <= response.status_code < 300:
                status_code = response.status_code
                reply = self._extract_test_reply(response.text, "chat")
                if reply:
                    success_dialog = f"测试成功\n状态码：{status_code}\n模型回复：{reply}"
                else:
                    success_dialog = f"测试成功（未解析到回复文本）\n状态码：{status_code}\n接口：{url}"
                success_log = f"[界面] 改写接口测试成功 | status={status_code} | url={url} | reply={reply or '无'} | body={body_preview}"
                self.root.after(
                    0,
                    lambda dialog_text=success_dialog, log_text=success_log: self._on_optimization_test_result(
                        True,
                        dialog_text,
                        log_text,
                    ),
                )
                return

            status_code = response.status_code
            fail_dialog = f"测试失败\n状态码：{status_code}\n接口：{url}\n响应：{body_preview}"
            fail_log = f"[界面] 改写接口测试失败 | status={status_code} | url={url} | body={body_preview}"
            self.root.after(
                0,
                lambda dialog_text=fail_dialog, log_text=fail_log: self._on_optimization_test_result(
                    False,
                    dialog_text,
                    log_text,
                ),
            )
        except Exception as e:
            err_text = str(e)
            fail_dialog = f"测试失败\n接口：{url}\n错误：{err_text}"
            fail_log = f"[界面] 改写接口测试异常 | url={url} | err={err_text}"
            self.root.after(
                0,
                lambda dialog_text=fail_dialog, log_text=fail_log: self._on_optimization_test_result(
                    False,
                    dialog_text,
                    log_text,
                ),
            )

    def _on_optimization_test_result(self, success: bool, dialog_text: str, log_text: str) -> None:
        if self.optimization_test_button is not None:
            self.optimization_test_button.configure(state="normal", text="测试")
        self._append_log(log_text)
        if success:
            messagebox.showinfo("改写接口测试", dialog_text)
        else:
            messagebox.showerror("改写接口测试", dialog_text)

    def _bind_dashboard_traces(self) -> None:
        self.host_var.trace_add("write", lambda *_: self._refresh_dashboard())
        self.port_var.trace_add("write", lambda *_: self._refresh_dashboard())
        for field in ("model", "message_type", "reasoning_depth"):
            if field in self.target_vars:
                self.target_vars[field].trace_add("write", lambda *_: self._refresh_dashboard())

    def _refresh_dashboard(self) -> None:
        host = self.host_var.get().strip() or "127.0.0.1"
        port = self.port_var.get().strip() or "8999"
        model = self.target_vars.get("model").get().strip() if self.target_vars.get("model") else ""
        message_type = self.target_vars.get("message_type").get().strip() if self.target_vars.get("message_type") else ""
        reasoning_depth = self.target_vars.get("reasoning_depth").get().strip() if self.target_vars.get("reasoning_depth") else ""

        self.summary_endpoint_var.set(f"{host}:{port}")
        self.summary_model_var.set(model or "未设置")
        strategy = " / ".join(part for part in [message_type or "responses", reasoning_depth or "默认"] if part)
        self.summary_strategy_var.set(strategy)
        self.summary_config_var.set(os.path.basename(self.config_path) if self.config_path else "config.json")
        self._set_status_appearance(bool(self.process and self.process.poll() is None))

    def _set_status_appearance(self, running: bool) -> None:
        fg_color = PALETTE["accent_soft"] if running else PALETTE["chip"]
        text_color = PALETTE["accent"] if running else PALETTE["text_muted"]
        for badge in (self.sidebar_status, self.header_status_badge):
            if badge is not None:
                badge.configure(fg_color=fg_color, text_color=text_color)

    def _read_json_file(self) -> Dict[str, Any]:
        if not os.path.exists(self.config_path):
            return json.loads(json.dumps(DEFAULT_CONFIG, ensure_ascii=False))
        with open(self.config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return merge_dict(DEFAULT_CONFIG, data if isinstance(data, dict) else {})

    def _load_config_to_form(self) -> None:
        try:
            data = self._read_json_file()
        except Exception as e:
            messagebox.showerror("加载配置失败", str(e))
            self._append_log(f"[界面] 加载配置失败：{e}")
            return

        self.config_path = ensure_local_config_exists(DEFAULT_CONFIG)
        self.config_status_var.set(f"配置文件：{self.config_path}")
        self._set_entry_values(data)
        self._refresh_dashboard()
        self._append_log(f"[界面] 已加载配置：{self.config_path}")

    def _set_entry_values(self, data: Dict[str, Any]) -> None:
        target = data.get("target_model", {})
        for field in self.target_field_labels:
            self.target_vars[field].set(str(target.get(field, "")))

        optimization = data.get("optimization_model", {})
        for field in self.optimization_field_labels:
            self.optimization_vars[field].set(str(optimization.get(field, "")))
        self.optimization_bool_var.set(bool(optimization.get("log_full_refined_content", True)))
        self.only_main_var.set(bool(optimization.get("only_main_user_request", True)))

        self._set_text(self.system_prompt_text, str(optimization.get("system_prompt", "")))

        response_filter = data.get("response_filter", {})
        self._set_text(self.denylist_text, "\n".join(response_filter.get("denylist", [])))

    def _save_config_from_form(self) -> bool:
        try:
            data = self._collect_form_data()
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            self.config_status_var.set(f"配置文件：{self.config_path}")
            self._refresh_dashboard()
            self._append_log(f"[界面] 已保存配置：{self.config_path}")
            return True
        except Exception as e:
            messagebox.showerror("保存配置失败", str(e))
            self._append_log(f"[界面] 保存配置失败：{e}")
            return False

    def _collect_form_data(self) -> Dict[str, Any]:
        port_value = self.port_var.get().strip()
        if port_value and not port_value.isdigit():
            raise ValueError("监听端口必须为数字")

        target = {field: self.target_vars[field].get().strip() for field in self.target_field_labels}
        optimization = {field: self.optimization_vars[field].get().strip() for field in self.optimization_field_labels}
        optimization["log_full_refined_content"] = bool(self.optimization_bool_var.get())
        optimization["only_main_user_request"] = bool(self.only_main_var.get())
        optimization["system_prompt"] = self._get_text(self.system_prompt_text)
        response_filter = {
            "denylist": self._split_lines(self._get_text(self.denylist_text)),
        }

        return {
            "target_model": target,
            "optimization_model": optimization,
            "response_filter": response_filter,
        }

    @staticmethod
    def _split_lines(text: str) -> list[str]:
        return [line.strip() for line in text.splitlines() if line.strip()]

    @staticmethod
    def _set_text(widget, value: str) -> None:
        widget.delete("1.0", "end")
        widget.insert("1.0", value)

    @staticmethod
    def _get_text(widget) -> str:
        return widget.get("1.0", "end").strip()

    def start_server(self) -> None:
        if self.process and self.process.poll() is None:
            self._append_log("[界面] 代理已经在运行")
            return

        if not self._save_config_from_form():
            return

        host = self.host_var.get().strip() or "127.0.0.1"
        port = self.port_var.get().strip() or "8999"
        if not port.isdigit():
            messagebox.showerror("启动失败", "监听端口必须为数字")
            return

        try:
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
            with open(self.log_path, "w", encoding="utf-8"):
                pass
        except Exception as e:
            messagebox.showerror("启动失败", f"清空日志失败：{e}")
            self._append_log(f"[界面] 清空日志失败：{e}")
            return

        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self.log_pos = 0

        cmd = self._build_server_command(host, int(port))
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

        try:
            self.process = subprocess.Popen(
                cmd,
                cwd=get_runtime_base_dir(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except Exception as e:
            messagebox.showerror("启动代理失败", str(e))
            self._append_log(f"[界面] 启动失败：{e}")
            return

        self.status_var.set(f"状态：运行中 {host}:{port}")
        self._refresh_dashboard()
        self._append_log(f"[界面] 已启动代理：{host}:{port}")

    def _build_server_command(self, host: str, port: int) -> list[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable, "--server-mode", "--host", host, "--port", str(port)]
        return [sys.executable, os.path.abspath(__file__), "--server-mode", "--host", host, "--port", str(port)]

    def stop_server(self) -> None:
        if not self.process or self.process.poll() is not None:
            self.status_var.set("状态：未启动")
            self._append_log("[界面] 代理当前未运行")
            return

        self._append_log("[界面] 正在停止代理...")
        try:
            self.process.terminate()
            self.process.wait(timeout=5)
        except Exception:
            try:
                self.process.kill()
            except Exception:
                pass
        finally:
            self.process = None
            self.status_var.set("状态：未启动")
            self._refresh_dashboard()
            self._append_log("[界面] 代理已停止")

    def open_runtime_dir(self) -> None:
        path = get_runtime_base_dir()
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception:
            messagebox.showinfo("程序目录", path)

    def _poll_process(self) -> None:
        if self.process and self.process.poll() is not None:
            code = self.process.returncode
            self.process = None
            self.status_var.set("状态：未启动")
            self._refresh_dashboard()
            self._append_log(f"[界面] 代理已退出：code={code}")
        self.root.after(1000, self._poll_process)

    def _poll_log_file(self) -> None:
        try:
            if os.path.exists(self.log_path):
                size = os.path.getsize(self.log_path)
                if size < self.log_pos:
                    self.log_pos = 0
                if size > self.log_pos:
                    with open(self.log_path, "r", encoding="utf-8", errors="ignore") as f:
                        f.seek(self.log_pos)
                        chunk = f.read()
                        self.log_pos = f.tell()
                    if chunk:
                        self._append_log(chunk.rstrip("\n"), from_file=True)
        except Exception:
            pass
        self.root.after(800, self._poll_log_file)

    def _enqueue_ui_action(self, action: str) -> None:
        self.ui_action_queue.put(action)

    def _poll_ui_actions(self) -> None:
        try:
            while True:
                action = self.ui_action_queue.get_nowait()
                if action == "show":
                    self.show_from_tray()
                elif action == "start":
                    self.start_server()
                elif action == "stop":
                    self.stop_server()
                elif action == "exit":
                    self.request_exit_from_tray()
        except queue.Empty:
            pass
        self.root.after(120, self._poll_ui_actions)

    def _append_log(self, text: str, from_file: bool = False) -> None:
        if not text:
            return
        self.log_text.configure(state="normal")
        if from_file:
            for line in text.splitlines():
                self.log_text.insert("end", line + "\n")
        else:
            self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def toggle_secret_visibility(self, key: str) -> None:
        entry = self.secret_entries.get(key)
        if not entry:
            return
        visible = not self.secret_visible.get(key, False)
        self.secret_visible[key] = visible
        entry.configure(show="" if visible else "*")
        button = self.secret_buttons.get(key)
        if button:
            button.configure(text="隐藏" if visible else "显示")

    def clear_logs(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self._append_log("[界面] 日志显示已清空")

    def copy_logs(self) -> None:
        content = self.log_text.get("1.0", "end").strip()
        if not content:
            self._append_log("[界面] 当前没有可复制的日志")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self._append_log("[界面] 日志已复制到剪贴板")

    def clear_system_prompt(self) -> None:
        self.system_prompt_text.delete("1.0", "end")
        self._append_log("[界面] system_prompt 已清空")

    def copy_system_prompt(self) -> None:
        content = self._get_text(self.system_prompt_text)
        if not content:
            self._append_log("[界面] system_prompt 为空")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self._append_log("[界面] system_prompt 已复制到剪贴板")

    def _on_window_unmap(self, _event) -> None:
        if self.app_closing:
            return
        try:
            if self.root.state() == "iconic" and not self.hidden_to_tray:
                self.hide_to_tray()
        except Exception:
            pass

    def hide_to_tray(self) -> None:
        if os.name != "nt" and (pystray is None or Image is None or ImageDraw is None):
            return
        self.hidden_to_tray = True
        self.root.withdraw()
        self._ensure_tray_icon()
        self._append_log("[界面] 已最小化到系统托盘")

    def show_from_tray(self) -> None:
        self.hidden_to_tray = False
        self.root.after(0, self._restore_window)

    def _restore_window(self) -> None:
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()
        self._stop_tray_icon()
        self._append_log("[界面] 已恢复主窗口")

    def on_close_clicked(self) -> None:
        if self.confirm_exit():
            self.exit_app()

    def confirm_exit(self) -> bool:
        return bool(
            messagebox.askyesno(
                "退出程序",
                "确定要退出 GPT 道德限制优化工具吗？\n\n退出后将停止代理服务，并关闭系统托盘图标。",
                icon="warning",
            )
        )

    def request_exit_from_tray(self) -> None:
        if self.confirm_exit():
            self.exit_app()

    def _ensure_tray_icon(self) -> None:
        if self.tray_icon is not None:
            return
        if os.name == "nt" and win32gui is not None and win32con is not None and win32api is not None:
            self.tray_icon = WindowsTrayIcon(self)
            self.tray_icon.start()
            return
        if pystray is None or Image is None or ImageDraw is None:
            return
        icon_image = self._create_tray_image()
        menu = pystray.Menu(
            pystray.MenuItem("显示主界面", lambda icon, item: self._enqueue_ui_action("show"), default=True),
            pystray.MenuItem("启动代理", lambda icon, item: self._enqueue_ui_action("start")),
            pystray.MenuItem("停止代理", lambda icon, item: self._enqueue_ui_action("stop")),
            pystray.MenuItem("退出程序", lambda icon, item: self._enqueue_ui_action("exit")),
        )
        self.tray_icon = pystray.Icon("gpt54jmp_proxy_gui", icon_image, "GPT5.4 中转代理", menu)
        self.tray_icon.run_detached()

    def _stop_tray_icon(self) -> None:
        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
            self.tray_icon = None

    @staticmethod
    def _create_tray_image():
        icon_path = resolve_resource_path("ico.png")
        if Image is not None and os.path.exists(icon_path):
            try:
                return Image.open(icon_path).convert("RGBA")
            except Exception:
                pass
        image = Image.new("RGBA", (64, 64), PALETTE["main_bg"])
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((8, 8, 56, 56), radius=16, fill=PALETTE["primary"])
        draw.rounded_rectangle((17, 17, 47, 47), radius=10, fill="#ffffff")
        draw.rounded_rectangle((25, 25, 39, 39), radius=6, fill=PALETTE["accent"])
        return image

    def exit_app(self) -> None:
        self.app_closing = True
        self.stop_server()
        self._stop_tray_icon()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def run_server_mode(host: str, port: int, workers: int) -> None:
    from proxy.main import app as proxy_app

    uvicorn.run(
        proxy_app,
        host=host,
        port=port,
        workers=workers,
        reload=False,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GPT5.4 中转代理图形界面")
    parser.add_argument("--server-mode", action="store_true", help="内部参数：以服务模式运行代理")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=8999, help="监听端口，默认 8999")
    parser.add_argument("--workers", type=int, default=1, help="工作进程数量，默认 1")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.server_mode:
        run_server_mode(args.host, args.port, args.workers)
        return
    app = ProxyGuiApp()
    app.run()


if __name__ == "__main__":
    main()
