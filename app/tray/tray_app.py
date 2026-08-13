import os
import threading

# Auf Wayland/KDE AppIndicator (SNI) bevorzugen, falls verfügbar
if "PYSTRAY_BACKEND" not in os.environ:
    try:
        import gi  # noqa: F401
        os.environ["PYSTRAY_BACKEND"] = "appindicator"
    except ImportError:
        pass

import pystray
from app.inject import notify
from app.tray.api_client import BlitztextClient
from app.tray.icon import create_tray_icon, create_recording_icon
from app.tray.settings_window import SettingsWindow


class BlitztextTrayApp:
    def __init__(self, api_url: str = "http://localhost:8765"):
        self.client = BlitztextClient(api_url)
        self._settings: SettingsWindow | None = None
        self._icon: pystray.Icon | None = None

    def run(self) -> None:
        icon_image = create_tray_icon(64)
        menu = pystray.Menu(
            pystray.MenuItem("Einstellungen öffnen", self._open_settings, default=True),
            pystray.MenuItem("Live-Modus starten", self._start_live),
            pystray.MenuItem("Live-Modus stoppen", self._stop_live),
            pystray.MenuItem("Neustart", self._restart_daemon),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Beenden", self._quit),
        )
        self._icon = pystray.Icon("stt-trans", icon_image, "stt-trans", menu)
        threading.Thread(target=self._poll_status, daemon=True).start()
        self._icon.run()

    def _start_live(self, icon=None, item=None) -> None:
        try:
            self.client.start_live()
            notify("recording", "Live-Modus gestartet")
        except Exception as e:
            notify("error", f"Live-Start fehlgeschlagen: {e}")

    def _stop_live(self, icon=None, item=None) -> None:
        try:
            self.client.stop_live()
            notify("done", "Live-Modus gestoppt")
        except Exception as e:
            notify("error", f"Live-Stop fehlgeschlagen: {e}")

    def _restart_daemon(self, icon=None, item=None) -> None:
        """Restart the stt-trans systemd user service (mit Feedback)."""
        import subprocess
        try:
            r = subprocess.run(
                ["systemctl", "--user", "restart", "stt-trans.service"],
                capture_output=True, timeout=15, text=True,
            )
            if r.returncode == 0:
                notify("done", "stt-trans neu gestartet")
            else:
                notify("error", f"Neustart fehlgeschlagen ({r.returncode})")
        except Exception as e:
            notify("error", f"Neustart-Fehler: {e}")

    def _open_settings(self, icon=None, item=None) -> None:
        # tkinter mainloop muss in eigenem Thread laufen (pystray blockiert main thread)
        def _run() -> None:
            if self._settings and self._settings._win:
                try:
                    if self._settings._win.winfo_exists():
                        self._settings._win.lift()
                        return
                except Exception:
                    pass
            self._settings = SettingsWindow(self.client)
            self._settings.show()

        t = threading.Thread(target=_run, daemon=True)
        t.start()

    def _poll_status(self) -> None:
        """Pollt /api/status alle 3s, wechselt Icon und aktualisiert Tooltip."""
        import time
        _idle_icon = create_tray_icon(64)
        _rec_icon = create_recording_icon(64)
        _was_recording = False

        while True:
            time.sleep(3)
            if self._icon is None:
                break
            api_ok = True
            is_rec = False
            mode = None
            try:
                status = self.client.get_status()
                is_rec = status.get("recording", False)
                mode = status.get("mode")
            except Exception:
                api_ok = False

            if is_rec != _was_recording:
                _was_recording = is_rec
                self._icon.icon = _rec_icon if is_rec else _idle_icon

            if api_ok:
                self._icon.title = (
                    f"stt-trans | API ✓ | recording: {mode}"
                    if is_rec else "stt-trans | API ✓ | idle"
                )
            else:
                self._icon.title = "stt-trans | API ✗ offline"

    def _quit(self, icon=None, item=None) -> None:
        # Erst den Diktatdienst stoppen – sonst tippt er nach dem Schliessen
        # des Tray-Symbols weiter Text ein (er laeuft als eigener systemd-Dienst).
        import subprocess
        try:
            subprocess.run(
                ["systemctl", "--user", "stop", "stt-trans.service"],
                capture_output=True, timeout=15, text=True,
            )
        except Exception:
            pass
        # Diese Tray-Instanz gewollt beenden. "stop" (kein Crash) neutralisiert
        # Restart=on-failure, damit das Symbol nicht sofort neu gestartet wird.
        # --no-block verhindert, dass wir uns selbst blockieren, waehrend systemd
        # uns per SIGTERM beendet.
        try:
            subprocess.Popen(
                ["systemctl", "--user", "--no-block", "stop", "stt-trans-tray.service"]
            )
        except Exception:
            pass
        if self._icon:
            self._icon.stop()


def main() -> None:
    BlitztextTrayApp().run()


if __name__ == "__main__":
    main()
