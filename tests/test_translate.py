from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import config
import glossary
import srtio
import translate
from conftest import FakeClient

FIXTURES = Path(__file__).parent / "fixtures"
REPLIES = "[1] สวัสดี\n[2] สบายดีไหม\n[3] {0}สบายดี{1}"


@pytest.fixture
def project(tmp_path) -> Path:
    shutil.copy(FIXTURES / "simple.srt", tmp_path / "sample.en.srt")
    (tmp_path / "config.toml").write_text('[llm]\nmodel = "test/model"\n', encoding="utf-8")
    return tmp_path


@pytest.fixture
def fake(monkeypatch) -> FakeClient:
    client = FakeClient()
    monkeypatch.setattr(config, "build_client", lambda settings, api_key=None: client)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    return client


def test_translates_one_file(project, fake, capsys):
    fake.queue(REPLIES)
    status = translate.main(
        [str(project / "sample.en.srt"), "--config", str(project / "config.toml")]
    )

    assert status == 0
    output = project / "sample.th.srt"
    assert output.exists()
    assert srtio.load_srt(output).subtitles[0].content == "สวัสดี"
    assert "sample.en.srt -> sample.th.srt" in capsys.readouterr().out


def test_missing_config_is_an_error(project, capsys):
    status = translate.main([str(project / "sample.en.srt"), "--config", str(project / "nope.toml")])
    assert status == 2
    assert "config error" in capsys.readouterr().err


def test_missing_input_file_is_an_error(project, fake, capsys):
    status = translate.main(
        [str(project / "ghost.srt"), "--config", str(project / "config.toml")]
    )
    assert status == 2
    assert "not found" in capsys.readouterr().err


def test_missing_api_key_is_an_error(project, monkeypatch, capsys):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(config, "OpenRouterClient", None, raising=False)
    status = translate.main(
        [str(project / "sample.en.srt"), "--config", str(project / "config.toml")]
    )
    assert status == 2
    assert "OPENROUTER_API_KEY" in capsys.readouterr().err


def test_multiple_files(project, fake):
    shutil.copy(FIXTURES / "simple.srt", project / "second.en.srt")
    fake.queue(REPLIES).queue(REPLIES)
    status = translate.main(
        [
            str(project / "sample.en.srt"),
            str(project / "second.en.srt"),
            "--config",
            str(project / "config.toml"),
        ]
    )
    assert status == 0
    assert (project / "sample.th.srt").exists()
    assert (project / "second.th.srt").exists()


def test_output_flag_requires_a_single_file(project, fake, capsys):
    status = translate.main(
        [
            str(project / "sample.en.srt"),
            str(project / "sample.en.srt"),
            "--output",
            str(project / "x.srt"),
            "--config",
            str(project / "config.toml"),
        ]
    )
    assert status == 2
    assert "--output" in capsys.readouterr().err


def test_output_flag_writes_where_asked(project, fake):
    target = project / "custom.srt"
    fake.queue(REPLIES)
    status = translate.main(
        [
            str(project / "sample.en.srt"),
            "--output",
            str(target),
            "--config",
            str(project / "config.toml"),
        ]
    )
    assert status == 0
    assert target.exists()


def test_failed_entries_are_reported_but_run_succeeds(project, fake, capsys):
    fake.queue("[1] สวัสดี\n[2] 你好\n[3] {0}สบายดี{1}")  # entry 2 leaks Chinese
    fake.queue("[2] 你好")
    fake.queue("[2] 你好")
    fake.queue("[2] 你好")
    status = translate.main(
        [str(project / "sample.en.srt"), "--config", str(project / "config.toml")]
    )
    captured = capsys.readouterr()
    assert status == 0
    assert "FAILED" in captured.out
    assert "left in English" in captured.err


def test_config_controls_output_naming(project, fake):
    (project / "config.toml").write_text(
        '[llm]\nmodel = "test/model"\n[output]\nstrip_en_suffix = false\n', encoding="utf-8"
    )
    fake.queue(REPLIES)
    translate.main([str(project / "sample.en.srt"), "--config", str(project / "config.toml")])
    assert (project / "sample.en.th.srt").exists()


