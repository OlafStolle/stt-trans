# app/workshop.py
"""Workshop-Assistent: liest die laufende Mitschrift und pflegt daraus Artefakte.

Ablauf:
  1. Der Assistent laeuft als Hintergrund-Task neben der Live-Session.
  2. Alle INTERVAL_SECONDS (oder ab MIN_NEW_ENTRIES neuen Beitraegen) nimmt er
     die seit dem letzten Lauf hinzugekommenen Transkript-Zeilen.
  3. Er schickt sie zusammen mit dem BISHERIGEN Artefakt-Stand an Claude und
     laesst die Artefakte fortschreiben (rolling summary statt Neuerzeugung).
  4. Das Ergebnis wird in Einzeldateien + eine HTML-Ansicht geschrieben.

Bewusst nicht pro Satz: aus einzelnen Sprachschnipseln laesst sich kein
sinnvolles Kanban oder Ablaufdiagramm bauen. Erst ein paar Minuten Gespraech
ergeben verwertbare Struktur.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import datetime
from pathlib import Path

from app import meeting_log

logger = logging.getLogger("stt-trans.workshop")

INTERVAL_SECONDS = 90
MIN_NEW_ENTRIES = 12
CLAUDE_TIMEOUT = 180

# Abschnitt -> (Dateiname, Ueberschrift fuer die HTML-Ansicht)
SECTIONS: dict[str, tuple[str, str]] = {
    "ZUSAMMENFASSUNG": ("zusammenfassung.md", "Worum ging es"),
    "AUFGABEN":        ("aufgaben.md", "Aufgaben"),
    "KANBAN":          ("kanban.md", "Kanban"),
    "ABLAUF":          ("ablauf.md", "Ablauf"),
    "TABELLEN":        ("tabellen.md", "Zahlen & Vergleiche"),
    "MOCKUP":          ("mockup.html", "Entwurf"),
    "OFFEN":           ("offene_fragen.md", "Offene Fragen"),
}

# Abschnitte, die fertiges HTML statt Markdown enthalten.
_HTML_SECTIONS = {"MOCKUP"}

_SYSTEM_PROMPT = """Du bist Protokollant und Analyst in einem laufenden Workshop.

Du bekommst:
1. Den BISHERIGEN Stand deiner Artefakte.
2. Die NEUEN Gespraechsabschnitte seit deinem letzten Durchgang.

Deine Aufgabe: Schreibe die Artefakte fort. Behalte alles Bestehende bei, das
weiterhin stimmt. Ergaenze Neues. Korrigiere, was sich als falsch herausgestellt
hat. Erfinde nichts, was nicht gesagt wurde.

Das Transkript stammt aus automatischer Spracherkennung und enthaelt Fehler,
abgeschnittene Saetze und Wortverwechslungen. Lies sinnerhaltend. Wenn eine
Stelle unklar ist, lass sie weg statt zu raten.

Gib deine Antwort GENAU in diesem Format aus, alle sechs Abschnitte, auch wenn
einer leer bleibt:

=== ZUSAMMENFASSUNG ===
Fliesstext, hoechstens 200 Woerter. Die besprochenen Themen in der Reihenfolge,
in der sie kamen. Getroffene Entscheidungen ausdruecklich als solche benennen.

=== AUFGABEN ===
Markdown-Tabelle mit den Spalten: Aufgabe | Wer | Bis wann | Status
Nur Aufgaben, die wirklich genannt wurden. Unbekanntes als "offen" eintragen.
Nennt jemand beim Zusagen seinen eigenen Namen ("Das mache ich, Miriam"), trage
diesen Namen als Verantwortlichen ein statt der Sprecher-Nummer. Bleibt unklar,
wer gemeint ist, schreibe die Sprecher-Bezeichnung.

=== KANBAN ===
Drei Markdown-Listen unter den Ueberschriften "### Backlog", "### In Arbeit",
"### Erledigt". Jeder Punkt eine Zeile.

