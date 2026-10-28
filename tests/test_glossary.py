from __future__ import annotations

import pytest

import glossary

THAI = "แกนดัล์ฟ"


# --- the term itself --------------------------------------------------------


def test_actions_are_the_three_agreed():
    assert glossary.ACTIONS == ("keep", "transliterate", "translate")


def test_term_trims_its_fields():
    assert glossary.Term("  Gandalf ", " Transliterate ", f" {THAI} ") == glossary.Term(
        "Gandalf", "transliterate", THAI
    )


def test_keep_drops_the_thai():
    term = glossary.Term("NASA", "keep", "นาซา")
    assert term.th == ""


def test_empty_english_is_rejected():
    with pytest.raises(glossary.GlossaryError, match="`en`"):
        glossary.Term("   ", "keep")


def test_unknown_action_is_rejected():
    with pytest.raises(glossary.GlossaryError, match="unknown action"):
        glossary.Term("Gandalf", "romanise", THAI)


@pytest.mark.parametrize("action", ["transliterate", "translate"])
def test_spelled_actions_need_thai(action):
    with pytest.raises(glossary.GlossaryError, match="needs the Thai spelling"):
        glossary.Term("Gandalf", action, " ")


# --- writing ----------------------------------------------------------------


def test_render_round_trips():
    terms = [
        glossary.Term("Gandalf", "transliterate", THAI),
        glossary.Term("NASA", "keep"),
        glossary.Term("wizard", "translate", "พ่อมด"),
    ]
    assert glossary.parse(glossary.render(terms, title="Movie")) == terms


def test_render_explains_the_actions():
    text = glossary.render([glossary.Term("NASA", "keep")])
    assert "# Glossary" in text
    for action in glossary.ACTIONS:
        assert action in text


def test_render_names_the_work_and_keeps_thai_readable():
    text = glossary.render([glossary.Term("Gandalf", "transliterate", THAI)], title="Movie")
    assert text.startswith("# Glossary for Movie")
    assert THAI in text  # not \u0e41... escapes
    assert "\\u" not in text


def test_render_escapes_quotes_and_backslashes():
    awkward = glossary.Term('The "Hub" \\ base', "translate", 'ศูนย์ "กลาง"')
    assert glossary.parse(glossary.render([awkward])) == [awkward]


def test_empty_glossary_renders_a_valid_empty_file():
    text = glossary.render()
    assert "(no terms found)" in text
    assert glossary.parse(text) == []


# --- reading ----------------------------------------------------------------


def test_parse_reads_a_hand_written_file():
    text = """
# my notes
[[term]]
en = "Gandalf"
action = "transliterate"
th = "แกนดัล์ฟ"

[[term]]
en = "NASA"
action = "keep"
"""
    assert glossary.parse(text) == [
        glossary.Term("Gandalf", "transliterate", THAI),
        glossary.Term("NASA", "keep"),
    ]


def test_parse_reports_bad_toml():
    with pytest.raises(glossary.GlossaryError, match="not valid TOML"):
        glossary.parse("[[term]\nen =")


def test_parse_rejects_a_single_term_table():
    with pytest.raises(glossary.GlossaryError, match="\\[\\[term\\]\\]"):
        glossary.parse('[term]\nen = "NASA"\naction = "keep"\n')


def test_parse_reports_a_missing_action():
    with pytest.raises(glossary.GlossaryError, match="has no `action`"):
        glossary.parse('[[term]]\nen = "NASA"\n')


def test_parse_reports_a_field_of_the_wrong_type():
    with pytest.raises(glossary.GlossaryError, match="must be a string"):
        glossary.parse('[[term]]\nen = 5\naction = "keep"\n')


def test_parse_rejects_the_same_word_twice():
    text = """
[[term]]
en = "Gandalf"
action = "keep"

[[term]]
en = "gandalf"
action = "transliterate"
th = "แกนดัล์ฟ"
"""
    with pytest.raises(glossary.GlossaryError, match="repeats 'gandalf'"):
        glossary.parse(text)


def test_parse_warns_about_unknown_fields(caplog):
    text = '[[term]]\nen = "NASA"\naction = "keep"\nnote = "agency"\n'
    with caplog.at_level("WARNING"):
        terms = glossary.parse(text)
    assert terms == [glossary.Term("NASA", "keep")]
    assert "note" in caplog.text


# --- loading ----------------------------------------------------------------


def test_load_missing_file_is_empty(tmp_path):
    assert glossary.load(tmp_path / "nope.glossary.toml") == []


def test_load_reads_the_file(tmp_path):
    path = tmp_path / "Movie.glossary.toml"
    path.write_text(glossary.render([glossary.Term("NASA", "keep")]), encoding="utf-8")
    assert glossary.load(path) == [glossary.Term("NASA", "keep")]


def test_load_names_the_file_when_a_term_is_bad(tmp_path):
    path = tmp_path / "Movie.glossary.toml"
    path.write_text('[[term]]\nen = "NASA"\naction = "shout"\nth = "x"\n', encoding="utf-8")
    with pytest.raises(glossary.GlossaryError, match="Movie.glossary.toml"):
        glossary.load(path)


# --- helper -----------------------------------------------------------------


def test_unique_keeps_the_first_spelling():
    terms = [
        glossary.Term("Gandalf", "transliterate", THAI),
        glossary.Term("gandalf", "transliterate", "กันดาล์ฟ"),
        glossary.Term("NASA", "keep"),
    ]
    assert glossary.unique(terms) == [terms[0], terms[2]]
