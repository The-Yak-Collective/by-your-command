"""Tests for the per-command JSON state store (layout + atomic round-trip)."""

import pytest

from bot import state


def test_layout_and_roundtrip(tmp_path, monkeypatch):
    # Point the XDG state root at a throwaway directory for the duration of the test.
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    store = state.JSONStore("showmymode")

    # A never-written file does not "exist" and load() returns the default.
    assert not store.exists("modes.json")
    assert store.load("modes.json", default={"users": {}}) == {"users": {}}

    data = {"users": {"42": {"char": "🙊", "expires_at": 100}}}
    store.save("modes.json", data)

    # The file lands in the per-command subdirectory under the XDG state root.
    expected = tmp_path / "by-your-command" / "showmymode" / "modes.json"
    assert expected.is_file()
    assert store.exists("modes.json")

    # It round-trips exactly, with the emoji stored literally (not \uXXXX-escaped).
    assert store.load("modes.json") == data
    assert "🙊" in expected.read_text(encoding="utf-8")

    # The atomic write leaves no temp file behind.
    assert list(expected.parent.glob("*.tmp")) == []


def test_load_quarantines_corrupt_json(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    store = state.JSONStore("showmymode")

    # Simulate a partially-written or hand-mangled state file.
    store.dir.mkdir(parents=True, exist_ok=True)
    bad = store.dir / "modes.json"
    bad.write_text("{ this is not valid json", encoding="utf-8")

    # load() must not raise: it returns the default and moves the bad file aside so a
    # fresh file can be written, rather than failing on every run.
    assert store.load("modes.json", default={"users": {}}) == {"users": {}}
    assert not bad.exists()
    assert (store.dir / "modes.json.corrupt").is_file()


def test_rejects_path_traversal_in_command_name(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    for bad_name in ("../escape", "sub/dir", "..", "/abs"):
        with pytest.raises(ValueError):
            state.JSONStore(bad_name)


def test_rejects_path_traversal_in_filename(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    store = state.JSONStore("showmymode")
    for bad_name in ("../modes.json", "sub/modes.json", "..", "/abs.json"):
        with pytest.raises(ValueError):
            store.load(bad_name, default=None)
        with pytest.raises(ValueError):
            store.save(bad_name, {})
        with pytest.raises(ValueError):
            store.exists(bad_name)
