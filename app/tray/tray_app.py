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
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Beenden", self._quit),
        )
        self._icon = pystray.Icon("stt-trans", icon_image, "stt-trans", menu)
        threading.Thread(target=self._poll_status, daemon=True).start()
        self._icon.run()

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
        """Pollt /api/status alle 3s und wechselt Icon."""
        import time
        _idle_icon = create_tray_icon(64)
        _rec_icon = create_recording_icon(64)
        _was_recording = False

        while True:
            time.sleep(3)
            if self._icon is None:
                break
            try:
                status = self.client.get_status()
                is_rec = status.get("recording", False)
            except Exception:
                is_rec = False

            if is_rec != _was_recording:
                _was_recording = is_rec
                self._icon.icon = _rec_icon if is_rec else _idle_icon

    def _quit(self, icon=None, item=None) -> None:
        if self._icon:
            self._icon.stop()


def main() -> None:
    BlitztextTrayApp().run()


if __name__ == "__main__":
    main()
