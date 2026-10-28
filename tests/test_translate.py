from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import config
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
