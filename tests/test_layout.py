from __future__ import annotations

import pytest

import layout


# --- no work needed ---------------------------------------------------------


def test_single_line_source_is_untouched():
    result = layout.rewrap("Hello.", "สวัสดี")
    assert result.text == "สวัสดี"
    assert result.method is None
    assert not result.rewrapped


def test_correct_line_count_is_untouched():
    result = layout.rewrap("One.\nTwo.", "หนึ่ง\nสอง")
    assert result.text == "หนึ่ง\nสอง"
    assert result.method is None


def test_more_lines_than_source_are_reported_not_reflowed():
    result = layout.rewrap("One.\nTwo.", "หนึ่ง\nสอง\nสาม")
    assert result.text == "หนึ่ง\nสอง\nสาม"
    assert result.method == "extra_lines"
    assert not result.rewrapped


# --- rule 1: dashes ---------------------------------------------------------


def test_dash_speakers_are_split_at_the_dash():
    source = "-Like a Sam?\n-Oh, my God, he wishes."
    translated = "-แบบแซมเหรอ? -โอ๊ย พระเจ้า ฝันไปเถอะ"
    result = layout.rewrap(source, translated)
    assert result.text == "-แบบแซมเหรอ?\n-โอ๊ย พระเจ้า ฝันไปเถอะ"
    assert result.method == "dash"


def test_dash_rule_handles_three_speakers():
    source = "-A\n-B\n-C"
    result = layout.rewrap(source, "-หนึ่ง -สอง -สาม")
    assert result.text == "-หนึ่ง\n-สอง\n-สาม"
    assert result.method == "dash"


def test_hyphenated_words_are_not_split_points():
    source = "-Well-known fact.\n-Is it?"
    # No " -" boundary (the hyphen is inside a word), so the only split is the turn.
    result = layout.rewrap(source, "ข้อเท็จจริงที่รู้กันดี -จริงเหรอ")
    assert result.text == "-ข้อเท็จจริงที่รู้กันดี\n-จริงเหรอ"
    assert result.method == "dash"
    assert result.dashes_restored  # the model dropped the first speaker's dash


def test_dash_followed_by_space_is_not_a_boundary():
    source = "-One\n-Two"
    # "word - next" has a space after the dash, so it is a dash, not a turn.
    result = layout.rewrap(source, "หนึ่ง - สอง")
    assert result.method == "proportional"


def test_one_dashed_source_line_is_enough_for_the_dash_rule():
    # "But short?" / "-Yeah..." in the sample: only the second line is a turn.
    source = "Just a sentence.\n-Not a speaker turn."
    result = layout.rewrap(source, "ประโยคหนึ่ง -อีกอย่าง")
    assert result.text == "ประโยคหนึ่ง\n-อีกอย่าง"
    assert result.method == "dash"


def test_dash_rule_is_skipped_when_no_source_line_is_dashed():
    result = layout.rewrap("One.\nTwo.", "หนึ่ง -สอง")
    assert result.method == "proportional"


def test_en_and_em_dashes_are_speaker_turns():
    for dash in ("\u2013", "\u2014"):
        source = f"{dash}Hello.\n{dash}Hi."
        result = layout.rewrap(source, f"{dash}สวัสดี {dash}หวัดดี")
        assert result.text == f"{dash}สวัสดี\n{dash}หวัดดี"
        assert result.method == "dash"


def test_dash_hidden_behind_a_placeholder_still_counts():
    # <i>-Swear on your life.</i> / -Swear.  from the sample.
    source = "{0}-Swear on your life.{1}\n-Swear."
    result = layout.rewrap(source, "{0}-สาบานเลย{1} -สาบาน")
    assert result.text == "{0}-สาบานเลย{1}\n-สาบาน"
    assert result.method == "dash"


# --- restoring dashes the model dropped -------------------------------------


def test_lost_dash_is_restored_after_a_proportional_split():
    source = "-Hello there.\n-Hi."
    result = layout.rewrap(source, "สวัสดีครับ หวัดดี")
    assert result.text == "-สวัสดีครับ\n-หวัดดี"
    assert result.method == "proportional"
    assert result.dashes_restored


def test_lost_dash_is_restored_when_the_line_count_is_already_right():
    source = "-Hello.\n-Hi."
    result = layout.rewrap(source, "สวัสดี\nหวัดดี")
    assert result.text == "-สวัสดี\n-หวัดดี"
    assert result.method is None
    assert result.dashes_restored


def test_dash_style_follows_the_source_line():
    source = "-Hello.\n– Hi."  # first line tight, second spaced, en dash
    result = layout.rewrap(source, "สวัสดี หวัดดี")
    assert result.text == "-สวัสดี\n– หวัดดี"