def test_verbose_flag_logs_batches(project, fake, caplog):
    fake.queue(REPLIES)
    translate.main(
        [str(project / "sample.en.srt"), "--config", str(project / "config.toml"), "-v"]
    )


# --- multi-file batch processing (step 14) -----------------------------------


def copy(project: Path, name: str) -> Path:
    shutil.copy(FIXTURES / "simple.srt", project / name)
    return project / name


def run(project, fake, *args) -> int:
    return translate.main([*map(str, args), "--config", str(project / "config.toml")])


def test_colliding_outputs_are_refused_before_any_call(project, fake, capsys):
    status = run(project, fake, copy(project, "movie.en.srt"), copy(project, "movie.srt"))
    err = capsys.readouterr().err
    assert status == 2
    assert "would both write movie.th.srt" in err
    assert fake.call_count == 0
    assert not (project / "movie.th.srt").exists()


def test_duplicate_input_is_refused(project, fake, capsys):
    source = copy(project, "sample.en.srt")
    status = run(project, fake, source, source)
    assert status == 2
    assert "given more than once" in capsys.readouterr().err
    assert fake.call_count == 0


def test_duplicate_detected_through_a_symlink(project, fake, capsys):
    source = copy(project, "sample.en.srt")
    alias = project / "alias.en.srt"
    alias.symlink_to(source)
    status = run(project, fake, source, alias)
    assert status == 2
    assert "given more than once" in capsys.readouterr().err


def test_existing_translation_is_refused(project, fake, capsys):
    already = copy(project, "done.th.srt")
    status = run(project, fake, already)
    err = capsys.readouterr().err
    assert status == 2
    assert "looks like an existing translation" in err
    assert "--force" in err
    assert fake.call_count == 0


def test_force_accepts_an_existing_translation(project, fake):
    already = copy(project, "done.th.srt")
    fake.queue(REPLIES)
    status = translate.main(
        [str(already), "--force", "--config", str(project / "config.toml")]
    )
    assert status == 0
    assert fake.call_count == 1
    assert (project / "done.th.th.srt").exists()


def test_a_directory_is_not_an_input_file(project, fake, capsys):
    folder = project / "subs"
    folder.mkdir()
    status = run(project, fake, folder)
    assert status == 2
    assert "is not a file" in capsys.readouterr().err


def test_every_problem_is_reported_at_once(project, fake, capsys):
    folder = project / "subs"
    folder.mkdir()
    status = run(project, fake, folder, project / "ghost.srt", copy(project, "done.th.srt"))
    err = capsys.readouterr().err
    assert status == 2
    assert "is not a file" in err
    assert "not found" in err
    assert "looks like an existing translation" in err


def test_one_bad_file_aborts_the_whole_run(project, fake):
    fake.queue(REPLIES)
    status = run(project, fake, copy(project, "sample.en.srt"), project / "ghost.srt")
    assert status == 2
    assert fake.call_count == 0
    assert not (project / "sample.th.srt").exists()


def test_output_flag_may_not_overwrite_its_input(project, fake, capsys):
    source = copy(project, "sample.en.srt")
    status = translate.main(
        [str(source), "--output", str(source), "--config", str(project / "config.toml")]
    )
    assert status == 2
    assert "overwritten by its own output" in capsys.readouterr().err


def test_dry_run_checks_the_inputs_too(project, fake, capsys):
    status = translate.main(
        [str(copy(project, "done.th.srt")), "--dry-run", "--config", str(project / "config.toml")]
    )
    assert status == 2
    assert "looks like an existing translation" in capsys.readouterr().err


def test_multiple_files_get_progress_headers_and_a_total(project, fake, capsys):
    fake.queue(REPLIES).queue(REPLIES)
    status = run(project, fake, copy(project, "a.en.srt"), copy(project, "b.en.srt"))
    captured = capsys.readouterr()

    assert status == 0
    assert "[1/2] a.en.srt" in captured.err
    assert "[2/2] b.en.srt" in captured.err
    assert "total: 2 files  2 translated  6 entries  0 tokens" in captured.out


