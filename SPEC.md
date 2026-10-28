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
   from the SOURCE before batching, so they are never sent or paid for.

   Speaker attribution survives: a bracket containing capitals is treated as a name and
   kept ([Maya], [Mr. O], [Yuki speaking Japanese]). All-caps brackets are still cues
   and go ([MUSIC PLAYING]). A few cues that happen to be mixed-case survive too
   ([Anna chuckles]); that is accepted, because deleting the speaker's name is worse. An entry left with no words is dropped from the output
   entirely, so the translated file may legitimately contain fewer entries than the
   source; verification is told to expect that. Default off, since removing sound cues
   is a viewing preference, not a translation one.

5. Validate by ID, not just count.
   All IDs present, no extras, none empty, Thai characters present (unless the source
   is non-verbal like "Mm-hmm" or a music marker), and formatting placeholders intact.

   Language leaks are caught too: DeepSeek-style models sometimes answer in Chinese.
   Any Han/Kana/Hangul/Cyrillic in the output that the English source did not already
   contain is a failure, so a half-Thai half-Chinese line cannot pass. Text that is
   Han in the source (an on-screen sign) stays allowed. Detecting this in code is the
   only reliable fix; prompting alone does not stop it, so the prompt says nothing extra.

6. Retry only the bad entries.
   If 58 of 60 are good, keep them and re-request only the 2 failures (max 3 retries,
   exponential backoff with jitter, respect Retry-After on 429, explicit timeout).
   Retries use temperature 0.1 to make a repeat less likely. One shared budget covers
   validation failures, 429s and timeouts alike.

   When the budget runs out the entry is left as its original English and flagged with
   a reason: English is a better failure than the wrong language, because it is visibly
   wrong and easy to fix by hand. Failed entries are not cached, so a rerun re-requests
   them. No fallback model.

7. Context in both directions.
   Previous 3-5 lines: English AND the Thai already produced (carries tone/pronouns).
   Next 2-3 lines: English only (helps with sentences split across entries).
   Context lines are marked "context only, do not translate".

8. Smart cache keys.
   Key per entry = hash(source text + model + prompt version + glossary entries that
   apply to that entry + a digest of the story bible). Editing the glossary only
   invalidates the affected lines. Editing the bible re-translates the file, because
   every line's pronouns and particles can depend on it and one file must not mix
   two registers; that is deliberate, not an accident.

9. GUI dropped (optional, probably never).
    The pain points were translation quality and the paste loop; a CLI fixes both.
    Replaced by a bilingual output option (English line above Thai) which gives
    side-by-side checking in any video player for almost no code.

10. Cache, context, multi-file batching and --limit/--dry-run moved into early phases.

