from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
import srt

import pipeline
import srtio
from batcher import SourceEntry

FIXTURES = Path(__file__).parent / "fixtures"


def subtitle(index: int, content: str, start_s: int = 1) -> srt.Subtitle:
    start = dt.timedelta(seconds=start_s)
    return srt.Subtitle(index=index, start=start, end=start + dt.timedelta(seconds=2), content=content)


def run(client, subtitles, **kwargs):
    kwargs.setdefault("sleep", lambda seconds: None)
    return pipeline.translate_subtitles(client, subtitles, **kwargs)


# --- output_path_for --------------------------------------------------------


def test_output_path_strips_en_suffix():
    assert srtio.output_path_for("Movie (2026).en.srt") == Path("Movie (2026).th.srt")


def test_output_path_without_en_suffix():
    assert srtio.output_path_for("Movie (2026).srt") == Path("Movie (2026).th.srt")


def test_output_path_is_case_insensitive():
    assert srtio.output_path_for("Movie.EN.SRT") == Path("Movie.th.srt")


def test_output_path_keeps_directory():
    assert srtio.output_path_for("/films/a.en.srt") == Path("/films/a.th.srt")


def test_output_path_for_other_extension():
    assert srtio.output_path_for("notes.txt") == Path("notes.th.srt")


# --- prepare ----------------------------------------------------------------


def test_prepare_protects_tags():
    prepared = pipeline.prepare([subtitle(1, "<i>Hello.</i>")])
    assert prepared.entries == [SourceEntry(1, "{0}Hello.{1}")]
    assert prepared.dropped == []


def test_prepare_drops_hi_only_entries_when_stripping():
    prepared = pipeline.prepare([subtitle(1, "[door slams]"), subtitle(2, "Hello.")], strip_hi=True)
    assert [e.index for e in prepared.entries] == [2]
    assert prepared.dropped == [1]


def test_prepare_keeps_hi_entries_by_default():
    prepared = pipeline.prepare([subtitle(1, "[door slams]")])
    assert [e.index for e in prepared.entries] == [1]


def test_prepare_drops_entries_with_nothing_after_tag_stripping():
    prepared = pipeline.prepare([subtitle(1, "<i></i>")])
    assert prepared.entries == []
    assert prepared.dropped == [1]


# --- end to end -------------------------------------------------------------


def test_translates_a_file_and_keeps_timestamps(tmp_path):
    from conftest import FakeClient

    client = FakeClient(["[1] สวัสดี\n[2] สบายดีไหม\n[3] {0}สบายดี ขอบคุณ{1}"])
    loaded = srtio.load_srt(FIXTURES / "simple.srt")
    result = run(client, loaded.subtitles)

    assert [s.content for s in result.subtitles] == [
        "สวัสดี",
        "สบายดีไหม",
        "<i>สบายดี ขอบคุณ</i>",
    ]
    assert [s.index for s in result.subtitles] == [1, 2, 3]
    assert [s.start for s in result.subtitles] == [s.start for s in loaded.subtitles]
    assert result.failures == []


def test_end_to_end_write_and_read_back(tmp_path):
    from conftest import FakeClient

    target = tmp_path / "out.srt"
    client = FakeClient(["[1] สวัสดี\n[2] สบายดีไหม\n[3] {0}สบายดี{1}"])
    result, path = pipeline.translate_file(
        client, FIXTURES / "simple.srt", target, sleep=lambda s: None
    )
    assert path == target

    reloaded = srtio.load_srt(target)
    assert [s.content for s in reloaded.subtitles][0] == "สวัสดี"


def test_tags_are_restored_even_for_failed_entries():
    from conftest import FakeClient

    client = FakeClient(["[1] 你好"] * 4)
    result = run(client, [subtitle(1, "<i>Hello.</i>")], max_retries=3)

    assert result.failures[0].ok is False
    assert result.subtitles[0].content == "<i>Hello.</i>"  # English kept, tags intact


def test_hi_dropped_entries_disappear_from_the_output():
    from conftest import FakeClient

    client = FakeClient(["[2] สวัสดี"])
    subtitles = [subtitle(1, "[door slams]"), subtitle(2, "Hello.")]
    result = run(client, subtitles, strip_hi=True)

    assert [s.index for s in result.subtitles] == [2]
    assert result.dropped == [1]


def test_nothing_to_translate_makes_no_calls():
    from conftest import FakeClient

    client = FakeClient()
    result = run(client, [subtitle(1, "[door slams]")], strip_hi=True)
    assert client.call_count == 0
    assert result.subtitles == []


def test_usage_is_summed_across_batches():
    from conftest import FakeClient
    from llm import Completion, Usage

    client = FakeClient(
        [Completion("[1] หนึ่ง", Usage(10, 5)), Completion("[2] สอง", Usage(3, 2))]
    )
    subtitles = [subtitle(1, "One."), subtitle(2, "Two.")]
    result = run(client, subtitles, size=1)

    assert client.call_count == 2
    assert result.usage.prompt_tokens == 13
    assert result.usage.completion_tokens == 7


def test_second_batch_context_carries_thai():
    from conftest import FakeClient

    client = FakeClient(["[1] หนึ่ง", "[2] สอง"])
    subtitles = [subtitle(1, "One."), subtitle(2, "Two.")]
    run(client, subtitles, size=1)

    second_prompt = client.calls[1][1]["content"]
    assert "TH: หนึ่ง" in second_prompt


