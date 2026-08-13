import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import meeting_log, workshop


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(meeting_log, "_BASE_DIR", tmp_path / "meetings")
    meeting_log.close_session()
    workshop._agent = None
    workshop._task = None
    yield
    meeting_log.close_session()
    workshop._agent = None
    workshop._task = None


CLAUDE_ANTWORT = """=== ZUSAMMENFASSUNG ===
Es ging um die Migration der Kundendatenbank. Entscheidung: Umstieg auf
PostgreSQL bis Quartalsende.

=== AUFGABEN ===
| Aufgabe | Wer | Bis wann | Status |
|---|---|---|---|
| Schema entwerfen | Thomas | KW 34 | offen |

=== KANBAN ===
### Backlog
- Altdaten sichten

### In Arbeit
- Schema entwerfen

### Erledigt
- Kickoff

=== ABLAUF ===
```mermaid
flowchart TD
  A[Altsystem] --> B[Export]
  B --> C[PostgreSQL]
```

=== TABELLEN ===
| Option | Kosten |
|---|---|
| PostgreSQL | 0 EUR |

=== OFFEN ===
- Wer übernimmt die Abnahme?
"""


def _agent(tmp_path):
    return workshop.WorkshopAgent("2026-08-07_120000", tmp_path / "wk")


def test_parse_extracts_all_sections(tmp_path):
    got = _agent(tmp_path)._parse(CLAUDE_ANTWORT)
    assert set(got) == set(workshop.SECTIONS) - {"MOCKUP"}
    assert "PostgreSQL" in got["ZUSAMMENFASSUNG"]
    assert "| Thomas |" in got["AUFGABEN"]
    assert "### Backlog" in got["KANBAN"]
    assert "flowchart TD" in got["ABLAUF"]


def test_parse_survives_missing_sections(tmp_path):
    teil = "=== ZUSAMMENFASSUNG ===\nNur ein Abschnitt.\n"
    got = _agent(tmp_path)._parse(teil)
    assert got == {"ZUSAMMENFASSUNG": "Nur ein Abschnitt."}


def test_parse_ignores_unknown_sections(tmp_path):
    txt = "=== ZUSAMMENFASSUNG ===\nA\n\n=== ERFUNDEN ===\nB\n"
    got = _agent(tmp_path)._parse(txt)
    assert "ERFUNDEN" not in got
    assert got["ZUSAMMENFASSUNG"] == "A"


def test_write_creates_one_file_per_section_plus_html(tmp_path):
    a = _agent(tmp_path)
    a.sections = a._parse(CLAUDE_ANTWORT)
    a.last_run = "12:34:56"
    a._write()
    for name, (fname, _title) in workshop.SECTIONS.items():
        if name == "MOCKUP":
            continue  # ohne Entwurf wird keine Datei geschrieben
        assert (a.out_dir / fname).exists(), fname
    html = (a.out_dir / "uebersicht.html").read_text()
    assert "Workshop-Mitschrift" in html
    assert "PostgreSQL" in html


def test_html_escapes_transcript_content(tmp_path):
    """Transkript kommt aus Spracherkennung — nichts davon darf als HTML wirken."""
    a = _agent(tmp_path)
    a.sections = {"ZUSAMMENFASSUNG": "<script>alert(1)</script> & <b>fett</b>"}
    a._write()
    html = (a.out_dir / "uebersicht.html").read_text()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_step_waits_for_enough_material(tmp_path):
    log = meeting_log.start_session()
    for i in range(3):
        log.append(channel="mic", text=f"Satz {i}", lang="de", translation=None, speaker=None)
    a = workshop.WorkshopAgent(log.session_id, tmp_path / "wk")

    with patch.object(a, "_ask_claude", AsyncMock()) as ask:
        assert asyncio.run(a._step()) is False
        ask.assert_not_awaited()  # 3 < MIN_NEW_ENTRIES


def test_step_forced_runs_with_little_material(tmp_path):
    log = meeting_log.start_session()
    log.append(channel="mic", text="Einziger Satz", lang="de", translation=None, speaker=None)
    a = workshop.WorkshopAgent(log.session_id, tmp_path / "wk")

    with patch.object(a, "_ask_claude", AsyncMock(return_value={"ZUSAMMENFASSUNG": "kurz"})):
        assert asyncio.run(a._step(force=True)) is True
    assert a.runs == 1
    assert a.processed == 1
    assert (a.out_dir / "zusammenfassung.md").exists()


def test_step_only_sends_new_entries(tmp_path):
    log = meeting_log.start_session()
    for i in range(15):
        log.append(channel="mic", text=f"alt {i}", lang="de", translation=None, speaker=None)
    a = workshop.WorkshopAgent(log.session_id, tmp_path / "wk")

    with patch.object(a, "_ask_claude", AsyncMock(return_value={"ZUSAMMENFASSUNG": "x"})) as ask:
        asyncio.run(a._step())
        for i in range(15):
            log.append(channel="mic", text=f"neu {i}", lang="de", translation=None, speaker=None)
        asyncio.run(a._step())

    zweiter_aufruf = ask.await_args_list[1].args[0]
    texte = [e["text"] for e in zweiter_aufruf]
    assert all(t.startswith("neu") for t in texte), "alte Beiträge wurden erneut geschickt"
    assert len(texte) == 15


