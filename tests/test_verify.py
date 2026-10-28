from __future__ import annotations

from dataclasses import dataclass, field

import glossary
import pipeline
import srtio
import verify

from conftest import FakeClient


def term(en: str, action: str = "keep", th: str = "") -> glossary.Term:
    return glossary.Term(en, action, th)


GANDALF = ["Gandalf", "transliterate", "แกนดัล์ฟ"]
NASA = ["NASA", "keep"]
WIZARD = ["wizard", "translate", "พ่อมด"]


# --- small helpers ----------------------------------------------------------


def test_scrub_removes_tags_placeholders_and_music():
    assert verify.scrub("<i>Hello {0}</i> ♪") == "Hello  "


def test_content_length_ignores_spaces_tags_and_placeholders():
    assert verify.content_length("สวัสดี {0}<i>ครับ</i>") == len("สวัสดีครับ")


def test_content_length_of_empty_markup_is_zero():
    assert verify.content_length("  {0}<i></i>  ") == 0


def test_english_words_finds_latin_words_once():
    assert verify.english_words("สวัสดี world, World!") == ["world"]


def test_english_words_keeps_apostrophes_inside_words():
    assert verify.english_words("don't stop") == ["don't", "stop"]


def test_english_words_ignores_tags_and_placeholders():
    assert verify.english_words("<i>ok</i> {0}") == ["ok"]


def test_english_words_returns_nothing_for_thai():
    assert verify.english_words("สวัสดีครับ") == []


# --- tag mismatch -----------------------------------------------------------


def test_tag_mismatch_is_none_when_tags_match():
    assert verify.tag_mismatch("<i>One.</i>", "<i>หนึ่ง</i>") is None


def test_tag_mismatch_reports_a_missing_tag():
    problem = verify.tag_mismatch("<i>Wait.</i>", "รอ")
    assert problem is not None
    assert "missing" in problem and "<i>" in problem and "</i>" in problem


def test_tag_mismatch_reports_extra_and_invented_placeholders():
    problem = verify.tag_mismatch("Wait.", "รอ {3}")
    assert problem is not None
    assert "extra {3}" in problem


def test_tag_mismatch_shows_a_second_copy_as_extra():
    problem = verify.tag_mismatch("<i>Wait.</i>", "<i>รอ<i>")
    assert problem is not None
    assert "missing </i>" in problem
    assert "extra <i>" in problem


# --- glossary ---------------------------------------------------------------


def test_glossary_keep_present_passes():
    assert verify.glossary_flags(1, "NASA is here.", "NASA อยู่ที่นี่", [term(*NASA)]) == []


def test_glossary_keep_missing_is_flagged():
    flags = verify.glossary_flags(1, "NASA is here.", "องค์กรอยู่ที่นี่", [term(*NASA)])
    assert [flag.kind for flag in flags] == ["glossary_missing"]
    assert "NASA" in flags[0].message


def test_glossary_transliteration_present_passes():
    flags = verify.glossary_flags(
        1, "Gandalf is here.", "แกนดัล์ฟอยู่ที่นี่", [term(*GANDALF)]
    )
    assert flags == []


def test_glossary_term_left_in_english_is_inconsistent():
    flags = verify.glossary_flags(
        1, "Gandalf is here.", "Gandalf อยู่ที่นี่", [term(*GANDALF)]
    )
    assert [flag.kind for flag in flags] == ["glossary_inconsistent"]
    assert "แกนดัล์ฟ" in flags[0].message


def test_glossary_translation_absent_is_flagged_missing():
    flags = verify.glossary_flags(1, "The wizard spoke.", "เขาพูด", [term(*WIZARD)])
    assert [flag.kind for flag in flags] == ["glossary_missing"]


def test_glossary_term_not_in_the_source_is_ignored():
    assert verify.glossary_flags(1, "Hello.", "สวัสดี", [term(*GANDALF)]) == []


def test_glossary_check_reports_the_entry_it_belongs_to():
    flags = verify.glossary_flags(7, "NASA is here.", "อยู่ที่นี่", [term(*NASA)])
    assert flags[0].index == 7
    assert flags[0].source == "NASA is here."


# --- leftover English -------------------------------------------------------


def test_leftover_english_is_flagged():
    flags = verify.leftover_english(1, "Hello world.", "สวัสดี world", [])
    assert [flag.kind for flag in flags] == ["english_left"]
    assert "world" in flags[0].message


