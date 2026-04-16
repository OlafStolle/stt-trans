import glob
import select
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable

import evdev
import evdev.ecodes

from app.tray.api_client import BlitztextClient


BG    = "#1c1c1e"
FG    = "#ffffff"
CARD  = "#2c2c2e"
BLUE  = "#3b82f6"
MUTED = "#8e8e93"
GREEN = "#22c55e"
FONT  = "DejaVu Sans"

MODES = [
    ("normal", "Blitztext"),
    ("plus",   "Blitztext+"),
    ("rage",   "Blitztext $%&!"),
    ("emoji",  "Blitztext 😊"),
]


def _listen_for_key(timeout: float = 8.0) -> dict | None:
    """Blockierend: Wartet auf ersten Tastendruck auf beliebigem evdev-Gerät."""
    devices: list[evdev.InputDevice] = []
    for path in sorted(glob.glob("/dev/input/event*")):
        try:
            devices.append(evdev.InputDevice(path))
        except Exception:
            pass

    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            r, _, _ = select.select(devices, [], [], min(remaining, 0.3))
            for dev in r:
                try:
                    for event in dev.read():
                        if event.type == evdev.ecodes.EV_KEY and event.value == 1:
                            raw = evdev.ecodes.KEY.get(event.code, f"KEY_{event.code}")
                            key_name = raw[0] if isinstance(raw, list) else raw
                            return {
                                "key_code": event.code,
                                "key_name": key_name,
                                "device_path": dev.path,
                                "device_name": dev.name,
                            }
                except Exception:
                    pass
    finally:
        for d in devices:
            try:
                d.close()
            except Exception:
                pass
    return None


