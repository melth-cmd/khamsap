### PROJECT GOAL

A lightweight personal CLI tool (Arch Linux, Python 3.11+) that translates English
.srt subtitle files into Thai using an LLM through the OpenRouter API.
It replaces my manual copy-paste workflow (see "OLD MANUAL WORKFLOW" at the bottom,
kept for reference only).

Core ideas:
- Only subtitle TEXT goes to the LLM. Indexes and timestamps never leave the program.
- A "story bible" + glossary built in a pre-pass gives consistent names, pronouns and register.
- Everything is validated in code and cached, so a crash or rerun resumes cleanly.
- Formatting tags are handled by code, not trusted to the model.


### DESIGN DECISIONS (what changed from the first draft, and why)

1. Story bible instead of glossary only.
   Thai needs speaker gender, age, relationship and politeness level for pronouns
   (ผม/ฉัน/หนู/เรา) and particles (ครับ/ค่ะ/นะ/สิ). English subtitles do not carry this,
   so the pre-pass must infer it. Title and year from the filename are passed in too,
   since models often recognize well-known films.

2. Structured, hand-editable glossary (TOML), injected per batch.
   Only entries that appear in the current batch are injected (saves tokens, keeps focus).

3. Wire format: tagged lines by default, JSON optional.
   Send:   [12] Hello there.\nHow are you?      (\n = line break inside one entry)
   Expect: [12] <Thai text>                      (same IDs back)
   Cheaper in tokens than JSON (Thai is token-expensive) and trivial to parse.
   Config option to switch to JSON / OpenRouter response_format json_schema if the
   chosen model supports it.

4. Tags handled by placeholders.
   Strip <i>, </i>, <b>, {\an8} etc. and music markers before sending, replace them with
   numbered placeholders ({0}, {1}, ...; chosen over symbolic ones for token cost),
   restore in code afterward, and validate that the placeholder counts match (missing,
   duplicate or invented placeholders are re-requested, never silently shipped).

   Sound-effect brackets ([door slams]) and speaker dashes ("- Hello.") are content,
   not tags, and are left intact for the model.

   Optional HI (hearing-impaired) stripping: with [cleanup] strip_hi enabled, bracketed
   and parenthesized descriptions ([door slams], (laughs), [MUSIC PLAYING]) are removed
   from the SOURCE before batching, so they are never sent or paid for. Music markers
   and speaker labels are kept. An entry left with no words is dropped from the output
   entirely, so the translated file may legitimately contain fewer entries than the
   source; verification is told to expect that. Default off, since removing sound cues
   is a viewing preference, not a translation one.

5. Validate by ID, not just count.
   All IDs present, no extras, none empty, Thai characters present (unless the source
   is non-verbal like "Mm-hmm" or a music marker).

6. Retry only the bad entries.
   If 58 of 60 are good, keep them and re-request only the 2 failures (max 3 retries,
   exponential backoff with jitter, respect Retry-After on 429, explicit timeout).

7. Reading-speed check instead of length-ratio check.
   Using the timestamps, flag entries whose Thai characters-per-second exceed a
   configurable limit. That catches subtitles that are unreadable on screen.

8. Context in both directions.
   Previous 3-5 lines: English AND the Thai already produced (carries tone/pronouns).
   Next 2-3 lines: English only (helps with sentences split across entries).
   Context lines are marked "context only, do not translate".

9. Smart cache keys.
   Key per entry = hash(source text + model + prompt version + glossary entries that
   apply to that entry). Editing the glossary only invalidates the affected lines.

10. GUI dropped (optional, probably never).
    The pain points were translation quality and the paste loop; a CLI fixes both.
    Replaced by a bilingual output option (English line above Thai) which gives
    side-by-side checking in any video player for almost no code.

11. Cache, context, multi-file batching and --limit/--dry-run moved into early phases.


### TECH STACK

- Python 3.11+ (tomllib is built in, nothing extra to install for config)
- srt (parsing/composing SRT)
- openai SDK pointed at https://openrouter.ai/api/v1  (or plain httpx)
- Optional: charset-normalizer (encoding fallback)
- pytest for tests


### PROJECT LAYOUT

  translate.py        CLI entry point (argparse)
  config.toml         model, temperature, batch size, limits, wire format, paths
  srtio.py            read (utf-8-sig, then charset fallback), parse, write
  tags.py             strip/restore formatting tags and placeholders; tag-count check
  batcher.py          split into ~60-entry batches, never mid-entry, add context
  llm.py              OpenRouter client, backoff, timeout, wire-format encode/decode
  cache.py            JSON cache, per-entry keys, atomic writes
  bible.py            pre-pass: story bible + glossary generation and loading
  glossary.py         load TOML glossary, select entries per batch
  verify.py           checks + report.txt
  tests/              pytest + fake LLM client + small sample .srt fixtures