def test_leftover_english_covered_by_a_term_is_left_to_the_glossary_check():
    flags = verify.leftover_english(1, "NASA is here.", "NASA อยู่", [term(*NASA)])
    assert flags == []


def test_leftover_english_ignores_words_inside_tags():
    assert verify.leftover_english(1, "Hello.", "<i>สวัสดี</i>", []) == []


def test_leftover_english_lists_each_word_once():
    flags = verify.leftover_english(1, "Hello.", "สวัสดี ok OK", [])
    assert "ok" in flags[0].message
    assert "OK" not in flags[0].message


# --- length -----------------------------------------------------------------


def test_a_much_too_short_translation_is_flagged():
    flags = verify.length_flags(1, "Hello there, my good friend.", "อือ")
    assert [flag.kind for flag in flags] == ["short"]


def test_a_much_too_long_translation_is_flagged():
    flags = verify.length_flags(1, "Okay.", "ฉันเข้าใจดีมากเลยจริงๆนะครับ")
    assert [flag.kind for flag in flags] == ["long"]


def test_a_normal_length_is_not_flagged():
    assert verify.length_flags(1, "How are you?", "สบายดีไหม") == []


def test_a_very_short_source_is_not_compared():
    assert verify.length_flags(1, "Hi", "สวัสดีค่ะ") == []


def test_an_empty_translation_is_not_measured():
    assert verify.length_flags(1, "Hello there.", "") == []


# --- check_entry ------------------------------------------------------------


def test_a_good_entry_has_no_flags():
    assert verify.check_entry(1, "Hello there.", "สวัสดีครับ") == []


def test_a_failed_entry_is_the_only_flag():
    flags = verify.check_entry(
        1, "Hello there.", "Hello there.", ok=False, reason="missing_thai"
    )
    assert [flag.kind for flag in flags] == ["failed"]
    assert flags[0].message == "left in English (missing_thai)"
    assert flags[0].translation == "Hello there."


def test_a_failed_entry_without_a_reason_still_says_it_is_english():
    flags = verify.check_entry(1, "Hello.", "Hello.", ok=False)
    assert flags[0].message == "left in English"


def test_an_empty_translation_is_flagged():
    assert [flag.kind for flag in verify.check_entry(1, "Hello.", " {0} ")] == ["empty"]


def test_a_non_verbal_source_is_left_alone():
    assert verify.check_entry(1, "Mm-hmm.", "Mm-hmm.") == []
    assert verify.check_entry(1, "[MUSIC PLAYING]", "[MUSIC PLAYING]") == []


def test_a_non_verbal_source_may_be_translated():
    assert verify.check_entry(1, "Mm-hmm.", "อืม") == []


def test_a_changed_line_count_is_flagged():
    flags = verify.check_entry(1, "One.\nTwo.", "หนึ่งสอง")
    assert [flag.kind for flag in flags] == ["line_count"]


def test_extra_lines_are_flagged_too():
    flags = verify.check_entry(1, "One.\nTwo.", "หนึ่ง\nสอง\nสาม")
    assert [flag.kind for flag in flags] == ["line_count"]


def test_a_lost_speaker_dash_is_flagged():
    flags = verify.check_entry(1, "- Hello.", "สวัสดี")
    assert [flag.kind for flag in flags] == ["dashes_lost"]


def test_a_kept_speaker_dash_is_not_flagged():
    assert verify.check_entry(1, "- Hello.", "- สวัสดี") == []


def test_a_dash_hidden_behind_a_tag_still_counts():
    assert verify.check_entry(1, "<i>- Hello.</i>", "<i>- สวัสดี</i>") == []


def test_an_entry_can_flag_several_things_in_kind_order():
    flags = verify.check_entry(1, "One.\nTwo.", "หนึ่ง English")
    kinds = [flag.kind for flag in flags]
    assert kinds == sorted(kinds, key=verify.KINDS.index)
    assert "line_count" in kinds and "english_left" in kinds


def test_flags_carry_their_entry_context():
    flag = verify.check_entry(1, "- Hello.", "สวัสดี")[0]
    assert flag.index == 1
    assert flag.source == "- Hello."
    assert flag.translation == "สวัสดี"


# --- verify_file ------------------------------------------------------------


@dataclass
class FakeResult:
    index: int
    text: str
    ok: bool = True
    reason: str | None = None


@dataclass
class FakeFile:
    results: list[FakeResult] = field(default_factory=list)
    sources: dict[int, str] = field(default_factory=dict)


