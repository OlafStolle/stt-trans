# app/inject.py
"""Text in aktives Fenster einfügen + Desktop-Benachrichtigungen."""
import subprocess
import logging

logger = logging.getLogger("blitztext.inject")

def inject_text(text: str, method: str = "xdotool", delay_ms: int = 50) -> None:
    """Fügt text in das aktuell fokussierte Fenster ein."""
    if not text:
        return
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

def notify(event: str, message: str, title: str = "Blitztext") -> None:
    """Sendet Desktop-Benachrichtigung via notify-send."""
    icon = _ICONS.get(event, "dialog-information")
    try:
        subprocess.run(
            ["notify-send", "-i", icon, "-t", "2000", title, message],
            check=False, capture_output=True,
        )
    except FileNotFoundError:
        logger.warning("notify-send not available")
