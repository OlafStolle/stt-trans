# app/screencap.py
"""Bildschirminhalte als Kontext fuer den Workshop-Assistenten.

Nimmt in Abstaenden ein Bild des Bildschirms auf, verwirft unveraenderte
Aufnahmen und liest — sofern eine Texterkennung verfuegbar ist — den Text
heraus. Der Text geht als zusaetzlicher Kontext an Claude: was auf einer Folie
steht, sagt oft mehr als das, was dazu gesprochen wird.

Screenshot-Weg: `spectacle -b -n -f` (KDE/Wayland). `grim` scheitert an KWin,
weil KWin das wlr-screencopy-Protokoll nicht anbietet.

Texterkennung, in dieser Reihenfolge:
  1. tesseract mit deutschen/englischen Sprachdaten  — schnell (Sekunden)
  2. das lokale `ocr`-CLI (VLM)                      — genauer, aber traege
  3. keine                                           — Bilder werden nur abgelegt
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("stt-trans.screencap")

INTERVAL_SECONDS = 60
CAPTURE_TIMEOUT = 25
OCR_TIMEOUT = 90
#: Ab wieviel Prozent geaenderter Bildflaeche gilt der Bildschirm als neu.
CHANGE_THRESHOLD = 0.04
#: Breite des Vergleichsbildes — grob genug, dass ein Mauszeiger nicht ausloest.
THUMB_WIDTH = 160


def screenshot_tool() -> list[str] | None:
    """Der Befehl, der ein Vollbild aufnimmt (ohne Zielpfad), oder None."""
    if shutil.which("spectacle"):
        return ["spectacle", "-b", "-n", "-f", "-o"]
    if shutil.which("grim"):
        return ["grim"]
    return None


def ocr_backend() -> str | None:
    """'tesseract', 'ocr' oder None."""
    if shutil.which("tesseract"):
        try:
            langs = subprocess.run(["tesseract", "--list-langs"],
                                   capture_output=True, text=True, timeout=10).stdout
            if "deu" in langs.split() or "eng" in langs.split():
                return "tesseract"
        except Exception:
            pass
    if shutil.which("ocr"):
        return "ocr"
    return None


def capabilities() -> dict:
    tool = screenshot_tool()
    return {
        "screenshot": tool[0] if tool else None,
        "ocr": ocr_backend(),
        "hint": None if ocr_backend() else
                "Texterkennung fehlt — 'sudo pacman -S tesseract-data-deu "
                "tesseract-data-eng' schaltet die schnelle Variante frei.",
    }


def _thumbnail(path: Path) -> bytes | None:
    """Kleines Graustufenbild fuer den Aenderungsvergleich."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            h = max(1, int(im.height * THUMB_WIDTH / max(1, im.width)))
            return im.convert("L").resize((THUMB_WIDTH, h)).tobytes()
    except Exception:
        return None


def _changed(a: bytes | None, b: bytes | None) -> bool:
    """True, wenn sich genug Bildflaeche geaendert hat."""
    if a is None or b is None or len(a) != len(b):
        return True
    diff = sum(1 for x, y in zip(a, b) if abs(x - y) > 24)
    return diff / len(a) > CHANGE_THRESHOLD


class ScreenWatcher:
    """Sammelt Bildschirminhalte fuer eine Sitzung."""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.shots = 0
        self.skipped = 0
        self.last_error: str | None = None
        self._thumb: bytes | None = None
        self._pending: list[dict] = []   # noch nicht abgeholte Texte

    async def _capture(self, target: Path) -> bool:
        cmd = screenshot_tool()
        if cmd is None:
            self.last_error = "Kein Screenshot-Werkzeug gefunden"
            return False
        args = cmd + [str(target)] if cmd[-1] == "-o" or cmd[0] == "grim" else cmd + [str(target)]
        try:
            proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            _, err = await asyncio.wait_for(proc.communicate(), timeout=CAPTURE_TIMEOUT)
        except asyncio.TimeoutError:
            self.last_error = "Screenshot dauerte zu lange"
            return False
        except Exception as e:
            self.last_error = str(e)[:120]
            return False
        if not target.exists() or target.stat().st_size == 0:
            self.last_error = (err.decode()[:120] if err else "Screenshot blieb leer")
            return False
        return True

    async def _read_text(self, image: Path) -> str:
        backend = ocr_backend()
        if backend is None:
            return ""
        try:
            if backend == "tesseract":
                langs = subprocess.run(["tesseract", "--list-langs"],
                                       capture_output=True, text=True, timeout=10).stdout.split()
                lang = "+".join(l for l in ("deu", "eng") if l in langs) or "eng"
                proc = await asyncio.create_subprocess_exec(
                    "tesseract", str(image), "-", "-l", lang, "--psm", "3",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=OCR_TIMEOUT)
                return out.decode(errors="replace")
            proc = await asyncio.create_subprocess_exec(
                "ocr", str(image), stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(proc.wait(), timeout=OCR_TIMEOUT)
            md = image.with_suffix(".ocr.md")
            return md.read_text(encoding="utf-8", errors="replace") if md.exists() else ""
        except asyncio.TimeoutError:
            self.last_error = f"Texterkennung ({backend}) dauerte zu lange"
        except Exception as e:
            self.last_error = str(e)[:120]
        return ""

    @staticmethod
    def _clean(text: str) -> str:
        """Kurze Bruchstuecke wegwerfen — OCR-Rauschen hilft Claude nicht."""
        lines = [ln.strip() for ln in text.splitlines()]
        keep = [ln for ln in lines if len(ln) > 3]
        return "\n".join(keep).strip()

    async def _step(self) -> bool:
        stamp = datetime.now()
        target = self.out_dir / f"screen_{stamp:%H%M%S}.png"
        if not await self._capture(target):
            return False

        thumb = _thumbnail(target)
        if not _changed(self._thumb, thumb):
            target.unlink(missing_ok=True)
            self.skipped += 1
            return False
        self._thumb = thumb
        self.shots += 1

        text = self._clean(await self._read_text(target))
        if text:
            (target.with_suffix(".txt")).write_text(text, encoding="utf-8")
            self._pending.append({"t": stamp.strftime("%H:%M:%S"), "text": text[:4000]})
        return True

    def take_pending(self) -> list[dict]:
        """Gibt die seit dem letzten Abruf erkannten Bildschirmtexte zurueck."""
        out, self._pending = self._pending, []
        return out

    async def run(self) -> None:
        try:
            while True:
                await asyncio.sleep(INTERVAL_SECONDS)
                try:
                    await self._step()
                except Exception as e:
                    self.last_error = str(e)[:120]
                    logger.warning("screencap: %s", self.last_error)
        except asyncio.CancelledError:
            pass

    def status(self) -> dict:
        return {
            "shots": self.shots,
            "skipped_unchanged": self.skipped,
            "pending_texts": len(self._pending),
            "last_error": self.last_error,
            **capabilities(),
        }