=== ABLAUF ===
Ein Mermaid-Diagramm des besprochenen Ablaufs oder Prozesses, in einem
```mermaid Codeblock. Nutze flowchart TD. Wenn noch kein Ablauf erkennbar ist,
schreibe nur: (noch kein Ablauf erkennbar)

=== TABELLEN ===
Genannte Zahlen, Vergleiche, Optionen als Markdown-Tabellen. Wenn nichts
Tabellarisches vorkam: (keine)

=== MOCKUP ===
NUR wenn im Gespraech eine Oberflaeche, Maske, Seite oder ein Formular
beschrieben wurde: ein schlichter HTML-Entwurf davon. Reines HTML mit einem
<style>-Block, keine Skripte, keine externen Dateien. Graustufen, sachlich,
Systemschrift — ein Wireframe, kein fertiges Design. Setze nur Elemente hinein,
die tatsaechlich genannt wurden.
Wenn keine Oberflaeche besprochen wurde, schreibe nur: (kein Entwurf)

=== OFFEN ===
Aufzaehlung der Fragen, die im Gespraech offen geblieben sind.

Schreibe auf Deutsch. Keine Einleitung, kein Schlusswort, keine Meta-Kommentare
ueber deine Arbeit. Beginne direkt mit "=== ZUSAMMENFASSUNG ===".
"""


class WorkshopAgent:
    """Pflegt die Artefakte einer Meeting-Session."""

    def __init__(self, session_id: str, out_dir: Path, model: str = "sonnet",
                 watcher=None) -> None:
        self.session_id = session_id
        self.out_dir = out_dir
        self.model = model
        self.watcher = watcher      # optionaler ScreenWatcher
        self.processed = 0          # bereits verarbeitete Transkript-Zeilen
        self.runs = 0
        self.last_run: str | None = None
        self.last_error: str | None = None
        self.sections: dict[str, str] = {}
        self.out_dir.mkdir(parents=True, exist_ok=True)

    # -- Artefakt-Erzeugung ------------------------------------------------

    def _current_state(self) -> str:
        if not self.sections:
            return "(noch keine Artefakte — dies ist der erste Durchgang)"
        return "\n\n".join(
            f"=== {name} ===\n{body}" for name, body in self.sections.items()
        )

    @staticmethod
    def _format_entries(entries: list[dict]) -> str:
        lines = []
        for e in entries:
            who = e.get("speaker") or ("Mikrofon" if e.get("channel") == "mic" else "Unterhaltung")
            lines.append(f"[{e.get('t','')[11:19]}] {who}: {e.get('text','')}")
        return "\n".join(lines)

    @staticmethod
    def _parse(raw: str) -> dict[str, str]:
        """Zerlegt die Claude-Antwort in die Abschnitte."""
        out: dict[str, str] = {}
        parts = re.split(r"^===\s*([A-ZÄÖÜ]+)\s*===\s*$", raw, flags=re.MULTILINE)
        # parts = [vorspann, name1, body1, name2, body2, ...]
        for i in range(1, len(parts) - 1, 2):
            name = parts[i].strip()
            if name in SECTIONS:
                out[name] = parts[i + 1].strip()
        return out

    async def _ask_claude(self, new_entries: list[dict]) -> dict[str, str]:
        from app.process import _call_claude_cli

        teile = [
            "## Bisheriger Stand deiner Artefakte\n",
            self._current_state(),
            "\n## Neue Gespraechsabschnitte\n",
            self._format_entries(new_entries),
        ]
        # Was auf dem geteilten Bildschirm stand, ergaenzt das Gesagte.
        if self.watcher is not None:
            screens = self.watcher.take_pending()
            if screens:
                teile.append("\n## Bildschirminhalte (Texterkennung, fehlerbehaftet)\n")
                for s in screens:
                    teile.append(f"[{s['t']}]\n{s['text']}\n")
        user = "\n".join(teile) + "\n"
        raw = await asyncio.wait_for(
            _call_claude_cli(_SYSTEM_PROMPT, user, self.model),
            timeout=CLAUDE_TIMEOUT,
        )
        parsed = self._parse(raw)
        if not parsed:
            raise RuntimeError(f"Antwort ohne erkennbare Abschnitte: {raw[:200]}")
        return parsed

    # -- Schreiben ---------------------------------------------------------

    @staticmethod
    def _clean_html_block(body: str) -> str:
        """Loest ```html-Fences auf, falls Claude den Entwurf eingerahmt hat."""
        m = re.search(r"```(?:html)?\s*\n(.*?)```", body, flags=re.DOTALL)
        return (m.group(1) if m else body).strip()

    def _mockup_html(self) -> str | None:
        """Der Entwurf, oder None wenn keiner vorliegt."""
        raw = self.sections.get("MOCKUP", "").strip()
        if not raw or raw.startswith("(kein") or "<" not in raw:
            return None
        return self._clean_html_block(raw)

    def _write(self) -> None:
        for name, body in self.sections.items():
            fname, title = SECTIONS[name]
            path = self.out_dir / fname
            if name in _HTML_SECTIONS:
                entwurf = self._mockup_html()
                if entwurf:
                    path.write_text(entwurf, encoding="utf-8")
                continue
            path.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
        (self.out_dir / "uebersicht.html").write_text(self._render_html(), encoding="utf-8")

    @staticmethod
    def _markdown_to_html(body: str) -> str:
        """Markdown -> HTML. Mermaid-Bloecke bleiben als <pre class="mermaid">.

        Das Transkript stammt aus Spracherkennung; Claude gibt es teils woertlich
        wieder. Deshalb wird alles ausserhalb der von uns erzeugten Struktur
        escaped — kein Fremdtext darf als Markup wirken.
        """
        blocks: list[str] = []

        def _stash(m: re.Match) -> str:
            blocks.append(m.group(1).strip())
            return f"\x00MERMAID{len(blocks) - 1}\x00"

        body = re.sub(r"```mermaid\s*\n(.*?)```", _stash, body, flags=re.DOTALL)

        # Python-Markdown reicht rohes HTML unveraendert durch. Deshalb wird
        # vorher alles escaped: Tabellen, Listen und Ueberschriften brauchen
        # kein HTML und funktionieren danach weiterhin — ein <script> aus dem
        # Transkript aber nicht mehr.
        body = html.escape(body, quote=True)

        try:
            import markdown as _md
            rendered = _md.markdown(body, extensions=["tables", "fenced_code", "nl2br"])
        except Exception:
            rendered = f"<pre>{body}</pre>"

        for i, code in enumerate(blocks):
            rendered = rendered.replace(
                f"\x00MERMAID{i}\x00",
                f'<pre class="mermaid">{html.escape(code)}</pre>',
            )
        return rendered

    def _render_html(self) -> str:
        stamp = self.last_run or "—"
        cards = []
        shown: list[str] = []
        for name, (_fname, title) in SECTIONS.items():
            body = self.sections.get(name, "").strip()
            if not body:
                continue
            if name in _HTML_SECTIONS:
                entwurf = self._mockup_html()
                if not entwurf:
                    continue
                # Der Entwurf stammt aus Transkript-Inhalten: abgeschottet
                # anzeigen, damit nichts daraus in diese Seite hineinwirkt.
                inner = html.escape(entwurf, quote=True)
                content = (f'<iframe class="mockup-frame" sandbox srcdoc="{inner}"'
                           f' title="{html.escape(title)}"></iframe>')
            else:
                content = f'<div class="md">{self._markdown_to_html(body)}</div>'
            shown.append(name)
            cards.append(
                f'<section class="card" id="c-{name.lower()}">'
                f'<h2>{html.escape(title)}</h2>{content}</section>'
            )
        nav = " ".join(
            f'<a href="#c-{n.lower()}">{html.escape(SECTIONS[n][1])}</a>' for n in shown
        )
        return _HTML_TEMPLATE.format(
            session=html.escape(self.session_id),
            stamp=html.escape(stamp),
            runs=self.runs,
            entries=self.processed,
            nav=nav,
            cards="\n".join(cards) or '<p class="empty">Noch nichts verdichtet.</p>',
        )

    # -- Hauptschleife -----------------------------------------------------

    async def _step(self, force: bool = False) -> bool:
        """Ein Verdichtungsdurchgang. True = es wurde etwas verarbeitet."""
        log = meeting_log.current()
        if log is None or log.session_id != self.session_id:
            return False
        entries = log.entries()
        new = entries[self.processed:]
        if not new or (not force and len(new) < MIN_NEW_ENTRIES):
            return False
        try:
            self.sections = await self._ask_claude(new)
            self.processed = len(entries)
            self.runs += 1
            self.last_run = datetime.now().strftime("%H:%M:%S")
            self.last_error = None
            self._write()
            logger.info("workshop: Durchgang %d, %d Beitraege verarbeitet",
                        self.runs, self.processed)
            return True
        except asyncio.TimeoutError:
            self.last_error = f"Claude antwortete nicht innerhalb {CLAUDE_TIMEOUT}s"
            logger.warning("workshop: %s", self.last_error)
        except Exception as e:
            self.last_error = str(e)[:200]
            logger.error("workshop: %s", self.last_error)
        return False

    async def run(self) -> None:
        """Laeuft bis zum Abbruch. Ein letzter Durchgang beim Sessionende."""
        try:
            while True:
                await asyncio.sleep(INTERVAL_SECONDS)
                if meeting_log.current() is None:
                    break  # Session beendet
                await self._step()
        except asyncio.CancelledError:
            pass
        finally:
            # Abschluss-Durchgang: alles noch nicht Verdichtete mitnehmen.
            try:
                await self._step(force=True)
            except Exception:
                pass

    def status(self) -> dict:
        s = {
            "running": True,
            "session_id": self.session_id,
            "runs": self.runs,
            "entries_processed": self.processed,
            "last_run": self.last_run,
            "last_error": self.last_error,
            "dir": str(self.out_dir),
            "sections": [n for n in SECTIONS if self.sections.get(n, "").strip()],
            "has_mockup": self._mockup_html() is not None,
            "model": self.model,
        }
        if self.watcher is not None:
            s["screen"] = self.watcher.status()
        return s


_HTML_TEMPLATE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Workshop {session}</title>
<style>
 :root {{ color-scheme: dark; }}
 * {{ box-sizing: border-box; }}
 body {{ font-family: -apple-system, "Segoe UI", sans-serif; background:#0f0f11;
        color:#e0e0e0; margin:0; padding:24px; line-height:1.55; max-width:1100px; }}
 header {{ border-bottom:1px solid #2a2a35; padding-bottom:12px; margin-bottom:20px; }}
 h1 {{ font-size:18px; margin:0 0 6px; }}
 .meta {{ font-size:12px; color:#6b7280; }}
 nav {{ margin-top:10px; display:flex; gap:12px; flex-wrap:wrap; }}
 nav a {{ font-size:11px; color:#7dd3fc; text-decoration:none;
          border:1px solid #1e3a4a; border-radius:4px; padding:2px 8px; }}
 nav a:hover {{ background:#143a4a; }}
 .card {{ background:#16161b; border:1px solid #2a2a35; border-radius:8px;
          padding:16px 20px; margin-bottom:16px; }}
 .card h2 {{ font-size:13px; text-transform:uppercase; letter-spacing:.08em;
             color:#a0a0b0; margin:0 0 12px; }}
 .md {{ font-size:14px; }}
 .md > *:first-child {{ margin-top:0; }}
 .md > *:last-child {{ margin-bottom:0; }}
 .md h3 {{ font-size:12px; text-transform:uppercase; letter-spacing:.06em;
           color:#7dd3fc; margin:16px 0 6px; }}
 .md table {{ border-collapse:collapse; width:100%; margin:10px 0; font-size:13px;
              display:block; overflow-x:auto; }}
 .md th, .md td {{ border:1px solid #2a2a35; padding:6px 10px; text-align:left; }}
 .md th {{ background:#1f1f28; color:#c0c0d0; font-weight:600; }}
 .md tr:nth-child(even) td {{ background:#131318; }}
 .md ul {{ margin:6px 0; padding-left:20px; }}
 .md li {{ margin:3px 0; }}
 .md code {{ background:#1f1f28; padding:1px 5px; border-radius:3px; font-size:12px; }}
 .md pre {{ background:#131318; border:1px solid #2a2a35; border-radius:6px;
            padding:12px; overflow-x:auto; }}
 .md pre.mermaid {{ background:#131318; text-align:center; }}
 .mockup-frame {{ width:100%; height:520px; border:1px solid #2a2a35;
                  border-radius:6px; background:#fff; }}
 .empty {{ color:#555; }}
</style>
<header>
  <h1>Workshop-Mitschrift</h1>
  <div class="meta">Sitzung {session} · Stand {stamp} · {runs} Durchgänge · {entries} Beiträge</div>
  <nav>{nav}</nav>
</header>
{cards}
<script src="/static/vendor/mermaid.min.js"></script>
<script>
  if (window.mermaid) mermaid.initialize({{ startOnLoad: true, theme: 'dark' }});
</script>
"""