def test_results_are_sorted_by_index():
    from conftest import FakeClient

    client = FakeClient(["[2] สอง\n[1] หนึ่ง"])
    result = run(client, [subtitle(1, "One."), subtitle(2, "Two.")])
    assert [r.index for r in result.results] == [1, 2]


def test_output_encoding_with_bom(tmp_path):
    from conftest import FakeClient

    target = tmp_path / "out.srt"
    client = FakeClient(["[1] สวัสดี\n[2] สอง\n[3] {0}สาม{1}"])
    pipeline.translate_file(
        client, FIXTURES / "simple.srt", target, encoding="utf-8-sig", sleep=lambda s: None
    )
    assert target.read_bytes().startswith(b"\xef\xbb\xbf")


def test_failure_list_is_exposed_for_the_report():
    from conftest import FakeClient

    client = FakeClient(["[1] สวัสดี\n[2] 你好"] * 2)
    result = run(client, [subtitle(1, "One."), subtitle(2, "Two.")], max_retries=1)
    assert [f.index for f in result.failures] == [2]
    assert result.failures[0].reason in {"foreign_script", "missing_thai"}


# --- cache / resume ---------------------------------------------------------


REPLIES = "[1] สวัสดี\n[2] สบายดีไหม\n[3] {0}สบายดี{1}"


def copied_fixture(tmp_path, name="movie.en.srt") -> Path:
    target = tmp_path / name
    target.write_bytes((FIXTURES / "simple.srt").read_bytes())
    return target


def test_cache_file_is_written_next_to_the_source(tmp_path):
    from conftest import FakeClient

    source = copied_fixture(tmp_path)
    pipeline.translate_file(FakeClient([REPLIES]), source, sleep=lambda s: None)
    assert (tmp_path / "movie.cache.json").exists()
    assert (tmp_path / "movie.th.srt").exists()


def test_second_run_makes_no_calls(tmp_path):
    from conftest import FakeClient

    source = copied_fixture(tmp_path)
    first = FakeClient([REPLIES])
    pipeline.translate_file(first, source, sleep=lambda s: None)
    assert first.call_count == 1

    second = FakeClient()  # would explode if anything were requested
    result, _ = pipeline.translate_file(second, source, sleep=lambda s: None)
    assert second.call_count == 0
    assert [s.content for s in result.subtitles] == [
        "สวัสดี",
        "สบายดีไหม",
        "<i>สบายดี</i>",
    ]
    assert all(entry.from_cache for entry in result.results)


def test_cache_can_be_disabled(tmp_path):
    from conftest import FakeClient

    source = copied_fixture(tmp_path)
    pipeline.translate_file(FakeClient([REPLIES]), source, use_cache=False, sleep=lambda s: None)
    assert not (tmp_path / "movie.cache.json").exists()


def test_only_uncached_entries_are_requested(tmp_path):
    from conftest import FakeClient
    import cache as cache_module

    source = copied_fixture(tmp_path)
    store = cache_module.Cache(tmp_path / "movie.cache.json", model="m/x")
    store.put(1, "Hello there.", "สวัสดี")
    store.put(2, "How are you?", "สบายดีไหม")
    store.save()

    client = FakeClient(["[3] {0}สบายดี{1}"])
    result, _ = pipeline.translate_file(client, source, model="m/x", sleep=lambda s: None)

    assert client.call_count == 1
    prompt = client.calls[0][1]["content"]
    assert "[TRANSLATE]\n[3] {0}Fine, thanks.{1}" in prompt
    assert "[1] Hello there." not in prompt.split("[TRANSLATE]")[1]
    assert result.failures == []


def test_cached_thai_is_used_as_context(tmp_path):
    from conftest import FakeClient
    import cache as cache_module

    source = copied_fixture(tmp_path)
    store = cache_module.Cache(tmp_path / "movie.cache.json", model="m/x")
    store.put(1, "Hello there.", "สวัสดี")
    store.save()

    client = FakeClient(["[2] สบายดีไหม\n[3] {0}สบายดี{1}"])
    pipeline.translate_file(client, source, model="m/x", sleep=lambda s: None, size=5)
    prompt = client.calls[0][1]["content"]
    assert "TH: สวัสดี" in prompt


def test_failed_entries_are_not_cached(tmp_path):
    from conftest import FakeClient
    import cache as cache_module

    source = copied_fixture(tmp_path)
    client = FakeClient(["[1] สวัสดี\n[2] 你好\n[3] {0}สบายดี{1}"])
    client.queue("[2] 你好", "[2] 你好", "[2] 你好")
    pipeline.translate_file(client, source, model="m/x", sleep=lambda s: None)

    store = cache_module.Cache(tmp_path / "movie.cache.json", model="m/x").load()
    assert 1 in store and 3 in store
    assert 2 not in store


def test_changed_source_line_is_retranslated(tmp_path):
    from conftest import FakeClient

    source = copied_fixture(tmp_path)
    pipeline.translate_file(FakeClient([REPLIES]), source, sleep=lambda s: None)

    edited = source.read_text(encoding="utf-8").replace("How are you?", "Where were you?")
    source.write_text(edited, encoding="utf-8")

    client = FakeClient(["[2] ไปไหนมา"])
    result, _ = pipeline.translate_file(client, source, sleep=lambda s: None)
    assert client.call_count == 1
    assert result.translated[2] == "ไปไหนมา"
    assert result.translated[1] == "สวัสดี"