def test_verify_file_checks_every_entry():
    translated = FakeFile(
        results=[FakeResult(1, "สวัสดี"), FakeResult(2, "ลาก่อน")],
        sources={1: "Hello.", 2: "Goodbye."},
    )
    verification = verify.verify_file(translated)
    assert verification.checked == 2
    assert verification.flags == []


def test_verify_file_skips_results_without_a_source():
    translated = FakeFile(results=[FakeResult(1, "สวัสดี")], sources={})
    verification = verify.verify_file(translated)
    assert verification.checked == 0
    assert verification.flags == []


def test_verify_file_sorts_by_entry_then_kind():
    translated = FakeFile(
        results=[
            FakeResult(2, "สอง", ok=False, reason="transport"),
            FakeResult(1, "หนึ่ง English", ok=True),
        ],
        sources={1: "One.\nTwo.", 2: "Two."},
    )
    verification = verify.verify_file(translated)
    assert verification.entries == [1, 2]
    assert verification.flags[0].index == 1
    assert [flag.kind for flag in verification.for_index(2)] == ["failed"]


def test_verification_counts_flags_by_kind():
    translated = FakeFile(
        results=[FakeResult(1, "สวัสดี"), FakeResult(2, "Hello.", ok=False)],
        sources={1: "Hello.", 2: "Goodbye."},
    )
    verification = verify.verify_file(translated)
    assert verification.by_kind == {"failed": 1}


def test_verify_file_only_uses_the_terms_it_is_given():
    translated = FakeFile(
        results=[FakeResult(1, "Gandalf อยู่ที่นี่")],
        sources={1: "Gandalf is here."},
    )
    # With no glossary there is no "keep" rule, so the name is just English.
    assert [flag.kind for flag in verify.verify_file(translated).flags] == ["english_left"]
    flagged = verify.verify_file(translated, [term(*GANDALF)])
    assert [flag.kind for flag in flagged.flags] == ["glossary_inconsistent"]


# --- end to end through the pipeline ---------------------------------------


SRT = """1
00:00:01,000 --> 00:00:02,000
<i>Wait for me.</i>
"""


def one_subtitle():
    subtitles, _ = srtio.parse_subtitles(SRT)
    return subtitles


def test_pipeline_keeps_the_source_of_every_result():
    result = pipeline.translate_subtitles(
        FakeClient(["[1] {0}รอด้วย{1}"]), one_subtitle(), sleep=lambda seconds: None
    )
    assert result.sources == {1: "<i>Wait for me.</i>"}
    assert result.results[0].text == "<i>รอด้วย</i>"


def test_pipeline_result_verifies_clean():
    result = pipeline.translate_subtitles(
        FakeClient(["[1] {0}รอด้วย{1}"]), one_subtitle(), sleep=lambda seconds: None
    )
    verification = verify.verify_file(result)
    assert verification.checked == 1
    assert verification.flags == []


def test_pipeline_failure_is_verified_as_failed():
    subtitles, _ = srtio.parse_subtitles(
        "1\n00:00:01,000 --> 00:00:02,000\nHello.\n"
    )
    result = pipeline.translate_subtitles(
        FakeClient(["[1] 等等"]),
        subtitles,
        max_retries=0,
        sleep=lambda seconds: None,
    )
    verification = verify.verify_file(result)
    assert verification.by_kind == {"failed": 1}
    assert "missing_thai" in verification.flags[0].message


def test_pipeline_strip_hi_source_is_the_cleaned_text():
    subtitles, _ = srtio.parse_subtitles(
        "1\n00:00:01,000 --> 00:00:02,000\nHello. [door slams]\n"
    )
    result = pipeline.translate_subtitles(
        FakeClient(["[1] สวัสดี"]),
        subtitles,
        strip_hi=True,
        sleep=lambda seconds: None,
    )
    assert result.sources == {1: "Hello."}


def test_pipeline_keeps_sources_for_cached_entries(tmp_path):
    source = tmp_path / "movie.en.srt"
    source.write_text(SRT, encoding="utf-8")

    first = pipeline.translate_file(
        FakeClient(["[1] {0}รอด้วย{1}"]), source, sleep=lambda seconds: None
    )
    assert first[0].sources == {1: "<i>Wait for me.</i>"}

    # Nothing queued: the second run must be served entirely from the cache.
    second = pipeline.translate_file(FakeClient(), source, sleep=lambda seconds: None)
    assert second[0].sources == {1: "<i>Wait for me.</i>"}
    assert verify.verify_file(second[0]).flags == []