### CONFIG (config.toml sketch)

  [llm]
  base_url = "https://openrouter.ai/api/v1"
  key_env = "OPENROUTER_API_KEY" # change for other providers
  model = "..."                 # e.g. deepseek/deepseek-v4.1-flash-1017
  temperature = 0.25
  timeout_s = 120
  max_retries = 3
  wire_format = "tagged"        # or "json"
  input_usd_per_mtok = 0.30     # for --dry-run estimates only
  output_usd_per_mtok = 1.20

  [batch]
  size = 60
  context_before = 4
  context_after = 2

  [output]
  encoding = "utf-8"            # or "utf-8-sig" for TVs/old players that garble Thai
  bilingual = false             # English line above Thai
  strip_en_suffix = true        # Movie.en.srt -> Movie.th.srt

  [cleanup]
  strip_hi = false              # drop [sound effects] and (descriptions) from the source

  [verify]
  max_cps = 17                  # Thai chars per second, tune by feel

API key: OPENROUTER_API_KEY environment variable only. Never in config or cache.


### FILES THE TOOL PRODUCES

  Movie (2026).th.srt        final translation
  Movie (2026).bible.md      story summary, characters (gender/age/relationships),
                             default register per relationship. Editable.
  Movie (2026).glossary.toml terms table. Editable. Example:
                               [[term]]
                               en = "Gandalf"
                               action = "transliterate"
                               th = "แกนดัล์ฟ"
                               [[term]]
                               en = "NASA"
                               action = "keep"
  Movie (2026).cache.json    per-entry translation cache
  Movie (2026).report.txt    verification flags (entry number, original, translation, reason)

Output naming: strip ".en" rather than appending ".th" blindly.


### TRANSLATION PROMPT REQUIREMENTS

(This replaces the old "translate skill". Chat-specific parts such as acknowledging
the plan, fenced blocks and manual verification are now handled by code.)

The system prompt must say:
- Output only the requested format. No notes, explanations or censoring.
- Natural, casual spoken Thai, not literary Thai.
- Subtitles are read quickly: be concise.
- Thai subtitle convention: spaces instead of commas and periods.
- Use the story bible for pronouns, particles and register per speaker/relationship.
- Follow the glossary exactly (keep in English vs. specific Thai spelling).
- Translate song lyrics for meaning; keep music markers.
- Profanity: translate at equivalent strength, do not soften.
- Idioms: translate meaning, not words.
- On-screen text: translate, keep the brackets. (Sound-effect brackets only reach the
  model when strip_hi is off; with it on they were already removed from the source.)
- Never merge or split entries. One input ID = one output ID.
- Context lines are for understanding only and must not appear in the output.

Test 2-3 models on the same 60-line sample before committing. Thai quality varies a lot
between models; this is the cheapest quality improvement available.


### PRE-PASS (story bible + glossary)

Input: whole English subtitle text (chunked if too large) + title/year from filename.
Ask the LLM to produce:
- 3-5 sentence plot/setting summary
- Characters: name, gender, age range, relationships, suggested register toward others
- Terms: character names, places, brands, technical terms, each with keep/transliterate
  and a suggested Thai spelling
Write to bible.md and glossary.toml. Pause (or offer --stop-after-bible) so I can edit
them by hand before translating. Translation requests then include the bible (short form)
and only the glossary entries relevant to each batch.


### VERIFICATION PASS (after translation, report only)

Flags written to report.txt, never auto-fixed:
- Leftover English words not in the glossary's "keep" list
- Glossary terms present in the source but missing or inconsistent in the Thai
- Reading speed above max_cps (using the original timestamps)
- Tag count mismatch with source
- Empty or suspiciously short/long output

Entries dropped by HI stripping are not flagged; the report notes how many were removed
so a surprisingly low entry count is explainable.

Optional --fix: re-translate flagged lines only, with a stricter prompt.
Optional --review: a second LLM pass that sees only flagged lines plus context and
proposes corrections (written to the report, applied only with --fix).


### CLI

  python translate.py input.en.srt            -> input.th.srt
  python translate.py *.srt                   multiple files (simple loop)
  --limit N / --range A-B                     translate only part of the file (dev/testing)
  --dry-run                                   show batch count and estimated tokens/cost, no API calls
  --bible-only                                build bible + glossary, then stop
  --strip-hi                                  drop [sound effects]/(descriptions) from output
  --fix, --review                             see Verification
  --bilingual                                 English above Thai
  --config path.toml