def test_step_keeps_old_artifacts_when_claude_fails(tmp_path):
    log = meeting_log.start_session()
    for i in range(15):
        log.append(channel="mic", text=f"a {i}", lang="de", translation=None, speaker=None)
    a = workshop.WorkshopAgent(log.session_id, tmp_path / "wk")

    with patch.object(a, "_ask_claude", AsyncMock(return_value={"ZUSAMMENFASSUNG": "gut"})):
        asyncio.run(a._step())
    for i in range(15):
        log.append(channel="mic", text=f"b {i}", lang="de", translation=None, speaker=None)
    with patch.object(a, "_ask_claude", AsyncMock(side_effect=RuntimeError("claude weg"))):
        assert asyncio.run(a._step()) is False

    assert a.sections["ZUSAMMENFASSUNG"] == "gut", "alter Stand ging verloren"
    assert a.last_error and "claude weg" in a.last_error
    assert a.runs == 1


def test_step_retries_same_entries_after_failure(tmp_path):
    """Nach einem Fehlschlag dürfen die Beiträge nicht verloren gehen."""
    log = meeting_log.start_session()
    for i in range(15):
        log.append(channel="mic", text=f"s {i}", lang="de", translation=None, speaker=None)
    a = workshop.WorkshopAgent(log.session_id, tmp_path / "wk")

    with patch.object(a, "_ask_claude", AsyncMock(side_effect=RuntimeError("weg"))):
        asyncio.run(a._step())
    assert a.processed == 0

    with patch.object(a, "_ask_claude", AsyncMock(return_value={"ZUSAMMENFASSUNG": "ok"})) as ask:
        asyncio.run(a._step())
    assert len(ask.await_args_list[0].args[0]) == 15
    assert a.processed == 15


def test_start_without_session_refuses():
    assert meeting_log.current() is None
    r = workshop.start()
    assert r["ok"] is False
    assert "Live-Session" in r["error"]


def test_status_when_not_running():
    assert workshop.status() == {"running": False}


def test_format_entries_labels_channels(tmp_path):
    a = _agent(tmp_path)
    txt = a._format_entries([
        {"t": "2026-08-07T12:00:05", "channel": "mic", "text": "Hallo", "speaker": None},
        {"t": "2026-08-07T12:00:09", "channel": "desktop", "text": "Moin", "speaker": "Sprecher 2"},
    ])
    assert "[12:00:05] Mikrofon: Hallo" in txt
    assert "[12:00:09] Sprecher 2: Moin" in txt


# --- Rendering ---------------------------------------------------------

def test_markdown_tables_become_html_tables(tmp_path):
    a = _agent(tmp_path)
    a.sections = a._parse(CLAUDE_ANTWORT)
    a._write()
    html = (a.out_dir / "uebersicht.html").read_text()
    assert "<table>" in html and "<th>" in html, "Tabelle blieb Rohtext"
    assert "<td>Thomas</td>" in html


def test_mermaid_block_survives_as_pre_mermaid(tmp_path):
    a = _agent(tmp_path)
    a.sections = a._parse(CLAUDE_ANTWORT)
    a._write()
    html = (a.out_dir / "uebersicht.html").read_text()
    assert '<pre class="mermaid">' in html
    assert "flowchart TD" in html
    assert "mermaid.min.js" in html, "Renderer nicht eingebunden"


def test_markdown_headings_and_lists_render(tmp_path):
    a = _agent(tmp_path)
    a.sections = a._parse(CLAUDE_ANTWORT)
    a._write()
    html = (a.out_dir / "uebersicht.html").read_text()
    assert "<h3>Backlog</h3>" in html
    assert "<li>Altdaten sichten</li>" in html


def test_injection_in_transcript_stays_inert(tmp_path):
    """Text aus dem Gespräch darf niemals als Markup wirken.

    Entscheidend ist nicht, ob die Zeichenfolge 'onerror=' im Dokument steht,
    sondern ob daraus ein Element entsteht. Ohne '<' gibt es kein Tag und damit
    auch kein ausgewertetes Attribut.
    """
    a = _agent(tmp_path)
    a.sections = {"ZUSAMMENFASSUNG": '<img src=x onerror="alert(1)"> und <script>bad()</script>'}
    a._write()
    doc = (a.out_dir / "uebersicht.html").read_text()

    # Aus dem Transkript darf kein Element entstanden sein …
    assert "<img" not in doc, "img-Tag aus dem Transkript ist echt geworden"
    # … die einzigen script-Tags sind unsere eigenen (Mermaid-Renderer).
    assert doc.count("<script") == 2, "zusätzliches script-Tag im Dokument"
    # … und der Text steht entschärft drin.
    assert "&lt;img src=x" in doc and "&lt;script&gt;bad()" in doc


