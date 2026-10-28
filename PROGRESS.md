### BUILD ORDER

(Each phase must work, and have passing tests, before starting the next.)

Phase 1: Core translator, command line
 1. [x] Read .srt (utf-8-sig, charset fallback), parse with srt (ignore_errors=True, log skipped)
      -> srtio.py: decode_bytes / read_text / parse_subtitles / load_srt / compose_subtitles / write_srt
      Decoding: utf-8-sig -> charset detection -> fallbacks (latin-1 floor, never crashes).
      Writes are atomic; compose keeps original indexes (reindex=False, strict=False).
      Skipped blocks are counted with a timecode regex and logged (srt drops them silently).
      tests/test_srtio.py: 20 passing. Fixtures: simple / bom / windows(cp1252) / malformed / empty.
      pytest.ini sets pythonpath=. so tests run from the project root.
 2. [x] Tag/placeholder handling (tags.py) with tag-count validation
      -> tags.py: protect / check / check_protected / restore (strict or best-effort)
      Placeholders are {0}, {1}, ... chosen for token cost; ASS blocks, HTML-ish tags and
      music notes are stripped. Literal {N} braces in the source are protected too, so our
      numbering can never collide with text the model sees.
      Mismatches raise TagMismatch (missing / duplicate / unknown) for the retry layer to use.
      Bracket effects and speaker dashes are content, not tags: they pass through untouched.
      tests/test_tags.py: 23 passing.
 2b. [x] HI stripping (hi.py)
      -> hi.py: strip_hi(text) -> HiResult(text, removed) with .changed and .dropped
      Removes [] and () descriptions only; music markers and speaker dashes stay.
      Capitalised brackets are treated as names and kept ([Maya], [Mr. O]); all-caps
      brackets are still removed ([MUSIC PLAYING]). Mixed-case cues like [Anna chuckles]
      survive; accepted trade-off, deleting a speaker name is worse.
      Lines left holding just a dash are removed; nothing spanning a line break is eaten,
      and text with no descriptions is returned unchanged (whitespace included).
      .dropped means no letters/digits remain, i.e. the entry must be omitted from output.
      tests/test_hi.py: 24 passing. SPEC.md updated: design item 4, config [cleanup],
      prompt rules, --strip-hi flag and verification note.
 3. [x] Split into ~60-entry batches, never mid-entry
      -> batcher.py: SourceEntry / ContextLine / Batch, select_range, parse_range, make_batches
      entries = full cleaned file (context source); selection = what to translate this run.
      Context comes from the full list, so --range still sees lines outside its window;
      before-lines carry Thai when `translated` supplies it, after-lines never do.
      Context is not counted toward batch size. Real SRT indexes for --range (inclusive).
      Bad size/context/selection raise instead of looping or silently dropping entries.
      tests/test_batcher.py: 41 passing (100 total).
 4. [x] Tagged-line wire format; send to OpenRouter; decode
      -> llm.py: encode_entries / decode_entries / build_messages / load_api_key / OpenRouterClient
      Wire format is "[12] text", continuation lines join the entry above; duplicates keep the
      first copy and are reported, empty entries and untagged replies are reported, not crashed on.
      Errors are typed: TransportError, RateLimited (with .retry_after), AuthError, BadResponse.
      SDK retries are disabled (max_retries=0); retrying is step 5's job.
      Key comes from the environment only, read via load_api_key() at run time, never logged.
      tests/conftest.py FakeClient + tests/helpers.py; tests/test_llm.py: 31 passing (131 total).
      Live check (costs money, needs the key): pytest -m live  # skipped unless OPENROUTER_API_KEY is set
      Live smoke test passed with deepseek/deepseek-v4.1-flash-1017: 3 entries, all came back Thai.
      config.toml key_env holds the variable NAME; a key-shaped value there is rejected without
      echoing it (this happened once, see llm._SECRET_SHAPED_RE).
      .githooks/pre-commit blocks staged keys and secret-bearing files; enable per clone with
      git config core.hooksPath .githooks
 5. [x] Validate by ID; retry only failed entries with backoff and Retry-After
      -> llm.py: scripts_in / has_thai / is_non_verbal / validate; runner.py: translate_batch
      Validation: missing, extra (context echoes ignored), empty, missing_thai, foreign_script
      (Han/Kana/Hangul/Cyrillic the source did not have), tag_mismatch, bad_response.
      Retry: only failing IDs re-sent, temperature 0.1 after the first call, one shared budget
      of max_retries=3, exponential backoff 2^attempt +/-25% jitter, Retry-After honoured
      (capped at 120s), sleep/rand injectable so tests never wait.
      Exhausted -> EntryResult(ok=False, reason=..., text=original English), never cached.
      AuthError is fatal and never retried; no fallback model, no extra prompt line.
      tests/test_runner.py: 24 passing (156 total).
 6. [x] Reassemble with original timestamps; write .th.srt (UTF-8, optional BOM)
      -> pipeline.py: prepare / translate_subtitles / translate_file ; srtio.output_path_for
      Order: parse -> HI strip -> protect tags -> batch -> translate -> restore -> rebuild -> write.
      Dropped entries (HI or no words left after tag protection) are excluded from batches and
      from the output, so the file may have fewer rows than the source.
      Timestamps, indexes and proprietary fields are copied from the source entry, never rebuilt.
      Context-before Thai is fed forward batch to batch. Results sorted by index; failures kept
      as English with ok=False for the report.
      Output naming: ".en." becomes ".th." wherever it stands (Show.en.hi_partial.srt ->
      Show.th.hi_partial.srt), else ".th" is inserted before the extension. Sidecars
      (cache) drop the tag instead: Show.en.hi_partial.srt -> Show.hi_partial.cache.json.
      tests/test_pipeline.py: 19 passing (175 total).
      Live end-to-end run OK: <i> tags restored, multi-line entry translated, no failures.
      Known gap: the model may join a two-line entry into one line; line breaks are not
      preserved or checked yet. Candidate for the step 18 verification report.
 7. [x] API key from env var; model and settings in config.toml
      -> config.py: LLMSettings/BatchSettings/OutputSettings/CleanupSettings/VerifySettings,
         settings_from_dict / load_settings / build_client. translate.py: argparse CLI.
      Every setting has a default, so a missing config beside the script only warns; an explicit
      --config that is missing is fatal. Values are type/range checked on load (temperature 0-2,
      size >= 1, known codec, wire_format "json" rejected until built). Unknown keys warn.
      Key read from [llm] key_env at build_client time only; missing key exits 2 with a message.
      CLI flags so far: FILE... , --config, --output (single file only), -v.
      Output naming honours [output] strip_en_suffix. Failed entries reported but exit 0.
      tests/test_config.py 31 + tests/test_translate.py 10 passing (211 total).
      Live CLI run OK: no key -> exit 2 with hint; with key -> demo.th.srt written, 721 tokens.
 8. [x] JSON cache saved after each batch (resume on rerun)
      -> cache.py: Cache(path, model, prompt_version) with load / get / put / save / contains.
      File sits beside the source (movie.en.srt -> movie.cache.json) via srtio.sidecar_path.
      Stores {version, model, prompt_version, entries: {index: {source, text}}}; source is the
      protected text, text is the finished Thai with tags restored.
      Validity: model, prompt version and that entry's source must match, else the entry (or the
      whole file) is ignored. Corrupt/short files warn and start empty.
      Saved atomically after every batch; no-op saves do not touch the file.
      Pipeline seeds context-before from cached Thai, and only uncached entries are batched.
      Failed entries are never cached. use_cache=False bypasses it entirely.
      tests/test_cache.py 14 + 7 pipeline tests passing (231 total).
      Live: first CLI run 418 tokens, second run 0 tokens and identical output.
 9. [x] Context lines (previous English+Thai, next English)
      Built earlier (batcher.py step 3, cached-Thai context in step 8, config wiring in
      step 7); this step verified the whole chain end to end.
      -> translate.py passes [batch] context_before / context_after into pipeline, which
         rebuilds batches each round so before-lines carry the Thai already produced.
      Verified by tests/test_translate.py: context counts come from config, first batch has
      no before-block, before-lines carry "TH:", after-lines never do, and context lines
      never appear in the output file (233 tests passing).
 10. [x] --limit/--range and --dry-run
      -> pipeline.estimate() + limit/range_ params on translate_subtitles/translate_file;
         translate.py flags --limit, --range, --dry-run.
      --range uses real SRT indexes, inclusive, validated before any work starts. Output holds
      only the selected entries (no merging); context still comes from the whole file.
      --dry-run builds no client, needs no key, and prints entries, batches, ~tokens and ~cost
      using [llm] input/output_usd_per_mtok. Token estimate is chars/3.2 input and chars/2.2
      output, labelled approximate.
      tests: 15 new (246 total). Live: dry-run without a key reports 3 entries / 1 batch /
      ~$0.0001; --range 3-3 wrote a one-entry file from cache at 0 tokens.
      Phase 1 complete.
 Test on a tiny sample first.