def test_single_file_gets_no_header_or_total(project, fake, capsys):
    fake.queue(REPLIES)
    run(project, fake, copy(project, "sample.en.srt"))
    captured = capsys.readouterr()
    assert "[1/1]" not in captured.err
    assert "total:" not in captured.out


def test_a_crashed_file_does_not_stop_the_rest(project, fake, capsys):
    import llm

    fake.queue(llm.LLMError("gateway exploded")).queue(REPLIES)
    status = run(project, fake, copy(project, "a.en.srt"), copy(project, "b.en.srt"))
    captured = capsys.readouterr()

    assert status == 1
    assert "a.en.srt: gateway exploded" in captured.err
    assert (project / "b.th.srt").exists()
    assert "1 FAILED" in captured.out


def test_leftover_english_does_not_change_the_exit_status(project, fake, capsys):
    fake.queue("[1] สวัสดี\n[2] 你好\n[3] {0}สบายดี{1}")
    for _ in range(3):
        fake.queue("[2] 你好")  # entry 2 never recovers
    fake.queue(REPLIES).queue(REPLIES)  # the retry loop and the second file
    status = run(project, fake, copy(project, "a.en.srt"), copy(project, "b.en.srt"))
    assert status == 0
    assert "total: 2 files  2 translated  6 entries  1 left in English  0 tokens" in capsys.readouterr().out


def test_limit_with_several_files_warns(project, fake, capsys):
    fake.queue("[1] สวัสดี").queue("[1] สวัสดี")
    run(project, fake, copy(project, "a.en.srt"), copy(project, "b.en.srt"), "--limit", "1")
    assert "--limit/--range apply to every file" in capsys.readouterr().err


# --- context (step 9) -------------------------------------------------------


def test_context_counts_come_from_config(project, fake):
    (project / "config.toml").write_text(
        '[llm]\nmodel = "test/model"\n[batch]\nsize = 1\ncontext_before = 1\ncontext_after = 1\n',
        encoding="utf-8",
    )
    fake.queue("[1] สวัสดี").queue("[2] สบายดีไหม").queue("[3] {0}สบายดี{1}")
    status = translate.main(
        [str(project / "sample.en.srt"), "--config", str(project / "config.toml")]
    )
    assert status == 0
    assert fake.call_count == 3  # size = 1

    first = fake.calls[0][1]["content"]
    second = fake.calls[1][1]["content"]

    assert "[CONTEXT BEFORE - do not translate]" not in first  # nothing behind it
    assert "[CONTEXT BEFORE - do not translate]\n[1] Hello there.\nTH: สวัสดี" in second

    after_block = second.split("[CONTEXT AFTER - do not translate]")[1].split("[TRANSLATE]")[0]
    assert "Fine, thanks." in after_block  # next line, English only
    assert "TH:" not in after_block


def test_context_is_not_part_of_the_output(project, fake):
    (project / "config.toml").write_text(
        '[llm]\nmodel = "test/model"\n[batch]\nsize = 1\ncontext_before = 2\n',
        encoding="utf-8",
    )
    fake.queue("[1] สวัสดี").queue("[2] สบายดีไหม").queue("[3] {0}สบายดี{1}")
    translate.main([str(project / "sample.en.srt"), "--config", str(project / "config.toml")])
    output = srtio.load_srt(project / "sample.th.srt")
    assert [s.index for s in output.subtitles] == [1, 2, 3]
    assert [s.content for s in output.subtitles] == ["สวัสดี", "สบายดีไหม", "<i>สบายดี</i>"]


# --- limit / range / dry-run (step 10) --------------------------------------


def test_limit_translates_only_the_first_n(project, fake):
    fake.queue("[1] สวัสดี")
    status = translate.main(
        [str(project / "sample.en.srt"), "--config", str(project / "config.toml"), "--limit", "1"]
    )
    assert status == 0
    assert fake.call_count == 1
    output = srtio.load_srt(project / "sample.th.srt")
    assert [s.index for s in output.subtitles] == [1]


