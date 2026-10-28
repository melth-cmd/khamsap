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
    # No " -" boundary (the hyphen is inside a word), so no dash split is possible.
    result = layout.rewrap(source, "ข้อเท็จจริงที่รู้กันดี -จริงเหรอ")
    assert result.text == "ข้อเท็จจริงที่รู้กันดี\n-จริงเหรอ"
    assert result.method == "dash"


def test_dash_followed_by_space_is_not_a_boundary():
    source = "-One\n-Two"
    # "word - next" has a space after the dash, so it is a dash, not a turn.
    result = layout.rewrap(source, "หนึ่ง - สอง")
    assert result.method == "proportional"


def test_dash_rule_requires_all_source_lines_dashed():
    source = "Just a sentence.\n-Not a speaker turn."
    result = layout.rewrap(source, "ประโยคหนึ่ง -อีกอย่าง")
    assert result.method == "proportional"


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


# --- helpers ----------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [("One.", 1), ("One.\nTwo.", 2), ("A\nB\nC", 3)],
)
def test_source_line_count(text, expected):
    assert layout.source_line_count(text) == expected