Phase 2: Reliability polish
 11. [x] Per-entry cache keys (source + model + prompt version + glossary)
      -> cache.py: entry_key(source, model, prompt_version, glossary) -> sha256, 32 hex chars.
         Cache(path, model, prompt_version, glossary=callable(index, source) -> fingerprint);
         the callable defaults to no_glossary (""), so it is the Phase 3 plug-in point.
         Stored per entry as {key, source, text}; key_for() is what get/put/load compare against.
      No more whole-file gate: the header still records model/prompt for humans, but a key
      mismatch is just a miss, logged as "N cached entries are stale". Editing one glossary
      term drops only the lines whose fingerprint changed; switching back to an older model
      hits its old entries again. Index is NOT in the key, so a line keeping its text can move.
      FORMAT_VERSION 2. Version 1 caches are ignored and start empty (agreed with user: the
      existing translations were test artefacts, no migration written).
      tests/test_cache.py: 23 passing (288 total).
 12. [x] Rate-limit handling: jitter, Retry-After, timeouts
      Most of the mechanism existed since step 5; this step closed the holes found by audit.
      -> llm.py: RetryableError, the shared base of TransportError and RateLimited, both of
         which carry .retry_after. 5xx and 408/409/425 now map to TransportError (retryable) instead
         of the fatal LLMError, so a gateway hiccup no longer kills a whole run; 4xx stays fatal.
         _retry_after() accepts seconds or an HTTP date, and ignores anything non-finite or
         negative, so a junk header falls back to backoff instead of a weird sleep or a crash.
      -> runner.py: one except llm.RetryableError block (was two copies) with _wait_for():
         Retry-After wins over backoff, still capped at max_retry_after (120s); unusable hint
         -> exponential backoff with +/-25% jitter. 429, 5xx and timeouts share one budget.
         Rate-limit/transport retries now increment EntryResult.attempts (the report counted
         only answered calls before).
      Timeouts: SDK timeout from [llm] timeout_s (>= 1s, config), max_retries=0 on the SDK so
      only the runner retries; now asserted in a test.
      Not done on purpose: no jitter on the Retry-After path (only the server knows, and this
      is a single-user tool, so there is no herd to spread); no separate connect timeout.
      tests: 14 in test_llm.py, 8 in test_runner.py (311 total).
 13. Multi-line subtitles, speaker dashes, sound-effect brackets edge cases
 14. Multi-file batch processing

