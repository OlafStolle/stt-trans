import threading
import pystray
from app.tray.api_client import BlitztextClient
from app.tray.icon import create_tray_icon
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
        self._icon = pystray.Icon("blitztext", icon_image, "Blitztext", menu)
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

    def _quit(self, icon=None, item=None) -> None:
        if self._icon:
            self._icon.stop()


def main() -> None:
    BlitztextTrayApp().run()


if __name__ == "__main__":
    main()