def test_dash_goes_after_a_leading_placeholder():
    source = "{0}-Hello.{1}\n{2}-Hi.{3}"
    result = layout.rewrap(source, "{0}สวัสดี{1} {2}หวัดดี{3}")
    assert result.text == "{0}-สวัสดี{1}\n{2}-หวัดดี{3}"


def test_dash_hidden_behind_html_tags_counts_when_layout_runs_after_restore():
    # The pipeline restores <i> before layout, so the tag is back in the text.
    source = "{0}-Swear on your life.{1}\n-Swear."
    result = layout.rewrap(source, "<i>สาบานเลย</i> สาบาน")
    assert result.text == "<i>-สาบานเลย</i>\n-สาบาน"


def test_dash_is_not_inserted_outside_the_html_tag():
    source = "-Hello.\n-Hi."
    result = layout.rewrap(source, "<i>สวัสดี</i>\n<i>หวัดดี</i>")
    assert result.text == "<i>-สวัสดี</i>\n<i>-หวัดดี</i>"


def test_dashes_are_not_invented_when_only_some_source_lines_have_one():
    source = "Just a sentence.\n-Not a speaker turn."
    result = layout.rewrap(source, "ประโยคหนึ่ง อีกอย่าง")
    assert result.text == "ประโยคหนึ่ง\nอีกอย่าง"
    assert not result.dashes_restored


def test_extra_lines_never_restore_dashes():
    source = "-One.\n-Two."
    result = layout.rewrap(source, "หนึ่ง\nสอง\nสาม")
    assert result.method == "extra_lines"
    assert not result.dashes_restored


def test_dash_rule_skips_when_count_mismatches():
    source = "-A\n-B\n-C"
    result = layout.rewrap(source, "-หนึ่ง -สอง")
    assert result.method == "proportional"
    assert result.text.count("\n") == 2


# --- rule 2: proportional ---------------------------------------------------


def test_proportional_uses_the_source_break_fraction():
    source = "Short.\nA much longer second line."
    translated = "สั้น แต่ว่าบรรทัดที่สองยาวกว่ามากทีเดียวนะ"
    result = layout.rewrap(source, translated)
    assert result.method == "proportional"
    first, second = result.text.split("\n")
    # First source line is about a quarter of the text, so the split lands early.
    assert len(first) < len(second)


def test_proportional_splits_three_lines():
    source = "One.\nTwo.\nThree."
    result = layout.rewrap(source, "หนึ่ง สอง สาม สี่")
    assert result.text.count("\n") == 2
    assert result.method == "proportional"


def test_proportional_falls_back_to_character_split_without_spaces():
    source = "abcdefghij\nklmnopqrst"
    result = layout.rewrap(source, "ก" * 20)
    assert result.text.count("\n") == 1
    assert len(result.text.replace("\n", "")) == 20


def test_proportional_never_returns_empty_pieces():
    source = "A.\nB."
    result = layout.rewrap(source, "ก")
    assert result.text.strip()
    assert "\n\n" not in result.text


def test_trailing_whitespace_is_trimmed_from_pieces():
    source = "One.\nTwo."
    result = layout.rewrap(source, "หนึ่ง  สอง")
    assert result.text == "หนึ่ง\nสอง"


# --- blank lines and unbreakable spans --------------------------------------


def test_blank_lines_do_not_count_as_lines():
    assert layout.source_line_count("One.\n\nTwo.") == 2


def test_entry_with_a_blank_line_is_rewrapped_to_its_real_line_count():
    result = layout.rewrap("One.\n\nTwo.", "หนึ่ง สอง")
    assert result.text == "หนึ่ง\nสอง"
    assert result.method == "proportional"


def test_split_does_not_land_inside_a_bracket():
    source = "Here is a sign.\n[SIGN: closed] Come back tomorrow."
    translated = "นี่คือป้าย [SIGN: closed] กลับมาพรุ่งนี้"
    result = layout.rewrap(source, translated)
    assert result.method == "proportional"
    for line in result.text.split("\n"):
        assert line.count("[") == line.count("]")


def test_split_does_not_land_inside_a_placeholder_pair():
    source = "A long first half here.\nShort."
    translated = "{0}ครึ่งแรกยาวมาก{1} {2}สั้น{3}"
    result = layout.rewrap(source, translated)
    for line in result.text.split("\n"):
        assert line.count("{") == line.count("}")


def test_split_does_not_land_inside_a_music_marker():
    source = "A long first line of a song.\nLa la la."
    translated = "\u266a ลา ลา ลา \u266a ลา ลา ลา"
    result = layout.rewrap(source, translated)
    lines = result.text.split("\n")
    assert len(lines) == 2
    assert any(line.count("\u266a") == 2 for line in lines)


# --- helpers ----------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [("One.", 1), ("One.\nTwo.", 2), ("A\nB\nC", 3), ("A\n\n\nB", 2)],
)
def test_source_line_count(text, expected):
    assert layout.source_line_count(text) == expected