# ---------------------------------------------------------------------------
# Modul-API
# ---------------------------------------------------------------------------

_agent: WorkshopAgent | None = None
_task: asyncio.Task | None = None
_watcher = None
_watch_task: asyncio.Task | None = None


def current() -> WorkshopAgent | None:
    return _agent


def start(model: str = "sonnet", watch_screen: bool = False) -> dict:
    """Startet den Assistenten fuer die laufende Session.

    watch_screen ist bewusst standardmaessig aus: die Aufnahme erfasst den
    GESAMTEN Bildschirm, also auch alles, was neben der Besprechung offen ist.
    Das muss eine bewusste Entscheidung sein, keine Voreinstellung.
    """
    """Startet den Assistenten fuer die laufende Session."""
    global _agent, _task, _watcher, _watch_task
    log = meeting_log.current()
    if log is None:
        return {"ok": False, "error": "Keine laufende Live-Session"}
    if _agent is not None and _task and not _task.done():
        return {"ok": True, "already_running": True, **_agent.status()}

    out_dir = log.path.parent / f"{log.session_id}_workshop"

    _watcher, _watch_task = None, None
    if watch_screen:
        from app import screencap
        if screencap.screenshot_tool() is not None:
            _watcher = screencap.ScreenWatcher(out_dir / "bildschirm")
            _watch_task = asyncio.create_task(_watcher.run())

    _agent = WorkshopAgent(log.session_id, out_dir, model, watcher=_watcher)
    _task = asyncio.create_task(_agent.run())
    logger.info("workshop: gestartet fuer %s -> %s (Bildschirm: %s)",
                log.session_id, out_dir, "ja" if _watcher else "nein")
    return {"ok": True, "already_running": False, **_agent.status()}


async def stop() -> dict:
    """Stoppt den Assistenten und laesst einen Abschlussdurchgang laufen."""
    global _agent, _task, _watcher, _watch_task
    if _task is None or _agent is None:
        return {"ok": True, "running": False}
    if _watch_task is not None:
        _watch_task.cancel()
        try:
            await _watch_task
        except asyncio.CancelledError:
            pass
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    status = _agent.status()
    status["running"] = False
    _agent, _task, _watcher, _watch_task = None, None, None, None
    return {"ok": True, **status}


def status() -> dict:
    if _agent is None or _task is None or _task.done():
        return {"running": False}
    return _agent.status()
