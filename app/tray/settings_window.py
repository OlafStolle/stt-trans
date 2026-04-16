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
from app.transcribe import local_available


BG    = "#1c1c1e"
FG    = "#ffffff"
CARD  = "#2c2c2e"
BLUE  = "#3b82f6"
MUTED = "#8e8e93"
GREEN = "#22c55e"
FONT  = "DejaVu Sans"

MODES = [
    ("normal", "stt-trans"),
    ("plus",   "stt-trans+"),
    ("rage",   "stt-trans $%&!"),
    ("emoji",  "stt-trans 😊"),
]


def _listen_for_key(timeout: float = 8.0) -> dict | None:
    """Blockierend: Wartet auf Tastenkombination (alle Keys halten, dann loslassen)."""
    devices: list[evdev.InputDevice] = []
    for path in sorted(glob.glob("/dev/input/event*")):
        try:
            devices.append(evdev.InputDevice(path))
        except Exception:
            pass

    pressed: dict[int, str] = {}  # key_code -> key_name
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            r, _, _ = select.select(devices, [], [], min(remaining, 0.3))
            for dev in r:
                try:
                    for event in dev.read():
                        if event.type != evdev.ecodes.EV_KEY:
                            continue
                        raw = evdev.ecodes.KEY.get(event.code, f"KEY_{event.code}")
                        key_name = raw[0] if isinstance(raw, list) else raw
                        if event.value == 1:
                            pressed[event.code] = key_name
                        elif event.value == 0 and pressed:
                            # Erste Taste losgelassen → Combo komplett
                            key_codes = list(pressed.keys())
                            key_names = list(pressed.values())
                            combo_name = " + ".join(key_names)
                            return {
                                "key_codes": key_codes,
                                "key_name": combo_name,
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
        self._cleared: set[str] = set()
        self._listening: bool = False
        # Transcription backend toggle
        self._backend_var: tk.StringVar | None = None
        self._model_var: tk.StringVar | None = None
        self._backend_btns: dict[str, tk.Button] = {}
        self._model_combo: ttk.Combobox | None = None
        self._backend_warn_lbl: tk.Label | None = None

    def show(self) -> None:
        if self._win and self._win.winfo_exists():
            self._win.lift()
            return
        self._build()

    def _build(self) -> None:
        win = tk.Tk()
        self._win = win
        win.title("stt-trans Einstellungen")
        win.geometry("620x540")
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
        self._cfg = cfg  # Cache — Daemon könnte später gestoppt sein

        self._build_anpassen(tab_anpassen, cfg)
        self._build_zugang(tab_zugang, cfg)

        win.mainloop()

    # ── Tab: Anpassen ────────────────────────────────────────────────────

    def _build_anpassen(self, frame: tk.Frame, cfg: dict) -> None:
        modes_cfg = cfg.get("modes", {})

        tk.Label(frame, text="TASTENKÜRZEL", bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(anchor="w", padx=16, pady=(12, 4))

        self._key_labels: dict[str, tk.Label] = {}
        self._detect_btns: dict[str, tk.Button] = {}
        self._trigger_vars: dict[str, tk.StringVar] = {}
        self._trigger_btns: dict[str, dict] = {}

        for mode_key, mode_label in MODES:
            m = modes_cfg.get(mode_key) or {}
            key_name = m.get("key_name", "")
            trigger  = m.get("trigger_mode", "hold")

            row = tk.Frame(frame, bg=CARD)
            row.pack(fill="x", padx=16, pady=2)

            # Modus-Name
            tk.Label(row, text=mode_label, bg=CARD, fg=FG,
                     font=(FONT, 10), width=12, anchor="w").pack(side="left", padx=8, pady=6)

            # Hold/Toggle pro Modus
            tvar = tk.StringVar(value=trigger)
            self._trigger_vars[mode_key] = tvar
            btn_hold = tk.Button(
                row, text="Halten",
                command=lambda mk=mode_key: self._set_mode_trigger(mk, "hold"),
                relief="flat", bd=0, padx=8, pady=4, font=(FONT, 8),
            )
            btn_toggle = tk.Button(
                row, text="Drücken",
                command=lambda mk=mode_key: self._set_mode_trigger(mk, "toggle"),
                relief="flat", bd=0, padx=8, pady=4, font=(FONT, 8),
            )
            btn_hold.pack(side="left", padx=1)
            btn_toggle.pack(side="left", padx=1)
            self._trigger_btns[mode_key] = {"hold": btn_hold, "toggle": btn_toggle}
            self._refresh_mode_trigger_buttons(mode_key)

            # Erkannte Taste/Combo anzeigen
            lbl = tk.Label(row, text=key_name, bg=CARD, fg=BLUE if key_name else MUTED,
                           font=(FONT, 9), anchor="w", width=14)
            lbl.pack(side="left", padx=4)
            self._key_labels[mode_key] = lbl

            # "Taste erkennen"-Button
            btn = tk.Button(
                row, text="⌨",
                command=lambda mk=mode_key: self._start_listen(mk),
                relief="flat", bd=0, padx=8, pady=4,
                bg=CARD, fg=MUTED, font=(FONT, 9),
                activebackground=BLUE, activeforeground="white",
            )
            btn.pack(side="right", padx=6)
            self._detect_btns[mode_key] = btn

            # "Tastenzuordnung löschen"-Button
            clr_btn = tk.Button(
                row, text="✕",
                command=lambda mk=mode_key: self._clear_key(mk),
                relief="flat", bd=0, padx=6, pady=4,
                bg=CARD, fg=MUTED, font=(FONT, 9),
                activebackground="#ef4444", activeforeground="white",
            )
            clr_btn.pack(side="right", padx=2)

        # ── Live-Modus-Taste ────────────────────────────────────────────
        tk.Label(frame, text="LIVE-MODUS", bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(anchor="w", padx=16, pady=(12, 4))

        live_key_name = cfg.get("live_key_name", "")
        live_row = tk.Frame(frame, bg=CARD)
        live_row.pack(fill="x", padx=16, pady=2)

        tk.Label(live_row, text="Live", bg=CARD, fg=FG,
                 font=(FONT, 10), width=12, anchor="w").pack(side="left", padx=8, pady=6)

        live_lbl = tk.Label(live_row, text=live_key_name,
                            bg=CARD, fg=BLUE if live_key_name else MUTED,
                            font=(FONT, 9), anchor="w", width=14)
        live_lbl.pack(side="left", padx=4)
        self._key_labels["live"] = live_lbl

        live_detect_btn = tk.Button(
            live_row, text="⌨",
            command=lambda: self._start_listen("live"),
            relief="flat", bd=0, padx=8, pady=4,
            bg=CARD, fg=MUTED, font=(FONT, 9),
            activebackground=BLUE, activeforeground="white",
        )
        live_detect_btn.pack(side="right", padx=6)
        self._detect_btns["live"] = live_detect_btn

        live_clr_btn = tk.Button(
            live_row, text="✕",
            command=lambda: self._clear_key("live"),
            relief="flat", bd=0, padx=6, pady=4,
            bg=CARD, fg=MUTED, font=(FONT, 9),
            activebackground="#ef4444", activeforeground="white",
        )
        live_clr_btn.pack(side="right", padx=2)

        # ── Transkriptions-Backend ───────────────────────────────────────
        tk.Label(frame, text="TRANSKRIPTION", bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(anchor="w", padx=16, pady=(12, 4))

        row_backend = tk.Frame(frame, bg=CARD)
        row_backend.pack(fill="x", padx=16, pady=2)

        backend_val = cfg.get("transcribe_backend", "online")
        self._backend_var = tk.StringVar(value=backend_val)

        btn_online = tk.Button(
            row_backend, text="Online",
            command=lambda: self._set_backend("online"),
            relief="flat", bd=0, padx=12, pady=4, font=(FONT, 9),
        )
        btn_lokal = tk.Button(
            row_backend, text="Lokal",
            command=lambda: self._set_backend("local"),
            relief="flat", bd=0, padx=12, pady=4, font=(FONT, 9),
        )
        btn_online.pack(side="left", padx=(8, 1), pady=6)
        btn_lokal.pack(side="left", padx=1)
        self._backend_btns = {"online": btn_online, "local": btn_lokal}

        model_val = cfg.get("local_whisper_model", "small")
        self._model_var = tk.StringVar(value=model_val)
        self._model_combo = ttk.Combobox(
            row_backend,
            textvariable=self._model_var,
            values=["small", "medium"],
            state="readonly",
            width=8,
            font=(FONT, 9),
        )
        self._model_combo.pack(side="left", padx=10)

        self._backend_warn_lbl = tk.Label(
            frame,
            text="faster-whisper nicht installiert — nur Online verfuegbar",
            bg=BG, fg=MUTED, font=(FONT, 8),
        )
        self._backend_warn_lbl.pack(anchor="w", padx=16)

        # Apply initial visual state
        self._refresh_backend_buttons()

        # Speichern
        tk.Button(
            frame, text="Speichern", bg=BLUE, fg="white",
            font=(FONT, 11, "bold"),
            relief="flat", bd=0, padx=20, pady=8,
            command=self._save_anpassen,
            activebackground="#2563eb", activeforeground="white",
        ).pack(pady=14)

    def _clear_key(self, mode_key: str) -> None:
        self._detected.pop(mode_key, None)
        self._cleared.add(mode_key)
        self._key_labels[mode_key].configure(text="", fg=MUTED)

    def _set_mode_trigger(self, mode_key: str, trigger: str) -> None:
        self._trigger_vars[mode_key].set(trigger)
        self._refresh_mode_trigger_buttons(mode_key)

    def _set_backend(self, backend: str) -> None:
        if self._backend_var:
            self._backend_var.set(backend)
        self._refresh_backend_buttons()

    def _refresh_backend_buttons(self) -> None:
        if not self._backend_var or not self._backend_btns:
            return
        is_online = self._backend_var.get() == "online"
        self._backend_btns["online"].configure(
            bg=BLUE if is_online else CARD,
            fg="white" if is_online else MUTED,
        )
        self._backend_btns["local"].configure(
            bg=BLUE if not is_online else CARD,
            fg="white" if not is_online else MUTED,
        )
        # Dropdown: enabled only when local is selected
        if self._model_combo:
            self._model_combo.configure(state="readonly" if not is_online else "disabled")
        # Warning: shown when local selected but faster-whisper not installed
        if self._backend_warn_lbl:
            show_warn = (not is_online) and (not local_available())
            if show_warn:
                self._backend_warn_lbl.pack(anchor="w", padx=16)
            else:
                self._backend_warn_lbl.pack_forget()

    def _refresh_mode_trigger_buttons(self, mode_key: str) -> None:
        is_hold = self._trigger_vars[mode_key].get() == "hold"
        btns = self._trigger_btns[mode_key]
        btns["hold"].configure(bg=BLUE if is_hold else CARD, fg="white" if is_hold else MUTED)
        btns["toggle"].configure(bg=BLUE if not is_hold else CARD, fg="white" if not is_hold else MUTED)

    def _start_listen(self, mode_key: str) -> None:
        if self._listening:
            return
        self._listening = True
        self._stop_daemon()

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

        for btn in self._detect_btns.values():
            btn.configure(state="normal", bg=CARD, fg=MUTED, text="⌨")

        if result is None:
            self._key_labels[mode_key].configure(text="Timeout", fg=MUTED)
            self._restart_daemon()
            return

        self._detected[mode_key] = result
        self._key_labels[mode_key].configure(text=result["key_name"], fg=GREEN)
        self._restart_daemon()

    def _save_anpassen(self) -> None:
        # Gecachte Config nutzen (Daemon könnte gestoppt gewesen sein)
        modes = dict(self._cfg.get("modes", {}))

        # trigger_mode pro Modus speichern
        for mode_key, tvar in self._trigger_vars.items():
            m = dict(modes.get(mode_key) or {})
            m["trigger_mode"] = tvar.get()
            modes[mode_key] = m

        # Erkannte Combos einpflegen
        for mode_key, detected in self._detected.items():
            m = dict(modes.get(mode_key) or {})
            m["key_codes"] = detected["key_codes"]
            m["key_name"]  = detected["key_name"]
            modes[mode_key] = m

        # Gelöschte Combos explizit leeren
        for mode_key in self._cleared:
            m = dict(modes.get(mode_key) or {})
            m["key_codes"] = []
            m["key_name"] = ""
            modes[mode_key] = m

        updates: dict = {"modes": modes}

        # Live-Key speichern
        if "live" in self._detected:
            d = self._detected["live"]
            updates["live_key_codes"] = d["key_codes"]
            updates["live_key_name"] = d["key_name"]
        elif "live" in self._cleared:
            updates["live_key_codes"] = []
            updates["live_key_name"] = ""

        mode_detected = {k: v for k, v in self._detected.items() if k != "live"}
        if mode_detected:
            first = next(iter(mode_detected.values()))
            updates["input_device"] = first["device_path"]

        # Transcription backend
        if self._backend_var:
            updates["transcribe_backend"] = self._backend_var.get()
        if self._model_var:
            updates["local_whisper_model"] = self._model_var.get()

        try:
            self.client.patch_config(updates)
            messagebox.showinfo("Gespeichert", "Einstellungen gespeichert.")
            self._restart_daemon()
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
            text=("HINWEIS\nFür direktes Einfügen: stt-trans einmal in Programme legen "
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
            self._restart_daemon()
        except Exception as e:
            messagebox.showerror("Fehler", str(e))

    def _stop_daemon(self) -> None:
        """Stoppt den stt-trans-Daemon (vor Key-Detection)."""
        import subprocess
        try:
            subprocess.run(
                ["systemctl", "--user", "stop", "stt-trans.service"],
                check=True, capture_output=True, timeout=10,
            )
        except Exception as e:
            import logging
            logging.getLogger("stt-trans.tray").warning("Daemon-Stop fehlgeschlagen: %s", e)

    def _restart_daemon(self) -> None:
        """Startet den stt-trans-Daemon neu damit neue Config wirksam wird."""
        import subprocess
        try:
            subprocess.run(
                ["systemctl", "--user", "restart", "stt-trans.service"],
                check=True, capture_output=True, timeout=10,
            )
        except Exception as e:
            import logging
            logging.getLogger("stt-trans.tray").warning("Daemon-Restart fehlgeschlagen: %s", e)

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
