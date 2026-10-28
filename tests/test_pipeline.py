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