def test_range_writes_only_that_slice(project, fake):
    fake.queue("[2] สบายดีไหม")
    status = translate.main(
        [str(project / "sample.en.srt"), "--config", str(project / "config.toml"),
         "--range", "2-2"]
    )
    assert status == 0
    assert fake.call_count == 1
    output = srtio.load_srt(project / "sample.th.srt")
    assert [s.index for s in output.subtitles] == [2]
    assert output.subtitles[0].content == "สบายดีไหม"


def test_range_still_sees_context_outside_the_slice(project, fake):
    fake.queue("[2] สบายดีไหม")
    translate.main(
        [str(project / "sample.en.srt"), "--config", str(project / "config.toml"),
         "--range", "2-2"]
    )
    prompt = fake.calls[0][1]["content"]
    assert "[1] Hello there." in prompt      # before
    assert "Fine, thanks." in prompt          # after


def test_empty_range_writes_an_empty_file(project, fake):
    status = translate.main(
        [str(project / "sample.en.srt"), "--config", str(project / "config.toml"),
         "--range", "90-99"]
    )
    assert status == 0
    assert fake.call_count == 0


def test_bad_range_is_rejected(project, fake, capsys):
    status = translate.main(
        [str(project / "sample.en.srt"), "--config", str(project / "config.toml"),
         "--range", "9-2"]
    )
    assert status == 2
    assert "after its end" in capsys.readouterr().err


def test_negative_limit_is_rejected(project, fake, capsys):
    status = translate.main(
        [str(project / "sample.en.srt"), "--config", str(project / "config.toml"),
         "--limit", "-1"]
    )
    assert status == 2
    assert "negative" in capsys.readouterr().err


def test_dry_run_says_one_entry_for_one_entry(project, fake, capsys):
    shutil.copy(FIXTURES / "simple.srt", project / "one.en.srt")
    translate.main(
        [str(project / "one.en.srt"), "--limit", "1", "--dry-run",
         "--config", str(project / "config.toml")]
    )
    out = capsys.readouterr().out
    assert "1 entry in 1 batch" in out
    assert "1 entries" not in out


def test_dry_run_needs_no_key_and_builds_no_client(project, monkeypatch, capsys):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    def boom(*args, **kwargs):
        raise AssertionError("dry run must not build a client")

    monkeypatch.setattr(config, "build_client", boom)
    status = translate.main(
        [str(project / "sample.en.srt"), "--config", str(project / "config.toml"), "--dry-run"]
    )
    out = capsys.readouterr().out
    assert status == 0
    assert "3 entries in 1 batch" in out  # default size 60 fits the whole file
    assert "tokens" in out and "$" in out


# --- story bible pre-pass (step 15) -----------------------------------------

BIBLE_REPLY = json.dumps(
    {
        "summary": "Somsak runs a garage; Mai comes home from school.",
        "notes": "Bangkok, present day.",
        "characters": [
            {
                "name": "Somsak",
                "gender": "male",
                "age": "40s",
                "role": "owns a garage",
                "relationships": "Mai's father",
                "register": "ผม + ครับ with customers",
            }
        ],
        "terms": [{"en": "Bangkok", "action": "transliterate", "th": "กรุงเทพ"}],
    },
    ensure_ascii=False,
)


def test_bible_only_writes_both_files_and_stops(project, fake, capsys):
    fake.queue(BIBLE_REPLY)
    status = run(project, fake, copy(project, "sample.en.srt"), "--bible-only")
    captured = capsys.readouterr()

    assert status == 0
    assert fake.call_count == 1
    assert not (project / "sample.th.srt").exists()
    assert "Somsak" in (project / "sample.bible.md").read_text(encoding="utf-8")
    assert glossary.load(project / "sample.glossary.toml") == [
        glossary.Term("Bangkok", "transliterate", "กรุงเทพ")
    ]
    assert "sample.en.srt -> sample.bible.md, sample.glossary.toml" in captured.out
    assert "1 character  1 term" in captured.out
    assert "then translate without --bible-only" in captured.err


