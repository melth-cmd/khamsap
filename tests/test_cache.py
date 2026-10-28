from __future__ import annotations

import json

import pytest

import cache
import llm


def make(tmp_path, name="Movie.cache.json", **kwargs) -> cache.Cache:
    return cache.Cache(tmp_path / name, model=kwargs.pop("model", "m/x"), **kwargs)


# --- round trip -------------------------------------------------------------


def test_missing_file_loads_empty(tmp_path):
    store = make(tmp_path).load()
    assert len(store) == 0


def test_put_then_save_then_load(tmp_path):
    store = make(tmp_path)
    store.put(1, "Hello.", "สวัสดี")
    store.save()

    reloaded = make(tmp_path).load()
    assert reloaded.get(1, "Hello.") == "สวัสดี"
    assert len(reloaded) == 1


def test_saved_file_is_readable_json_with_thai_intact(tmp_path):
    store = make(tmp_path)
    store.put(1, "Hello.", "สวัสดี")
    store.save()

    data = json.loads((tmp_path / "Movie.cache.json").read_text(encoding="utf-8"))
    assert data["version"] == cache.FORMAT_VERSION
    assert data["model"] == "m/x"
    assert data["prompt_version"] == llm.PROMPT_VERSION
    assert data["entries"]["1"] == {"source": "Hello.", "text": "สวัสดี"}
    assert "สวัสดี" in (tmp_path / "Movie.cache.json").read_text(encoding="utf-8")


def test_save_without_changes_does_not_rewrite(tmp_path):
    path = tmp_path / "Movie.cache.json"
    store = make(tmp_path)
    store.put(1, "Hello.", "สวัสดี")
    store.save()
    first = path.stat().st_mtime_ns

    store.save()  # nothing changed
    assert path.stat().st_mtime_ns == first


def test_save_leaves_no_temp_files(tmp_path):
    store = make(tmp_path)
    store.put(1, "Hello.", "สวัสดี")
    store.save()
    assert [p.name for p in tmp_path.iterdir()] == ["Movie.cache.json"]


# --- validity ---------------------------------------------------------------


def test_get_miss_returns_none(tmp_path):
    assert make(tmp_path).load().get(9, "anything") is None


def test_changed_source_invalidates_the_entry(tmp_path):
    store = make(tmp_path)
    store.put(1, "Hello.", "สวัสดี")
    store.save()
    assert make(tmp_path).load().get(1, "Hello, friend.") is None


def test_different_model_invalidates_everything(tmp_path, caplog):
    store = make(tmp_path, model="m/one")
    store.put(1, "Hello.", "สวัสดี")
    store.save()

    with caplog.at_level("INFO"):
        other = make(tmp_path, model="m/two").load()
    assert len(other) == 0
    assert "different model or prompt" in caplog.text


def test_different_prompt_version_invalidates_everything(tmp_path):
    store = make(tmp_path, prompt_version=1)
    store.put(1, "Hello.", "สวัสดี")
    store.save()
    assert len(make(tmp_path, prompt_version=2).load()) == 0


# --- damaged files ----------------------------------------------------------


def test_corrupt_json_is_a_warning_not_a_crash(tmp_path, caplog):
    (tmp_path / "Movie.cache.json").write_text("{not json", encoding="utf-8")
    with caplog.at_level("WARNING"):
        store = make(tmp_path).load()
    assert len(store) == 0
    assert "unreadable cache" in caplog.text


def test_unknown_format_version_is_ignored(tmp_path, caplog):
    (tmp_path / "Movie.cache.json").write_text('{"version": 99}', encoding="utf-8")
    with caplog.at_level("WARNING"):
        store = make(tmp_path).load()
    assert len(store) == 0
    assert "unknown format" in caplog.text


def test_bad_entry_values_are_skipped(tmp_path):
    payload = {
        "version": cache.FORMAT_VERSION,
        "model": "m/x",
        "prompt_version": llm.PROMPT_VERSION,
        "entries": {"1": {"source": "a", "text": "b"}, "2": {"source": 5}, "x": {}},
    }
    (tmp_path / "Movie.cache.json").write_text(json.dumps(payload), encoding="utf-8")
    store = make(tmp_path).load()
    assert len(store) == 1
    assert store.get(1, "a") == "b"


def test_contains(tmp_path):
    store = make(tmp_path)
    store.put(1, "Hello.", "สวัสดี")
    assert 1 in store
    assert 2 not in store