class SettingsWindow:
    def __init__(self, client: BlitztextClient, on_close: Callable | None = None):
        self.client = client
        self.on_close = on_close
        self._win: tk.Tk | None = None
        # Speichert erkannte Tasten pro Modus: {mode_key: {key_code, key_name, device_path}}
        self._detected: dict[str, dict] = {}
        self._listening: bool = False

    def show(self) -> None:
        if self._win and self._win.winfo_exists():
            self._win.lift()
            return
        self._build()

    def _build(self) -> None:
        win = tk.Tk()
        self._win = win
        win.title("Blitztext Einstellungen")
        win.geometry("420x480")
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
        modes_cfg = cfg.get("modes", {})

        # Trigger-Modus
        tk.Label(frame, text="MODUS", bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(anchor="w", padx=16, pady=(12, 4))

        mode_frame = tk.Frame(frame, bg=CARD, padx=4, pady=4)
        mode_frame.pack(fill="x", padx=16, pady=2)

        self._trigger_var = tk.StringVar(value=cfg.get("trigger_mode", "hold"))
        self._btn_hold = tk.Button(
            mode_frame, text="Halten",
            command=lambda: self._set_trigger("hold"),
            relief="flat", bd=0, padx=20, pady=7, font=(FONT, 11),
        )
        self._btn_press = tk.Button(
            mode_frame, text="Drücken",
            command=lambda: self._set_trigger("toggle"),
            relief="flat", bd=0, padx=20, pady=7, font=(FONT, 11),
        )
        self._btn_hold.pack(side="left", padx=2)
        self._btn_press.pack(side="left", padx=2)
        self._refresh_trigger_buttons()

        # Tastenkürzel mit "Taste erkennen"
        tk.Label(frame, text="TASTENKÜRZEL", bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(anchor="w", padx=16, pady=(18, 4))

        self._key_labels: dict[str, tk.Label] = {}
        self._detect_btns: dict[str, tk.Button] = {}

        for mode_key, mode_label in MODES:
            m = modes_cfg.get(mode_key) or {}
            key_name    = m.get("key_name", "")
            device_name = ""  # Gerätename nicht in Config gespeichert

            row = tk.Frame(frame, bg=CARD)
            row.pack(fill="x", padx=16, pady=2)

            # Modus-Name
            tk.Label(row, text=mode_label, bg=CARD, fg=FG,
                     font=(FONT, 10), width=14, anchor="w").pack(side="left", padx=10, pady=7)

            # Erkannte Taste anzeigen
            display = _format_key_display(key_name, device_name)
            lbl = tk.Label(row, text=display, bg=CARD, fg=BLUE if display else MUTED,
                           font=(FONT, 9), anchor="w", width=18)
            lbl.pack(side="left")
            self._key_labels[mode_key] = lbl

            # "Taste erkennen"-Button
            btn = tk.Button(
                row, text="⌨ Taste erkennen",
                command=lambda mk=mode_key: self._start_listen(mk),
                relief="flat", bd=0, padx=10, pady=5,
                bg=CARD, fg=MUTED, font=(FONT, 9),
                activebackground=BLUE, activeforeground="white",
            )
            btn.pack(side="right", padx=6)
            self._detect_btns[mode_key] = btn

        # Speichern
        tk.Button(
            frame, text="Speichern", bg=BLUE, fg="white",
            font=(FONT, 11, "bold"),
            relief="flat", bd=0, padx=20, pady=8,
            command=self._save_anpassen,
            activebackground="#2563eb", activeforeground="white",
        ).pack(pady=14)

    def _set_trigger(self, mode: str) -> None:
        self._trigger_var.set(mode)
        self._refresh_trigger_buttons()

    def _refresh_trigger_buttons(self) -> None:
        is_hold = self._trigger_var.get() == "hold"
        self._btn_hold.configure(
            bg=BLUE if is_hold else CARD, fg="white" if is_hold else MUTED)
        self._btn_press.configure(
            bg=BLUE if not is_hold else CARD, fg="white" if not is_hold else MUTED)

    def _start_listen(self, mode_key: str) -> None:
        if self._listening:
            return
        self._listening = True

        # Alle Buttons deaktivieren, aktiven hervorheben
        for mk, btn in self._detect_btns.items():
            if mk == mode_key:
                btn.configure(text="Drücken...", bg=BLUE, fg="white")
            else:
                btn.configure(state="disabled")
        self._key_labels[mode_key].configure(text="", fg=MUTED)

        def _worker() -> None:
            result = _listen_for_key(timeout=8.0)
            if self._win:
                self._win.after(0, lambda: self._on_key_detected(mode_key, result))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_key_detected(self, mode_key: str, result: dict | None) -> None:
        self._listening = False

        # Buttons wieder aktivieren
        for btn in self._detect_btns.values():
            btn.configure(state="normal", bg=CARD, fg=MUTED, text="⌨ Taste erkennen")

        if result is None:
            self._key_labels[mode_key].configure(text="Timeout", fg=MUTED)
            return

        self._detected[mode_key] = result
        display = _format_key_display(result["key_name"], result["device_name"])
        self._key_labels[mode_key].configure(text=display, fg=GREEN)

    def _save_anpassen(self) -> None:
        updates: dict = {"trigger_mode": self._trigger_var.get()}

        if self._detected:
            # Hole aktuelle modes-Config
            try:
                cfg = self.client.get_config()
                modes = cfg.get("modes", {})
            except Exception:
                modes = {}

            for mode_key, detected in self._detected.items():
                m = dict(modes.get(mode_key) or {})
                m["key_code"] = detected["key_code"]
                m["key_name"] = detected["key_name"]
                modes[mode_key] = m

            # Gerät aus erstem erkannten Modus setzen (alle teilen dasselbe Gerät)
            first = next(iter(self._detected.values()))
            updates["input_device"] = first["device_path"]
            updates["modes"] = modes

        try:
            self.client.patch_config(updates)
            messagebox.showinfo("Gespeichert", "Einstellungen gespeichert.")
        except Exception as e:
            messagebox.showerror("Fehler", str(e))

    # ── Tab: Zugang ──────────────────────────────────────────────────────

    def _build_zugang(self, frame: tk.Frame, cfg: dict) -> None:
        tk.Label(frame, text="OPENAI API-KEY", bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(anchor="w", padx=16, pady=(12, 4))

        self._api_key_var = tk.StringVar(value=cfg.get("openai_api_key", ""))
        tk.Entry(
            frame, textvariable=self._api_key_var, show="•",
            bg=CARD, fg=FG, insertbackground=FG,
            font=(FONT, 11), relief="flat", bd=0,
        ).pack(fill="x", padx=16, ipady=7)

        tk.Label(
            frame,
            text=("HINWEIS\nFür direktes Einfügen: Blitztext einmal in Programme legen "
                  "und dann Mikrofon sowie Bedienungshilfen erlauben."),
            bg=BG, fg=MUTED, wraplength=340, justify="left", font=(FONT, 9),
        ).pack(anchor="w", padx=16, pady=16)

        tk.Button(
            frame, text="Speichern", bg=BLUE, fg="white",
            font=(FONT, 11, "bold"),
            relief="flat", bd=0, padx=20, pady=8,
            command=self._save_zugang,
            activebackground="#2563eb", activeforeground="white",
        ).pack(pady=8)

    def _save_zugang(self) -> None:
        key = self._api_key_var.get().strip()
        if not key:
            return
        try:
            self.client.patch_config({"openai_api_key": key})
            messagebox.showinfo("Gespeichert", "API-Key gespeichert.")
        except Exception as e:
            messagebox.showerror("Fehler", str(e))

    def _close(self) -> None:
        if self._win:
            self._win.destroy()
            self._win = None
        if self.on_close:
            self.on_close()


def _format_key_display(key_name: str, device_name: str) -> str:
    if not key_name:
        return ""
    short_device = device_name[:16] if device_name else ""
    if short_device:
        return f"{key_name} ({short_device})"
    return key_name
