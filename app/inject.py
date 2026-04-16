# app/inject.py
"""Text in aktives Fenster einfügen + Desktop-Benachrichtigungen."""
import subprocess
import logging
import time

logger = logging.getLogger("stt-trans.inject")

def inject_text(text: str, method: str = "xdotool", delay_ms: int = 50, paste_shortcut: str = "ctrl+shift+v") -> None:
    """Fügt text in das aktuell fokussierte Fenster ein."""
    if not text:
        return
    logger.info("inject_text: method=%s text=%r", method, text[:40])
    try:
        if method == "xdotool":
            subprocess.run(
                ["xdotool", "type", "--clearmodifiers",
                 f"--delay={delay_ms}", "--", text],
                check=True, capture_output=True,
            )
        elif method == "xclip+paste":
            subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=text.encode(), check=True,
            )
            subprocess.run(
                ["xdotool", "key", "--clearmodifiers", "ctrl+v"],
                check=True, capture_output=True,
            )
        elif method == "wtype":
            subprocess.run(
                ["wtype", "--", text],
                check=True, capture_output=True,
            )
        elif method == "ydotool":
            subprocess.run(
                ["ydotool", "type", "--", text],
                check=True, capture_output=True,
            )
        elif method == "wl-copy+paste":
            subprocess.run(
                ["wl-copy"],
                input=text.encode(), check=True,
            )
            time.sleep(0.15)
            # Keycodes: Shift=42, Ctrl=29, V=47
            if paste_shortcut == "ctrl+shift+v":
                keys = ["42:1", "29:1", "47:1", "47:0", "29:0", "42:0"]
            else:
                keys = ["29:1", "47:1", "47:0", "29:0"]
            result = subprocess.run(
                ["ydotool", "key", "--key-delay", "20"] + keys,
                capture_output=True,
            )
            logger.info("ydotool key exit=%d stderr=%r", result.returncode, result.stderr[:80] if result.stderr else b"")
        else:
            logger.warning("Unknown inject method: %s", method)
    except subprocess.CalledProcessError as e:
        logger.error("inject_text failed: %s", e)
    except FileNotFoundError as e:
        logger.error("Tool not found: %s", e)

_ICONS = {
    "recording": "media-record",
    "done":      "dialog-information",
    "error":     "dialog-error",
}

def notify(event: str, message: str, title: str = "stt-trans") -> None:
    """Sendet Desktop-Benachrichtigung via notify-send."""
    icon = _ICONS.get(event, "dialog-information")
    try:
        subprocess.run(
            ["notify-send", "-i", icon, "-t", "2000", title, message],
            check=False, capture_output=True,
        )
    except FileNotFoundError:
        logger.warning("notify-send not available")