# --- Mockup ------------------------------------------------------------

MOCKUP_ANTWORT = """=== ZUSAMMENFASSUNG ===
Onboarding-Maske besprochen.

=== MOCKUP ===
<style>.f{font-family:sans-serif}</style>
<div class="f"><h1>Neukunde anlegen</h1><input placeholder="Firma"><button>Speichern</button></div>
"""


def test_mockup_is_written_as_html_file(tmp_path):
    a = _agent(tmp_path)
    a.sections = a._parse(MOCKUP_ANTWORT)
    a._write()
    f = a.out_dir / "mockup.html"
    assert f.exists()
    body = f.read_text()
    assert "Neukunde anlegen" in body
    assert not body.startswith("#"), "Markdown-Überschrift ins HTML geschrieben"


def test_mockup_renders_in_sandboxed_frame(tmp_path):
    a = _agent(tmp_path)
    a.sections = a._parse(MOCKUP_ANTWORT)
    a._write()
    html = (a.out_dir / "uebersicht.html").read_text()
    assert "<iframe" in html and "sandbox" in html
    assert "srcdoc=" in html
    # Der Entwurf darf nicht roh in der Seite stehen
    assert "<h1>Neukunde anlegen</h1>" not in html
    assert "&lt;h1&gt;" in html


def test_mockup_absent_is_not_rendered(tmp_path):
    a = _agent(tmp_path)
    a.sections = {"ZUSAMMENFASSUNG": "nur Text", "MOCKUP": "(kein Entwurf)"}
    a._write()
    assert not (a.out_dir / "mockup.html").exists()
    assert "<iframe" not in (a.out_dir / "uebersicht.html").read_text()


def test_mockup_fenced_block_is_unwrapped(tmp_path):
    a = _agent(tmp_path)
    a.sections = {"MOCKUP": "```html\n<div>Maske</div>\n```"}
    a._write()
    assert (a.out_dir / "mockup.html").read_text().strip() == "<div>Maske</div>"


# --- Bildschirminhalte -------------------------------------------------

def test_screen_texts_reach_claude(tmp_path):
    log = meeting_log.start_session()
    for i in range(15):
        log.append(channel="mic", text=f"gesagt {i}", lang="de", translation=None, speaker=None)

    watcher = MagicMock()
    watcher.take_pending.return_value = [{"t": "10:00:00", "text": "Folie: Quartalsziele Q3"}]
    a = workshop.WorkshopAgent(log.session_id, tmp_path / "wk", watcher=watcher)

    with patch("app.process._call_claude_cli",
               AsyncMock(return_value="=== ZUSAMMENFASSUNG ===\nok")) as cli:
        asyncio.run(a._step())

    prompt = cli.await_args_list[0].args[1]
    assert "Bildschirminhalte" in prompt
    assert "Quartalsziele Q3" in prompt


def test_no_watcher_means_no_screen_section(tmp_path):
    log = meeting_log.start_session()
    for i in range(15):
        log.append(channel="mic", text=f"x {i}", lang="de", translation=None, speaker=None)
    a = workshop.WorkshopAgent(log.session_id, tmp_path / "wk")

    with patch("app.process._call_claude_cli",
               AsyncMock(return_value="=== ZUSAMMENFASSUNG ===\nok")) as cli:
        asyncio.run(a._step())
    assert "Bildschirminhalte" not in cli.await_args_list[0].args[1]


# --- Bildschirm-Beobachtung ist bewusst abzuwählen ---------------------

def test_screen_watching_is_off_by_default(tmp_path):
    """Die Aufnahme erfasst den ganzen Bildschirm — das darf keine Voreinstellung sein."""
    async def go():
        log = meeting_log.start_session()
        log.append(channel="mic", text="x", lang="de", translation=None, speaker=None)
        with patch("app.screencap.ScreenWatcher") as W:
            r = workshop.start()
            assert r["ok"] is True
            W.assert_not_called()
        assert "screen" not in workshop.status()
        await workshop.stop()

    asyncio.run(go())


def test_screen_watching_starts_when_asked(tmp_path):
    async def go():
        log = meeting_log.start_session()
        log.append(channel="mic", text="x", lang="de", translation=None, speaker=None)
        with patch("app.screencap.screenshot_tool", return_value=["spectacle"]), \
             patch("app.screencap.ScreenWatcher") as W:
            W.return_value.run = AsyncMock()      # wird als Task gestartet
            W.return_value.status.return_value = {"shots": 0}
            r = workshop.start(watch_screen=True)
            W.assert_called_once()
            assert "screen" in r
        await workshop.stop()

    asyncio.run(go())
