import json
import time
from pathlib import Path

import pytest

from app import meeting_log


@pytest.fixture(autouse=True)
def isolated_dir(tmp_path, monkeypatch):
    """Jeder Test schreibt in ein eigenes Verzeichnis."""
    monkeypatch.setattr(meeting_log, "_BASE_DIR", tmp_path / "meetings")
    meeting_log.close_session()
    yield tmp_path / "meetings"
    meeting_log.close_session()


def test_start_session_creates_jsonl():
    log = meeting_log.start_session()
    assert log.path.exists()
    assert log.path.suffix == ".jsonl"
    assert log.session_id in log.path.name


def test_append_writes_one_line_per_entry():
    log = meeting_log.start_session()
    log.append(channel="mic", text="Hallo Welt", lang="de", translation=None, speaker=None)
    log.append(channel="desktop", text="Hello world", lang="en", translation="Hallo Welt",
               speaker="Sprecher 1")
    lines = log.path.read_text().strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["text"] == "Hallo Welt"
    assert first["channel"] == "mic"
    assert first["lang"] == "de"
    assert "t" in first and "elapsed" in first


def test_append_flushes_immediately_survives_crash():
    """Ohne close() muss der Text schon auf Platte sein (Absturzsicherheit)."""
    log = meeting_log.start_session()
    log.append(channel="mic", text="ueberlebt", lang="de", translation=None, speaker=None)
    content = Path(log.path).read_text()
    assert "ueberlebt" in content


def test_append_ignores_empty_text():
    log = meeting_log.start_session()
    log.append(channel="mic", text="   ", lang="de", translation=None, speaker=None)
    log.append(channel="mic", text="", lang="de", translation=None, speaker=None)
    assert log.path.read_text().strip() == ""


def test_entries_returns_chronological_across_channels():
    log = meeting_log.start_session()
    log.append(channel="mic", text="erst", lang="de", translation=None, speaker=None)
    time.sleep(0.01)
    log.append(channel="desktop", text="dann", lang="de", translation=None, speaker=None)
    texts = [e["text"] for e in log.entries()]
    assert texts == ["erst", "dann"]


def test_to_markdown_merges_both_channels_with_labels():
    log = meeting_log.start_session()
    log.append(channel="mic", text="Ich sage was", lang="de", translation=None, speaker=None)
    log.append(channel="desktop", text="They say something", lang="en",
               translation="Sie sagen etwas", speaker="Sprecher 2")
    md = log.to_markdown()
    assert md.startswith("# Meeting")
    assert "Ich sage was" in md
    assert "They say something" in md
    assert "Sie sagen etwas" in md
    assert "Mikrofon" in md
    assert "Unterhaltung" in md


def test_close_session_writes_markdown_file():
    log = meeting_log.start_session()
    log.append(channel="mic", text="Abschluss", lang="de", translation=None, speaker=None)
    meeting_log.close_session()
    md_path = log.path.with_suffix(".md")
    assert md_path.exists()
    assert "Abschluss" in md_path.read_text()


def test_close_session_without_entries_writes_no_markdown():
    log = meeting_log.start_session()
    meeting_log.close_session()
    assert not log.path.with_suffix(".md").exists()


def test_current_returns_active_session_and_none_after_close():
    assert meeting_log.current() is None
    log = meeting_log.start_session()
    assert meeting_log.current() is log
    meeting_log.close_session()
    assert meeting_log.current() is None


def test_start_session_twice_keeps_first_until_closed():
    """Idempotent: ein zweiter Start darf ein laufendes Meeting nicht abschneiden."""
    log1 = meeting_log.start_session()
    log1.append(channel="mic", text="laeuft", lang="de", translation=None, speaker=None)
    log2 = meeting_log.start_session()
    assert log2 is log1
    assert len(log1.entries()) == 1


def test_list_sessions_newest_first():
    a = meeting_log.start_session()
    a.append(channel="mic", text="a", lang="de", translation=None, speaker=None)
    meeting_log.close_session()
    time.sleep(1.1)  # Session-ID hat Sekundenaufloesung
    b = meeting_log.start_session()
    b.append(channel="mic", text="b", lang="de", translation=None, speaker=None)
    meeting_log.close_session()

    sessions = meeting_log.list_sessions()
    assert len(sessions) == 2
    assert sessions[0]["session_id"] == b.session_id
    assert sessions[0]["entries"] == 1


def test_load_session_reads_finished_session():
    log = meeting_log.start_session()
    log.append(channel="mic", text="gespeichert", lang="de", translation=None, speaker=None)
    sid = log.session_id
    meeting_log.close_session()

    loaded = meeting_log.load_session(sid)
    assert loaded is not None
    assert [e["text"] for e in loaded.entries()] == ["gespeichert"]


def test_load_session_rejects_path_traversal():
    assert meeting_log.load_session("../../etc/passwd") is None
    assert meeting_log.load_session("foo/bar") is None


def test_corrupt_line_does_not_break_reading():
    """Halbe Zeile nach Stromausfall darf den Rest nicht unlesbar machen."""
    log = meeting_log.start_session()
    log.append(channel="mic", text="gut", lang="de", translation=None, speaker=None)
    with open(log.path, "a") as f:
        f.write('{"t": "kaputt"\n')  # abgeschnittene Zeile
    log.append(channel="mic", text="danach", lang="de", translation=None, speaker=None)
    texts = [e["text"] for e in log.entries()]
    assert texts == ["gut", "danach"]