11. Line breaks inside an entry are layout, so code repairs them.
    Models treat a newline inside an entry as soft wrapping and merge lines: measured
    4/4 merges on stacked lines before the prompt said anything about it. The prompt now
    states the rule as a line count ("if the source entry has two lines, the translation
    must have two lines"), which took the same sample to 12/12 correct.

    As a safety net, layout.py restores the source line count when the model ignores it:
      - dash rule: when any source line starts with a speaker dash (hyphen, en dash or
        em dash) and the merged line has exactly the right number of inline turns, split
        at those turns. The dash is structural, so this recovers the speaker boundary
        exactly. A dash hidden behind a tag or a placeholder still counts ({0}-Hello.).
      - proportional rule: otherwise split at the space nearest each source break point,
        scaled by source line lengths; three or more lines split at every point. A split
        never lands inside a placeholder, a bracket or a music marker.
    When every source line has a dash and the model dropped one, the dash is put back,
    in the style of that source line ("- Hello." keeps its space, "-Hello." does not),
    and reported as a restored dash. A source with only some dashed lines gets no
    invented dashes, because there a dash could be a hyphen.
    Blank lines inside an entry do not count as lines. More lines than the source are
    accepted and reported, not trimmed.

    Re-wrapping happens on READ, including from cache, so entries merged by an older run
    are repaired without retranslating. Every repair is flagged for report.txt, which also
    gives a merge rate to judge whether the prompt rule is working. Layout is never a
    reason to fall back to English, and never a reason to spend a retry.


### TECH STACK

- Python 3.11+ (tomllib is built in, nothing extra to install for config)
- srt (parsing/composing SRT)
- openai SDK pointed at https://openrouter.ai/api/v1  (or plain httpx)
- Optional: charset-normalizer (encoding fallback)
- pytest for tests


### PROJECT LAYOUT

  translate.py        CLI entry point (argparse)
  config.toml         model, temperature, batch size, limits, wire format, paths
  config.py           load + validate config.toml, build the client from it
  srtio.py            read (utf-8-sig, then charset fallback), parse, write
  tags.py             strip/restore formatting tags and placeholders; tag-count check
  batcher.py          split into ~60-entry batches, never mid-entry, add context
  llm.py              OpenRouter client, timeout, wire-format encode/decode, validation
  runner.py           translate one batch: validate replies, retry only what failed
  pipeline.py         one file end to end: prepare, batch, translate, restore, rebuild
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
  strip_en_suffix = true        # ".en." becomes ".th." wherever it stands

  [cleanup]
  strip_hi = false              # drop [sound effects] and (descriptions) from the source

API key: OPENROUTER_API_KEY environment variable only. Never in config or cache.

Config loading rules: every setting has a built-in default, so a missing config.toml
(beside the script) is a warning, not an error; an explicit --config that does not exist
is fatal. Values are type- and range-checked on load, unknown keys are ignored with a
warning, and wire_format "json" is rejected until it is implemented.


### FILES THE TOOL PRODUCES

  Movie (2026).th.srt        final translation
  Movie (2026).bible.md      story summary, characters (gender/age/relationships),
                             default register per relationship. Editable: read again at
                             the start of every run, and the whole file goes with every
                             request (it is short by design).
  Movie (2026).glossary.toml terms table. Editable and read again on every run;
                             a broken term stops the run (exit 2) before any API
                             call, naming the term. action is one of keep,
                             transliterate or translate; th holds the Thai
                             spelling for the last two. Example:
                               [[term]]
                               en = "Gandalf"
                               action = "transliterate"
                               th = "แกนดัล์ฟ"
                               [[term]]
                               en = "NASA"
                               action = "keep"
                               [[term]]
                               en = "wizard"
                               action = "translate"
                               th = "พ่อมด"
  Movie (2026).cache.json    per-entry translation cache
                             (saved after every batch; invalidated by a change of model,
                              prompt version or that entry's source text; failed entries
                              are never cached, so a rerun re-requests only them)
  Movie (2026).report.txt    verification flags (entry number, original, translation, reason)

Output naming: the ".en." tag becomes ".th." wherever it stands in the name, and ".th"
is appended before the extension when there is no such tag:
  Movie (2026).en.srt            -> Movie (2026).th.srt
  Show.S01E01.en.hi_partial.srt  -> Show.S01E01.th.hi_partial.srt
  Movie (2026).srt               -> Movie (2026).th.srt
  (strip_en_suffix = false keeps the tag: Movie.en.srt -> Movie.en.th.srt)
Sidecar files (cache, bible, glossary, report) drop the tag instead of rewriting it,
so Movie.en.srt gives Movie.cache.json.


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
- Never merge or split the lines inside an entry: two source lines must come back as
  two lines, three as three.
- Context lines are for understanding only and must not appear in the output.

Test 2-3 models on the same 60-line sample before committing. Thai quality varies a lot
between models; this is the cheapest quality improvement available.


### PRE-PASS (story bible + glossary)

Run only when asked: `--bible-only` reads the file and stops. A normal translation run
never builds the files, it only uses them if they already exist.

Input: the same cleaned English text translation would see (HI already stripped per
config, formatting tags removed), plus the title and year guessed from the filename.
The guess is only a hint, never written into the files.

Chunking: parts of about 12,000 characters, cut between entries only. One part is used
as it comes back. Several parts are read separately (characters/terms per part) and a
single merge call folds them into one bible, so a character who first appears halfway
through still gets a consistent register.

Ask the LLM to produce:
- 3-5 sentence plot summary, plus setting/era/tone
- Characters: name, gender, age range, relationships, and the Thai register in Thai
  script (pronouns and particles), e.g. "ผม + ครับ with strangers, หนู + ค่ะ with her
  father". Everything else is written in English, so the file is easy to check by hand.
  Unnamed speakers ("first speaker", "he") are never characters.
- Terms: character names, places, brands, titles, ranks, technical words, each with
  action = "keep" (English), "transliterate" (Thai spelling in th) or "translate"
  (Thai meaning in th)

Write to bible.md and glossary.toml, refusing to overwrite existing files without
--force, so hand edits survive. Then translate: every request carries the whole bible
(it is short by design) and only the glossary entries relevant to that batch.

Reading them back: both files are read again at the start of every run, so a hand edit
applies to the very next run with no rebuild step. A missing file is a normal state (a
one-line hint suggests --bible-only); an unreadable bible or a broken glossary stops the
run before any API call, naming the file and term, rather than translating with notes the
translator cannot trust. --bible-only does not read the files it is about to replace, so
--force can repair a broken one. The --dry-run estimate counts the bible once per batch.


### VERIFICATION PASS (after translation, report only)

Flags written to report.txt, never auto-fixed:
- Leftover English words not in the glossary's "keep" list
- Glossary terms present in the source but missing or inconsistent in the Thai
- Tag count mismatch with source
- Empty or suspiciously short/long output
- Line count changed: a two-line entry that came back as one line, or the reverse
- Speaker dashes lost: an entry whose "- " lines came back without them, which blurs
  who is speaking

Entries dropped by HI stripping are not flagged; the report notes how many were removed
so a surprisingly low entry count is explainable.

Optional --fix: re-translate flagged lines only, with a stricter prompt.
Optional --review: a second LLM pass that sees only flagged lines plus context and
proposes corrections (written to the report, applied only with --fix).


### CLI

  python translate.py input.en.srt            -> input.th.srt
  python translate.py *.en.srt                several files in one run: one client, one
                                              progress header per file, per-file summary and
                                              a total at the end. A file that crashes is
                                              reported and the rest still run (exit 1).
                                              The whole list is checked before any API call;
                                              any problem aborts the run (exit 2) rather than
                                              translating half of it: missing files,
                                              directories, the same file twice, two inputs
                                              writing the same .th.srt, and inputs that
                                              already look translated (*.th.srt).
  --force                                     accept *.th.srt inputs anyway
  --limit N / --range A-B                     translate only part of the file (dev/testing).
                                              With several files it applies to every one.
                                              --range uses real SRT indexes, inclusive. The output
                                              file holds only the selected entries; context is
                                              still read from the whole file. Not merged into a
                                              previous full translation.
  --dry-run                                   show batch count and estimated tokens/cost, no API calls
                                              and no API key needed
  --bible-only                                build bible + glossary, then stop.
                                              Refuses to overwrite existing files
                                              without --force (hand edits).
                                              --limit/--range restrict what it reads.
  --strip-hi                                  drop [sound effects]/(descriptions) from output
  --fix, --review                             see Verification
  --bilingual                                 English above Thai
  --config path.toml
