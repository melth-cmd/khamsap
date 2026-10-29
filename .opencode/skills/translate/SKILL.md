---
name: Thai subtitle translator
description: Use when translating an English .srt into Thai with this project's CLI, or when asked about its flags, sidecar files, report, cache, bible/glossary, or how to run it.
---

# Thai subtitle translator

Work from the project root. Run with the venv interpreter
(`.venv/bin/python translate.py ...`) or after `source .venv/bin/activate`.

## Key

The key comes from the environment variable named in `[llm] key_env`
(`OPENROUTER_API_KEY` by default). It is never in `config.toml` or the cache, and
must never be printed or committed. A local `.env` may hold it for this machine;
source it without echoing: `set -a; . ./.env; set +a`.

## Translate

    python translate.py "Show.en.srt"           # -> Show.th.srt next to it
    python translate.py *.en.srt                 # several files in one run
    python translate.py "Show.en.srt" --dry-run  # batches + rough cost, no call, no key

Use `*.en.srt`, not `*.srt`: the latter also matches existing `.th.srt` outputs and
pre-flight refuses them without `--force`.

Next to each source you get `.th.srt` (watch this), `.report.txt` (flags; read it),
`.cache.json` (resume), `.bible.md` and `.glossary.toml` (hand-editable notes).

## Common options

    --fix        after translating, re-translate only the flagged entries (costs money)
    --review     ask the model to review flagged entries; suggestions at the top of the report
    --bible-only build bible + glossary from the script, then stop (--force to rebuild)
    --limit N / --range A-B   translate part of the file (real SRT indexes)
    --output PATH             write elsewhere (one input file only)
    --config PATH             use another config.toml
    -v                        per-batch progress

`strip_hi` is a config setting (`[cleanup] strip_hi`), not a flag. `bilingual` is
planned and not implemented.

## Behaviour worth remembering

- Checks and `--review` never change the `.srt`. Only `--fix` re-translates; a line
  that still fails stays English and says why in the report.
- Editing a glossary term re-translates only the lines that use it; editing the bible
  re-translates the whole file.
- Exit codes: 0 finished (even with lines left in English), 1 a file failed, 2 a
  problem before any work (usage, config, missing input).
- Real runs cost money and take minutes. Use `--dry-run` when unsure, and only run
  against files the user named.

## Development

    uv pip install -r requirements-dev.txt
    pytest -q

`README.md` is the user manual; `SPEC.md` and `PROGRESS.md` hold the design and status.
