"""Tests for the per-command JSON state store (layout + atomic round-trip)."""

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