def test_bible_only_refuses_to_overwrite_hand_edits(project, fake, capsys):
    path = project / "sample.bible.md"
    path.write_text("my own notes", encoding="utf-8")
    status = run(project, fake, copy(project, "sample.en.srt"), "--bible-only")
    err = capsys.readouterr().err

    assert status == 2
    assert "already exists" in err and "--force" in err
    assert fake.call_count == 0
    assert path.read_text(encoding="utf-8") == "my own notes"
    assert not (project / "sample.glossary.toml").exists()


def test_force_rebuilds_the_bible(project, fake):
    (project / "sample.glossary.toml").write_text("# old", encoding="utf-8")
    fake.queue(BIBLE_REPLY)
    status = run(project, fake, copy(project, "sample.en.srt"), "--bible-only", "--force")

    assert status == 0
    assert (project / "sample.bible.md").exists()
    assert glossary.load(project / "sample.glossary.toml")


def test_bible_only_reports_every_existing_file_before_any_call(project, fake, capsys):
    (project / "a.bible.md").write_text("mine", encoding="utf-8")
    (project / "b.glossary.toml").write_text("# mine", encoding="utf-8")
    status = run(project, fake, copy(project, "a.en.srt"), copy(project, "b.en.srt"), "--bible-only")
    err = capsys.readouterr().err

    assert status == 2
    assert "a.bible.md already exists" in err
    assert "b.glossary.toml already exists" in err
    assert fake.call_count == 0


def test_bible_only_reads_only_the_selected_range(project, fake):
    fake.queue(BIBLE_REPLY)
    status = run(
        project, fake, copy(project, "sample.en.srt"), "--bible-only", "--range", "1-1"
    )

    assert status == 0
    script = fake.calls[0][1]["content"]
    assert "Hello there." in script
    assert "How are you?" not in script


def test_bible_only_with_several_files(project, fake, capsys):
    fake.queue(BIBLE_REPLY).queue(BIBLE_REPLY)
    status = run(project, fake, copy(project, "a.en.srt"), copy(project, "b.en.srt"), "--bible-only")
    captured = capsys.readouterr()

    assert status == 0
    assert "[1/2] a.en.srt" in captured.err and "[2/2] b.en.srt" in captured.err
    for name in ("a", "b"):
        assert (project / f"{name}.bible.md").exists()
        assert (project / f"{name}.glossary.toml").exists()


def test_a_failed_pre_pass_writes_nothing(project, fake, capsys):
    fake.queue("I would rather not.").queue("Still prose.")
    status = run(project, fake, copy(project, "sample.en.srt"), "--bible-only")
    err = capsys.readouterr().err

    assert status == 1
    assert "error: sample.en.srt" in err
    assert not (project / "sample.bible.md").exists()
    assert not (project / "sample.glossary.toml").exists()


def test_bible_only_rejects_output_flag(project, fake, capsys):
    status = translate.main(
        [
            str(copy(project, "sample.en.srt")),
            "--bible-only",
            "--output",
            str(project / "x.srt"),
            "--config",
            str(project / "config.toml"),
        ]
    )
    assert status == 2
    assert "--output" in capsys.readouterr().err
    assert fake.call_count == 0


def test_a_normal_run_does_not_build_a_bible(project, fake):
    fake.queue(REPLIES)
    status = run(project, fake, copy(project, "sample.en.srt"))

    assert status == 0
    assert fake.call_count == 1  # translation only, no pre-pass
    assert not (project / "sample.bible.md").exists()
    assert not (project / "sample.glossary.toml").exists()


def test_check_bible_targets_ignores_missing_inputs(project):
    assert translate.check_bible_targets([str(project / "ghost.srt")]) == []


def test_bible_paths_sit_beside_the_source(project):
    bible_path, glossary_path = translate.bible_paths(project / "Show.en.hi_partial.srt")
    assert bible_path.name == "Show.hi_partial.bible.md"
    assert glossary_path.name == "Show.hi_partial.glossary.toml"
