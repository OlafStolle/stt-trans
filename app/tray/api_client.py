import glob
import requests
from typing import Any

try:
    import evdev
    _EVDEV = True
except ImportError:
    _EVDEV = False


class BlitztextClient:
    def __init__(self, base_url: str = "http://localhost:8765"):
        self.base = base_url.rstrip("/")

    def get_config(self) -> dict[str, Any]:
        r = requests.get(f"{self.base}/api/config", timeout=3)
        r.raise_for_status()
        return r.json()

    def patch_config(self, updates: dict[str, Any]) -> dict[str, Any]:
        r = requests.patch(f"{self.base}/api/config", json=updates, timeout=3)
        r.raise_for_status()
        return r.json()

    def get_health(self) -> dict[str, Any]:
        r = requests.get(f"{self.base}/api/health", timeout=3)
        r.raise_for_status()
        return r.json()

    def get_status(self) -> dict[str, Any]:
        r = requests.get(f"{self.base}/api/status", timeout=2)
        r.raise_for_status()
        return r.json()

    def start_live(self) -> dict[str, Any]:
        r = requests.post(f"{self.base}/live/start", timeout=5)
        r.raise_for_status()
        return r.json()

    def stop_live(self) -> dict[str, Any]:
        r = requests.post(f"{self.base}/live/stop", timeout=5)
        r.raise_for_status()
        return r.json()

    def list_input_devices(self) -> list[tuple[str, str]]:
        """Returns list of (path, name) tuples for all evdev devices."""
        devices: list[tuple[str, str]] = []
        if not _EVDEV:
            return devices
        for path in sorted(glob.glob("/dev/input/event*")):
            try:
                dev = evdev.InputDevice(path)
                devices.append((path, dev.name))
            except Exception:
                pass
        return devices