Phase 3: Story bible and glossary
 15. Pre-pass producing bible.md and glossary.toml
 16. Hand-editable files; reload on each run
 17. Inject bible (short form) and per-batch glossary entries into every request

Phase 4: Verification
 18. Leftover English, glossary consistency, tag mismatch (reading speed DROPPED: not a
     priority, may add later - do not build it)
     Also report, from the PEN15 test run:
     - line count changed: a 2-line entry back as 1 line. 76 entries in that file are
       multi-line, so this is not hypothetical; the model joined one in a live run.
     - speaker dashes lost: 48 entries have "- " lines; losing the dash blurs who speaks.
     Reference numbers from that file: 179 entries, 76 multi-line, 46 with dashes.
 19. report.txt only, no auto-fix

Phase 5: Extras
 20. --fix (stricter prompt, flagged lines only)
 21. --review second pass
 22. Bilingual output option
 23. Other providers (different base URL + key env var)
 24. Saved settings are already covered by config.toml

### POST-PHASE-1: line breaks (A + D), agreed with user

  -> layout.py: rewrap(source, translated) -> Layout(text, method), method is
     "dash" / "proportional" / "extra_lines" / None. pipeline applies it on READ
     (cached entries included) and flags the result in EntryResult.rewrapped.
  Prompt: llm.SYSTEM_PROMPT now states the rule as a line count.
  Evidence: before the prompt line, stacked lines merged 4/4 and dashed 2/4 in live runs.
  After, 4 runs x 3 entries (2-line plain, 2-line dash, 3-line) kept the right count 12/12.
  Cache stores the RAW model output, so improving re-wrapping later still applies without
  retranslating. PROMPT_VERSION deliberately NOT bumped for this: old merged entries are
  repaired on read, and bumping would throw away every cache for a layout fix.
  Not implemented on purpose: max chars per line (no convention chosen yet); lines are
  split at the source's break points, not to a width.

