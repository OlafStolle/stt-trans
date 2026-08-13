# app/meeting_log.py
"""Persistenz fuer den Live-Modus: schreibt Meeting-Transkripte auf Platte.

Design:
- Eine Session = eine append-only JSONL-Datei. Jede Zeile wird sofort geflusht,
  damit ein Absturz hoechstens den letzten Chunk kostet.
- Beide Kanaele (Mikrofon + Unterhaltung) landen in DERSELBEN Datei, damit die
  chronologische Reihenfolge fuer das Protokoll erhalten bleibt.
- Beim Beenden wird zusaetzlich eine .md daneben geschrieben (lesbar ohne Tool).
- Das Schreiben passiert serverseitig, unabhaengig davon ob ein Browser offen ist.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("stt-trans.meeting_log")

_BASE_DIR = Path(
    os.getenv("BLITZTEXT_MEETING_DIR")
    or (Path(os.getenv("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
        / "blitztext" / "meetings")
)

_SESSION_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}$")

CHANNEL_LABELS = {"mic": "Mikrofon", "desktop": "Unterhaltung"}

_current: "MeetingLog | None" = None


class MeetingLog:
    """Append-only Transkript einer Live-Session."""

    def __init__(self, session_id: str, path: Path, started_at: float) -> None:
        self.session_id = session_id
        self.path = path
        self.started_at = started_at

    # -- schreiben ---------------------------------------------------------

    def append(
        self,
        *,
        channel: str,
        text: str,
        lang: str,
        translation: str | None,
        speaker: str | None,
    ) -> None:
        """Haengt einen Transkript-Eintrag an. Leerer Text wird ignoriert."""
        if not text or not text.strip():
            return
        entry = {
            "t": datetime.now().isoformat(timespec="seconds"),
            "elapsed": round(time.time() - self.started_at, 1),
            "channel": channel,
            "speaker": speaker,
            "lang": lang,
            "text": text.strip(),
            "translation": translation,
        }
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:
            logger.error("Meeting-Log schreiben fehlgeschlagen (%s): %s", self.path, e)

    # -- lesen -------------------------------------------------------------

    def entries(self) -> list[dict[str, Any]]:
        """Alle Eintraege in Schreibreihenfolge. Kaputte Zeilen werden uebersprungen."""
        if not self.path.exists():
            return []
        out: list[dict[str, Any]] = []
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        logger.warning("Kaputte Zeile in %s uebersprungen", self.path.name)
        except OSError as e:
            logger.error("Meeting-Log lesen fehlgeschlagen (%s): %s", self.path, e)
        return out

    def to_markdown(self) -> str:
        """Rendert das Transkript als Protokoll-Markdown, beide Kanaele chronologisch."""
        entries = self.entries()
        started = datetime.fromtimestamp(self.started_at)
        lines = [
            f"# Meeting {started.strftime('%d.%m.%Y %H:%M')}",
            "",
            f"- Session: `{self.session_id}`",
            f"- Beitraege: {len(entries)}",
        ]
        if entries:
            lines.append(f"- Dauer: {int(entries[-1].get('elapsed', 0) // 60)} min")
        lines += ["", "---", ""]

        last_channel = None
        for e in entries:
            channel = e.get("channel", "")
            label = CHANNEL_LABELS.get(channel, channel)
            if channel != last_channel:
                lines.append(f"### {label}")
                lines.append("")
                last_channel = channel
            meta = e.get("t", "")[11:19] or ""
            if e.get("speaker"):
                meta = f"{meta} · {e['speaker']}"
            if e.get("lang"):
                meta = f"{meta} · {e['lang']}"
            lines.append(f"**{meta}**  ")
            lines.append(e.get("text", ""))
            if e.get("translation"):
                lines.append("")
                lines.append(f"> {e['translation']}")
            lines.append("")
        return "\n".join(lines)

    def write_markdown(self) -> Path | None:
        """Schreibt die .md neben die .jsonl. Ohne Eintraege passiert nichts."""
        if not self.entries():
            return None
        md_path = self.path.with_suffix(".md")
        try:
            md_path.write_text(self.to_markdown(), encoding="utf-8")
            return md_path
        except OSError as e:
            logger.error("Markdown-Export fehlgeschlagen (%s): %s", md_path, e)
            return None


# ---------------------------------------------------------------------------
# Modul-API
# ---------------------------------------------------------------------------

def start_session() -> MeetingLog:
    """Startet eine Session. Idempotent — ein laufendes Meeting wird nie abgeschnitten."""
    global _current
    if _current is not None:
        return _current
    _BASE_DIR.mkdir(parents=True, exist_ok=True)
    now = time.time()
    session_id = datetime.fromtimestamp(now).strftime("%Y-%m-%d_%H%M%S")
    path = _BASE_DIR / f"{session_id}.jsonl"
    path.touch(exist_ok=True)
    _current = MeetingLog(session_id, path, now)
    logger.info("Meeting-Mitschrift: %s", path)
    return _current


def current() -> MeetingLog | None:
    return _current


def close_session() -> Path | None:
    """Beendet die Session und schreibt das Markdown-Protokoll.

    Eine Session ohne einen einzigen Beitrag hinterlaesst keine Datei-Leiche.
    """
    global _current
    log = _current
    _current = None
    if log is None:
        return None
    md = log.write_markdown()
    if md is None:
        try:
            log.path.unlink(missing_ok=True)
        except OSError:
            pass
    return md


def list_sessions() -> list[dict[str, Any]]:
    """Alle bisherigen Sessions, neueste zuerst."""
    if not _BASE_DIR.exists():
        return []
    out = []
    for p in sorted(_BASE_DIR.glob("*.jsonl"), reverse=True):
        log = MeetingLog(p.stem, p, p.stat().st_mtime)
        entries = log.entries()
        preview = next((e.get("text", "") for e in entries if e.get("text")), "")
        out.append({
            "session_id": p.stem,
            "entries": len(entries),
            "started": datetime.fromtimestamp(p.stat().st_ctime).isoformat(timespec="seconds"),
            "preview": preview[:120],
            "has_markdown": p.with_suffix(".md").exists(),
        })
    return out


def load_session(session_id: str) -> MeetingLog | None:
    """Laedt eine abgeschlossene Session. Verweigert Pfad-Traversal."""
    if not _SESSION_ID_RE.match(session_id or ""):
        return None
    path = _BASE_DIR / f"{session_id}.jsonl"
    if not path.exists():
        return None
    return MeetingLog(session_id, path, path.stat().st_ctime)


def base_dir() -> Path:
    return _BASE_DIR
