# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-30

First public release.

### Added

- Command-line tool that translates English `.srt` subtitles into Thai using an
  LLM (OpenRouter by default).
- Only subtitle text is sent to the model; subtitle numbers and timestamps are
  never changed.
- Translation cache (`.cache.json`) so reruns do not pay for finished lines again.
- Story bible (`.bible.md`) and glossary (`.glossary.toml`) to keep names,
  pronouns and politeness consistent. Both are plain text and meant to be
  edited by hand.
  - Editing a glossary term re-translates only the lines that use it.
  - Editing the bible re-translates the whole file.
- Quality-check report (`.report.txt`) listing flagged lines, with checks for
  `english_left`, `glossary_missing`, `line_count`, `dashes_lost` and `failed`.
  Nothing is changed automatically.
- Command options:
  - Multiple input files in one run; a failing file does not stop the others.
  - `--dry-run` to show the batch count and a rough cost with no API call.
  - `--bible-only` (with `--force` to rebuild) to generate the bible and
    glossary before translating a long series.
  - `--fix` to re-translate only flagged lines with a stricter prompt.
  - `--review` to have the model suggest improvements for flagged lines
    without changing the subtitles.
  - `--limit` and `--range` to translate part of a file.
  - `--output`, `--config`, `-v` and `--force`.
- Optional `strip_hi` setting to remove sound descriptions such as `[door slams]`
  and `(laughs)` before translating.
- Support for any OpenAI-compatible provider through `[llm]` settings in
  `config.toml`, including custom request headers.
- API key is read from the environment only and is never written to config
  or cache files.
- Exit codes: `0` finished, `1` a file failed, `2` problem before any work.
- Test suite (`pytest`) and development requirements file.

[Unreleased]: https://github.com/melth-cmd/khamsub/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/melth-cmd/khamsub/releases/tag/v0.1.0
