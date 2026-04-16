import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable
from app.tray.api_client import BlitztextClient


BG    = "#1c1c1e"
FG    = "#ffffff"
CARD  = "#2c2c2e"
BLUE  = "#3b82f6"
MUTED = "#8e8e93"
FONT  = "DejaVu Sans"


class SettingsWindow:
    def __init__(self, client: BlitztextClient, on_close: Callable | None = None):
        self.client = client
        self.on_close = on_close
        self._win: tk.Tk | None = None

    def show(self) -> None:
        if self._win and self._win.winfo_exists():
            self._win.lift()
            return
        self._build()

    def _build(self) -> None:
        win = tk.Tk()
        self._win = win
        win.title("Blitztext Einstellungen")
        win.geometry("380x440")
        win.configure(bg=BG)
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", self._close)

        style = ttk.Style(win)
        style.theme_use("clam")
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=CARD, foreground=FG,
                        padding=[16, 6], font=(FONT, 11, "bold"))
        style.map("TNotebook.Tab", background=[("selected", BLUE)],
                  foreground=[("selected", "white")])
        style.configure("TCombobox", fieldbackground=CARD, background=CARD,
                        foreground=FG, selectbackground=BLUE)

        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=12, pady=12)

        tab_anpassen = tk.Frame(nb, bg=BG)
        tab_zugang   = tk.Frame(nb, bg=BG)
        nb.add(tab_anpassen, text="  Anpassen  ")
        nb.add(tab_zugang,   text="  Zugang  ")

        try:
            cfg = self.client.get_config()
        except Exception:
            cfg = {}

        self._build_anpassen(tab_anpassen, cfg)
        self._build_zugang(tab_zugang, cfg)

        win.mainloop()

    # ── Tab: Anpassen ────────────────────────────────────────────────────

    def _build_anpassen(self, frame: tk.Frame, cfg: dict) -> None:
        # Modus
        tk.Label(frame, text="MODUS", bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(anchor="w", padx=16, pady=(12, 4))

        mode_frame = tk.Frame(frame, bg=CARD, padx=4, pady=4)
        mode_frame.pack(fill="x", padx=16, pady=2)

        self._trigger_var = tk.StringVar(value=cfg.get("trigger_mode", "hold"))

        self._btn_hold = tk.Button(
            mode_frame, text="Halten",
            command=lambda: self._set_trigger("hold"),
            relief="flat", bd=0, padx=20, pady=7,
            font=(FONT, 11),
        )
        self._btn_press = tk.Button(
            mode_frame, text="Drücken",
            command=lambda: self._set_trigger("toggle"),
            relief="flat", bd=0, padx=20, pady=7,
            font=(FONT, 11),
        )
        self._btn_hold.pack(side="left", padx=2)
        self._btn_press.pack(side="left", padx=2)
        self._refresh_trigger_buttons()

        # Tastenkürzel
        tk.Label(frame, text="TASTENKÜRZEL", bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(anchor="w", padx=16, pady=(18, 4))

        modes = cfg.get("modes", {})
        entries = [
            ("normal", "F13", "Blitztext"),
            ("plus",   "F14", "Blitztext+"),
            ("rage",   "F15", "Blitztext $%&!"),
            ("emoji",  "F16", "Blitztext 😊"),
        ]
        for mode_key, default_key, label in entries:
            key_name = (modes.get(mode_key) or {}).get("key_name", f"KEY_{default_key}")
            # Display as "F13" not "KEY_F13"
            display = key_name.replace("KEY_", "") if key_name.startswith("KEY_") else key_name

            row = tk.Frame(frame, bg=CARD)
            row.pack(fill="x", padx=16, pady=1)
            tk.Label(row, text=display, bg=CARD, fg=MUTED,
                     font=(FONT, 10), width=7, anchor="w").pack(side="left", padx=10, pady=6)
            tk.Label(row, text=label, bg=CARD, fg=FG,
                     font=(FONT, 11)).pack(side="left")

        # Speichern
        tk.Button(
            frame, text="Speichern", bg=BLUE, fg="white",
            font=(FONT, 11, "bold"),
            relief="flat", bd=0, padx=20, pady=8,
            command=self._save_anpassen,
            activebackground="#2563eb", activeforeground="white",
        ).pack(pady=16)

    def _set_trigger(self, mode: str) -> None:
        self._trigger_var.set(mode)
        self._refresh_trigger_buttons()

    def _refresh_trigger_buttons(self) -> None:
        is_hold = self._trigger_var.get() == "hold"
        self._btn_hold.configure(
            bg=BLUE if is_hold else CARD,
            fg="white" if is_hold else MUTED,
        )
        self._btn_press.configure(
            bg=BLUE if not is_hold else CARD,
            fg="white" if not is_hold else MUTED,
        )

    def _save_anpassen(self) -> None:
        try:
            self.client.patch_config({"trigger_mode": self._trigger_var.get()})
            messagebox.showinfo("Gespeichert", "Einstellungen gespeichert.")
        except Exception as e:
            messagebox.showerror("Fehler", str(e))

    # ── Tab: Zugang ──────────────────────────────────────────────────────

    def _build_zugang(self, frame: tk.Frame, cfg: dict) -> None:
        # API Key
        tk.Label(frame, text="OPENAI API-KEY", bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(anchor="w", padx=16, pady=(12, 4))

        self._api_key_var = tk.StringVar(value=cfg.get("openai_api_key", ""))
        tk.Entry(
            frame, textvariable=self._api_key_var, show="•",
            bg=CARD, fg=FG, insertbackground=FG,
            font=(FONT, 11), relief="flat", bd=0,
        ).pack(fill="x", padx=16, ipady=7)

        # Input Device
        tk.Label(frame, text="EINGABEGERÄT", bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(anchor="w", padx=16, pady=(16, 4))

        devices = self.client.list_input_devices()
        self._device_paths: list[str] = [p for p, _ in devices]
        device_labels: list[str] = [f"{p}  —  {n}" for p, n in devices]

        current_device = cfg.get("input_device", "")
        if current_device and current_device not in self._device_paths:
            self._device_paths.insert(0, current_device)
            device_labels.insert(0, f"{current_device}  —  (aktuell)")

        self._device_var = tk.StringVar()
        self._device_combo = ttk.Combobox(
            frame, textvariable=self._device_var,
            values=device_labels, state="readonly",
            font=(FONT, 10),
        )
        if current_device in self._device_paths:
            idx = self._device_paths.index(current_device)
            self._device_combo.current(idx)
        elif device_labels:
            self._device_combo.current(0)
        self._device_combo.pack(fill="x", padx=16)

        # Hinweis
        tk.Label(
            frame,
            text=("HINWEIS\nFür direktes Einfügen: Blitztext einmal in Programme legen "
                  "und dann Mikrofon sowie Bedienungshilfen erlauben."),
            bg=BG, fg=MUTED, wraplength=320, justify="left",
            font=(FONT, 9),
        ).pack(anchor="w", padx=16, pady=14)

        # Speichern
        tk.Button(
            frame, text="Speichern", bg=BLUE, fg="white",
            font=(FONT, 11, "bold"),
            relief="flat", bd=0, padx=20, pady=8,
            command=self._save_zugang,
            activebackground="#2563eb", activeforeground="white",
        ).pack(pady=8)

    def _save_zugang(self) -> None:
        updates: dict = {}

        key = self._api_key_var.get().strip()
        if key:
            updates["openai_api_key"] = key

        idx = self._device_combo.current()
        if 0 <= idx < len(self._device_paths):
            updates["input_device"] = self._device_paths[idx]

        if not updates:
            return
        try:
            self.client.patch_config(updates)
            messagebox.showinfo("Gespeichert", "Einstellungen gespeichert.")
        except Exception as e:
            messagebox.showerror("Fehler", str(e))

    def _close(self) -> None:
        if self._win:
            self._win.destroy()
            self._win = None
        if self.on_close:
            self.on_close()
