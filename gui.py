#!/usr/bin/env python3
"""NVIDIA Register — 图形界面：点开始注册，成功账号实时显示。"""

from __future__ import annotations

import asyncio
import csv
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from config import CONFIG_FILE, describe_config, load_config, save_email_provider, save_nonecap_api_key
from main import run as register_run
from records import FIELDNAMES

SCRIPT_DIR = Path(__file__).resolve().parent
NONECAP_DASHBOARD = "https://dashboard.nonecap.com/overview"


class GuiLogWriter:
    """把 print 输出 divert 到队列，供界面刷新。"""

    def __init__(self, q: queue.Queue[str], original):
        self.q = q
        self.original = original

    def write(self, text: str) -> int:
        if text:
            self.q.put(text)
            if self.original is not None:
                try:
                    self.original.write(text)
                except Exception:
                    pass
        return len(text) if text else 0

    def flush(self) -> None:
        if self.original is not None:
            try:
                self.original.flush()
            except Exception:
                pass


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("NVIDIA Build 自动注册")
        self.geometry("1000x740")
        self.minsize(860, 620)

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.stop_flag = threading.Event()
        self.csv_mtime: float | None = None

        try:
            self.config = load_config()
        except SystemExit:
            messagebox.showerror("配置错误", "缺少 config.toml，请先配置后再打开。")
            self.destroy()
            return
        except Exception as exc:
            messagebox.showerror("配置错误", str(exc))
            self.destroy()
            return

        self._build_ui()
        self._refresh_accounts()
        self.after(200, self._drain_logs)
        self.after(1000, self._watch_csv)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill=tk.BOTH, expand=True)

        # —— 顶部控制区 ——
        ctrl = ttk.LabelFrame(root, text="注册控制", padding=10)
        ctrl.pack(fill=tk.X)

        ttk.Label(ctrl, text="注册数量").grid(row=0, column=0, sticky=tk.W)
        self.count_var = tk.StringVar(value="1")
        count_spin = ttk.Spinbox(ctrl, from_=1, to=500, textvariable=self.count_var, width=8)
        count_spin.grid(row=0, column=1, padx=(8, 16), sticky=tk.W)

        self.headless_var = tk.BooleanVar(value=self.config.browser.headless)
        ttk.Checkbutton(
            ctrl,
            text="无头模式（屏外运行，兼容组织页）",
            variable=self.headless_var,
        ).grid(
            row=0, column=2, padx=(0, 16), sticky=tk.W
        )

        self.start_btn = ttk.Button(ctrl, text="开始批量注册", command=self._on_start)
        self.start_btn.grid(row=0, column=3, padx=(0, 8))

        self.stop_btn = ttk.Button(ctrl, text="停止", command=self._on_stop, state=tk.DISABLED)
        self.stop_btn.grid(row=0, column=4, padx=(0, 8))

        ttk.Button(ctrl, text="刷新账号", command=self._refresh_accounts).grid(row=0, column=5)

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(ctrl, textvariable=self.status_var).grid(row=0, column=6, padx=(16, 0), sticky=tk.E)
        ctrl.columnconfigure(6, weight=1)

        row2 = ttk.Frame(ctrl)
        row2.grid(row=1, column=0, columnspan=7, sticky=tk.EW, pady=(10, 0))
        ttk.Label(row2, text="邮箱服务").pack(side=tk.LEFT)
        self.email_provider_var = tk.StringVar(value=self.config.email_provider)
        ttk.Combobox(
            row2,
            textvariable=self.email_provider_var,
            values=("duckmail", "mail.tm"),
            width=12,
            state="readonly",
        ).pack(side=tk.LEFT, padx=(8, 8))
        ttk.Button(row2, text="保存邮箱服务", command=self._save_email_provider).pack(side=tk.LEFT)

        info = ttk.Frame(ctrl)
        info.grid(row=2, column=0, columnspan=7, sticky=tk.EW, pady=(8, 0))
        self.info_var = tk.StringVar(value=self._info_text())
        ttk.Label(info, textvariable=self.info_var, foreground="#444").pack(anchor=tk.W)

        # —— NoneCap Key ——
        key_frame = ttk.LabelFrame(root, text="NoneCap 打码 Key（dashboard.nonecap.com）", padding=10)
        key_frame.pack(fill=tk.X, pady=(10, 0))

        ttk.Label(key_frame, text="API Key").grid(row=0, column=0, sticky=tk.W)
        current_key = self.config.captcha.nonecap_api_key or ""
        self.nonecap_var = tk.StringVar(value=current_key)
        self.nonecap_entry = ttk.Entry(key_frame, textvariable=self.nonecap_var, width=56, show="*")
        self.nonecap_entry.grid(row=0, column=1, padx=8, sticky=tk.EW)
        key_frame.columnconfigure(1, weight=1)

        self.show_key_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            key_frame,
            text="显示",
            variable=self.show_key_var,
            command=self._toggle_key_visibility,
        ).grid(row=0, column=2, padx=(0, 6))

        ttk.Button(key_frame, text="保存 Key", command=self._save_nonecap_key).grid(
            row=0, column=3, padx=(0, 6)
        )
        ttk.Button(key_frame, text="打开控制台", command=self._open_nonecap_dashboard).grid(
            row=0, column=4
        )

        hint = (
            "从 Dashboard 复制新的 nc_live_... 粘贴到这里 → 保存 Key。"
            "下方列表是注册成功的 NVIDIA 账号（邮箱/密码/nvapi Key）。"
        )
        ttk.Label(key_frame, text=hint, foreground="#555").grid(
            row=1, column=0, columnspan=5, sticky=tk.W, pady=(8, 0)
        )

        # —— 账号列表 ——
        accounts_frame = ttk.LabelFrame(
            root,
            text="已注册 NVIDIA 账号（邮箱 + 密码 + nvapi Key）",
            padding=8,
        )
        accounts_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        cols = ("email", "password", "apikey")
        self.tree = ttk.Treeview(accounts_frame, columns=cols, show="headings", height=10)
        self.tree.heading("email", text="NVIDIA 邮箱")
        self.tree.heading("password", text="NVIDIA 密码")
        self.tree.heading("apikey", text="NVIDIA API Key (nvapi-...)")
        self.tree.column("email", width=220, anchor=tk.W)
        self.tree.column("password", width=120, anchor=tk.W)
        self.tree.column("apikey", width=480, anchor=tk.W)

        yscroll = ttk.Scrollbar(accounts_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)

        action_bar = ttk.Frame(root)
        action_bar.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(action_bar, text="复制选中邮箱", command=lambda: self._copy_col("email")).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(action_bar, text="复制选中密码", command=lambda: self._copy_col("password")).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(action_bar, text="复制选中 API Key", command=lambda: self._copy_col("apikey")).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(action_bar, text="打开 CSV", command=self._open_csv).pack(side=tk.LEFT)
        self.count_label = ttk.Label(action_bar, text="共 0 个账号")
        self.count_label.pack(side=tk.RIGHT)

        # —— 日志 ——
        log_frame = ttk.LabelFrame(root, text="运行日志", padding=8)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        self.log_text = tk.Text(log_frame, height=12, wrap=tk.WORD, font=("Consolas", 9))
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _toggle_key_visibility(self) -> None:
        self.nonecap_entry.configure(show="" if self.show_key_var.get() else "*")

    def _info_text(self) -> str:
        if self.config.email_provider == "mail.tm":
            domain = self.config.mail_tm.domain or "auto"
        elif self.config.email_provider == "cloudflare_temp_email":
            domain = self.config.cloudflare_temp_email.domain
        else:
            domain = self.config.duckmail.domain
        return (
            f"邮箱: {self.config.email_provider} / {domain}  ·  "
            f"打码: {self.config.captcha.mode}  ·  "
            f"浏览器: {self.config.browser.channel or 'chromium'}  ·  "
            f"输出: {self.config.nvidia.output_csv.name}"
        )

    def _save_email_provider(self) -> None:
        provider = self.email_provider_var.get().strip()
        try:
            save_email_provider(provider)
            self.config = load_config()
            self.email_provider_var.set(self.config.email_provider)
            self.info_var.set(self._info_text())
            self.status_var.set(f"邮箱服务已切换为 {self.config.email_provider}")
            self._append_log(f"已更新 email_provider={self.config.email_provider}\n")
            messagebox.showinfo("成功", f"已保存邮箱服务: {self.config.email_provider}")
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))

    def _save_nonecap_key(self) -> None:
        key = self.nonecap_var.get().strip()
        if not key:
            messagebox.showerror("错误", "请先粘贴 NoneCap API Key")
            return
        try:
            save_nonecap_api_key(key)
            self.config = load_config()
            self.status_var.set("NoneCap Key 已保存")
            self._append_log(f"已更新 NoneCap Key → {CONFIG_FILE.name}\n")
            messagebox.showinfo("成功", "NoneCap API Key 已写入 config.toml")
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))

    def _open_nonecap_dashboard(self) -> None:
        import webbrowser

        webbrowser.open(NONECAP_DASHBOARD)

    def _append_log(self, text: str) -> None:
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)

    def _drain_logs(self) -> None:
        try:
            while True:
                self._append_log(self.log_queue.get_nowait())
        except queue.Empty:
            pass
        self.after(200, self._drain_logs)

    def _watch_csv(self) -> None:
        path = self.config.nvidia.output_csv
        try:
            mtime = path.stat().st_mtime if path.exists() else None
        except OSError:
            mtime = None
        if mtime != self.csv_mtime:
            self.csv_mtime = mtime
            self._refresh_accounts()
        self.after(1000, self._watch_csv)

    def _refresh_accounts(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        path = self.config.nvidia.output_csv
        rows: list[dict[str, str]] = []
        if path.exists():
            try:
                with path.open(newline="", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        if not row.get("email"):
                            continue
                        rows.append(
                            {
                                "email": row.get("email", ""),
                                "password": row.get("password", ""),
                                "apikey": row.get("apikey", ""),
                            }
                        )
            except Exception as exc:
                self._append_log(f"读取 accounts.csv 失败: {exc}\n")
        for row in rows:
            self.tree.insert("", tk.END, values=(row["email"], row["password"], row["apikey"]))
        self.count_label.configure(text=f"共 {len(rows)} 个账号")

    def _copy_col(self, col: str) -> None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选中一行账号")
            return
        idx = FIELDNAMES.index(col)
        values = self.tree.item(sel[0], "values")
        if not values or idx >= len(values):
            return
        self.clipboard_clear()
        self.clipboard_append(values[idx])
        self.status_var.set(f"已复制 {col}")

    def _open_csv(self) -> None:
        path = self.config.nvidia.output_csv
        if not path.exists():
            messagebox.showinfo("提示", "还没有 accounts.csv")
            return
        try:
            import os

            os.startfile(path)  # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))

    def _on_start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        try:
            count = int(self.count_var.get().strip())
        except ValueError:
            messagebox.showerror("错误", "注册数量必须是数字")
            return
        if count < 1:
            messagebox.showerror("错误", "注册数量必须 >= 1")
            return

        # 重新加载配置，并覆盖 headless
        try:
            cfg = load_config()
        except Exception as exc:
            messagebox.showerror("配置错误", str(exc))
            return

        # frozen dataclass — 用 object.__setattr__ 或重建 BrowserConfig
        from dataclasses import replace

        cfg = replace(cfg, browser=replace(cfg.browser, headless=self.headless_var.get()))
        self.config = cfg

        self.stop_flag.clear()
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self.status_var.set(f"正在注册 {count} 个账号...")
        self._append_log(f"\n{'=' * 50}\n开始注册 x{count}  headless={cfg.browser.headless}\n")

        self.worker = threading.Thread(target=self._run_worker, args=(cfg, count), daemon=True)
        self.worker.start()

    def _on_stop(self) -> None:
        # main.py 用全局 _shutdown；这里设置后当前账号结束后停
        import main as main_mod

        main_mod._shutdown = True
        self.stop_flag.set()
        self.status_var.set("正在停止（等当前账号完成）...")
        self._append_log("\n已请求停止，等待当前账号完成...\n")

    def _run_worker(self, cfg, count: int) -> None:
        import main as main_mod

        main_mod._shutdown = False
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = GuiLogWriter(self.log_queue, old_out)
        sys.stderr = GuiLogWriter(self.log_queue, old_err)
        try:
            describe_config(cfg)
            asyncio.run(register_run(cfg, count))
            self.log_queue.put("\n全部任务结束。\n")
            self.after(0, lambda: self.status_var.set("完成"))
        except Exception as exc:
            self.log_queue.put(f"\n运行失败: {exc}\n")
            self.after(0, lambda: self.status_var.set("失败"))
        finally:
            sys.stdout = old_out
            sys.stderr = old_err
            self.after(0, self._on_worker_done)

    def _on_worker_done(self) -> None:
        self.start_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)
        self._refresh_accounts()
        if self.status_var.get() not in {"完成", "失败"}:
            self.status_var.set("就绪")


def main() -> None:
    app = App()
    if app.winfo_exists():
        app.mainloop()


if __name__ == "__main__":
    main()