### DEV NOTES (for a fresh session)

Read AGENT.md, then SPEC.md, then this file. Phase 1 is complete (items 1-10 plus the
post-phase line-break work). Phase 2: item 11 and 12 done, next is item 13 (multi-line/subtitle edge cases).

Environment
  - Python 3.12 venv in .venv        (uv python install 3.12; uv venv --python 3.12 .venv)
  - Install deps:   uv pip install -r requirements.txt
  - Tests:          .venv/bin/python -m pytest -q      (run from the project root;
                    pytest.ini sets pythonpath=. so imports resolve)
  - Live test:      pytest -m live     (excluded by default; needs OPENROUTER_API_KEY;
                    one real call, well under a cent)
  - Secret guard:   git config core.hooksPath .githooks   (already set in this clone)
  - API key: environment only. [llm] key_env holds the variable NAME, never the key.

Modules (one line each)
  srtio      read/parse/write, encoding fallback, output + sidecar naming
  tags       {0} placeholders: protect / check / restore
  hi         strip SDH descriptions; capitalised brackets ([Maya]) are kept
  batcher    batching, context lines, select_range (real SRT indexes)
  llm        OpenRouter client, wire format, prompt, validate (IDs/Thai/script/tags)
  runner     one batch: retry only failures, backoff, English fallback
  cache      per-entry JSON cache, atomic saves
  layout     rewrap translated text to the source line count (dash / proportional)
  pipeline   prepare -> batch -> translate -> restore -> rebuild -> write
  config     config.toml loading/validation, build_client
  translate  argparse CLI

Open threads carried into Phase 2
  - Item 11 done: the cache already takes a `glossary` callable. Phase 3 (item 15/17) only
    has to build glossary.py and pass it in wherever Cache(...) is built (pipeline.translate_file
    builds it today; translate_file has no pass-through parameter yet).
  - PROMPT_VERSION is still 1 and was deliberately NOT bumped for the line-break prompt rule:
    old entries are repaired on read by layout.py, so bumping would have thrown away every
    cache for a layout fix.
  - No max chars-per-line rule exists. layout.py splits at the source's break points only.
  - report.txt is not written yet (item 19). Failures and rewraps live in
    EntryResult.reason / .rewrapped and TranslatedFile.failures / .rewrapped.
  - verify.py, bible.py and glossary.py are still empty.
  - Test material from this phase lived in /tmp/opencode (PEN15 sample, edge-case files);
    /tmp may be cleared, so copy anything worth keeping.
