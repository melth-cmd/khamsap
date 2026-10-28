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
 13. [x] Multi-line subtitles, speaker dashes, sound-effect brackets edge cases
      Survey first, on a real episode-length HI-tagged file (179 entries, 76 multi-line
      after prepare): 47 all-dash, 48 with any dash, 0 blank-line entries, 0 dropped.
      -> layout.py: DASHES = "-" + en dash + em dash, so turns in any dash style are found,
         and a dash hidden behind a leading placeholder or HTML tag counts ({0}-Hello.,
         <i>-Hello.</i>). layout runs AFTER tags.restore, so that second form is the common one.
         The dash rule now fires when ANY source line is dashed (was: all), which fixes the
         "But short? / -Yeah..." shape; 2 such entries were in the surveyed file.
         _restore_dashes(): when EVERY source line is dashed and the model dropped one, put it
         back in that source line's style (spaced or tight), after any leading tag. Reported
         as Layout.dashes_restored -> EntryResult.dashes_restored -> TranslatedFile.restored_dashes.
         Never invented for mixed sources; never on extra_lines.
         Blank lines no longer count as lines (source_line_count and rewrap agree). Before, an
         entry with a blank line asked for a line the source did not have and rewrap gave up.
         Splits never land inside {N}, [brackets], (parens) or a music pair (6 of the 22
         bracket/music entries had in-span split candidates with the old rule).
      -> pipeline.prepare(): CRLF and bare CR normalised to LF, so layout counts the same lines
         the model saw.
      Evidence: identity rewrap is a no-op on all 179 sample entries. Worst case (model merges
      AND drops every dash): all 76 multi-line entries get the right line count back, 47 get
      their dashes restored, 0 remain merged. With dashes kept: 48 by the dash rule, 28
      proportional.
      tests: 14 new in test_layout.py, 5 in test_pipeline.py, plus fixtures/edge_cases.srt
      (dash turn, dash behind <i>, mixed, music, sign) run end to end (333 total).
      Not done: no chars-per-line limit; extra lines still only reported.
 14. [x] Multi-file batch processing
      The loop already existed (one client, per-file try/except, continue on error). This step
      made it safe to point at a glob.
      -> translate.py: check_inputs() runs BEFORE the client is built or any call is made, and
         aborts the whole run on: missing file, directory, same file given twice (symlinks
         resolved), two inputs that would write the same .th.srt (Movie.en.srt + Movie.srt),
         --output that would overwrite its own input, and inputs that look translated
         (*.th.srt; "python translate.py *.srt" would otherwise translate Thai into Thai).
         All problems are listed at once. --force accepts the last kind only.
      Progress header "[2/5] name.en.srt" on stderr when several files are given; per-file
      summary lines unchanged; a "total:" line at the end (files, translated, FAILED,
      entries, left in English, tokens). Single-file output is byte-for-byte the same as
      before (no header, no total). --limit/--range with several files warns.
      Exit status: 0 clean or entries left in English (unchanged rule, agreed), 1 when a file
      crashed, 2 for any pre-flight/config/usage problem.
      tests/test_translate.py: 15 new (348 total).

Phase 3: Story bible and glossary
 15. [x] Pre-pass producing bible.md and glossary.toml
      Decisions agreed before code: read the whole file in parts and merge; English
      prose with Thai suggestions in Thai script; actions keep / transliterate /
      translate; run ONLY with --bible-only, never overwrite without --force; a bible
      digest goes into every cache key (wired in item 17, the hook already exists).
      -> glossary.py: Term (action validated; trimmed; keep drops th), render / parse /
         load / unique. Writing escapes with JSON rules (valid TOML) and leaves Thai
         literal. Reading is strict: bad TOML, unknown action, spelled action without
         th, wrong field type and a repeated word (case-insensitive) all raise
         GlossaryError naming the term; unknown fields warn. Missing file -> [].
         Unique() is only for model output; hand edits get the error instead.
      -> bible.py: title_from_filename (drops .en. and release junk, splits the year),
         chunk_entries (<= CHUNK_CHARS 12000, never mid-entry, oversized entry alone),
         parse_json (fences and prose tolerated), notes + merge prompts,
         character/term normalisation, build (one part used as-is, several parts plus
         one merge call), write (glossary then bible, atomically, UTF-8).
         Model output is repaired, never trusted: unknown action -> transliterate when
         a Thai form exists else keep; spelled action without th -> keep; nameless
         entries skipped; duplicate people/terms keep the first; title/year always come
         from the filename (the model's are ignored). Placeholder speakers ("He",
         "First speaker (unnamed)", "the man on the phone") are forbidden in both
         prompts AND dropped in code, because a real multi-part run produced them.
      -> translate.py: --bible-only; check_bible_targets joins the other pre-flight
         checks (every existing bible/glossary reported at once, exit 2, no call);
         --force rebuilds them (hand edits lost, by request); --output rejected;
         --limit/--range restrict what is read. A normal run never builds a bible.
      Pre-pass temperature 0.2; a non-JSON reply is re-asked once at 0.1 with the bad
      reply attached, then BibleError exits 1. Transport/auth errors bubble to the CLI.
      tests: 96 new (23 glossary, 62 bible, 11 CLI/pre-flight) = 445 passing.
      Live single part: 14-line Thai family scene, 1 call, 5,954 tokens -> sensible
      bible (Mai / Somsak / Somchai with Thai registers) and 9 usable terms.
      Live multi part: 3 calls, 9,684 tokens after the fix; before it the merge invented
      "First speaker (unnamed)" and "He", which is what the guard now prevents.
      Known: a single-part glossary is not filtered for rare words (the merge call does
      that for multi-part files); a transport error loses the parts already read.
      A rerun without --force refuses and makes no call.
 16. [x] Hand-editable files; reload on each run
      Decisions agreed: this step makes only the bible live; the glossary is loaded and
      validated now and sent from item 17 (so no run pays for the whole glossary first).
      A broken glossary stops the run like config.toml does, before any API call.
      -> bible.py: load() (missing -> "", HTML comments stripped, utf-8-sig tolerated,
         unreadable -> BibleError), Notes (bible text, terms, paths, has_bible /
         has_glossary), load_notes(source) reads both sidecars fresh; nothing is kept
         between calls or runs.
      -> translate.py: load_notes_for() reads every input after check_inputs and before
         the dry run or the client, and reports ALL broken glossaries at once (exit 2,
         zero calls, no key needed). The bible is handed to the pipeline through the
         parameter that already existed, so every request carries it. The per-file summary
         gains "bible N chars"; one stderr hint per run when files have no bible
         ("no story bible for a.en.srt (optional; --bible-only builds one)"), and -v
         logs the file names, sizes and term count. --bible-only does not read the files
         it replaces, so --force repairs a broken glossary.
      -> pipeline.estimate(bible=...) counts the bible once per batch; _dry_run uses it.
      tests: 27 new (9 bible loader/Notes, 1 glossary BOM, 2 estimate, 15 CLI) = 472.
      Live: demo scene with bible + glossary -> -v reported "bible ... (1,788 chars),
      glossary ... (9 terms)", the request carried the bible, summary "bible 1,788 chars",
      and the Thai followed it (Mai calls him พ่อ). A broken glossary exited 2 naming the
      term, with no key set and no file written. Dry-run: appending 61 chars to the bible
      moved the estimate by 19 prompt tokens on the next run, so it is re-read every time.
      Known: until item 17 the bible is not in the cache key, so a bible edit does not
      re-translate cached lines (the CLI reload test deletes the cache for that reason).
 17. [x] Inject bible (short form) and per-batch glossary entries into every request
      -> glossary.py: matching() (whole word, case-insensitive, "Gandalf's" matches,
         "Mai" does not match "Maid", glossary order kept), for_prompt() renders the
         chosen terms as "Gandalf = transliterate: แกนดัล์ฟ" / "NASA = keep",
         fingerprint(terms, bible) is sorted by English word (reordering the file
         invalidates nothing) and is "" when there are no terms and no bible, so caches
         made before this step stay valid for files that still have neither.
      -> pipeline.py: batch_terms() / glossary_for_batch() are the one definition of
         "relevant": words in the batch's own items, never the context lines. Each batch
         sends only those terms; a batch using none gets no [GLOSSARY] section at all.
         translate_file builds the cache callable from fingerprint(matching(term, entry),
         bible): editing one term drops only the lines containing it, editing the bible
         drops the whole file (agreed; one file must not mix registers). estimate() counts
         the bible and each batch's glossary, so --dry-run matches what is sent.
         -v now reports ", N glossary terms" on the batch line.
      -> translate.py: hands notes.terms to the pipeline; --dry-run gives them to the
         estimate. The bible reached requests in step 16 through the same path.
      tests: 500 passing (28 new: glossary matching/rendering/fingerprint, per-batch
      selection, cache invalidation by term and by bible, estimate, CLI).
      Live (demo scene, bible + 9 terms):
        - full run: "Batch 1/1: 14 entries, 9 glossary terms", 1,898 tokens;
        - rerun: 14 cached, 0 tokens;
        - Toyota changed to transliterate: "13 cached, 1 stale", "1 entry, 4 glossary
          terms", and โตโยต้า appeared in that line of the output;
        - one bible line added: all 14 stale (2 sent with --limit 2), so the digest works.
      Without a bible or glossary nothing changes: no sections, no terms, same output
      (confirmed live: 456 tokens for the 3-line fixture and one hint line).
      Phase 3 complete.

Phase 4: Verification
 18. Leftover English, glossary consistency, tag mismatch (reading speed DROPPED: not a
     priority, may add later - do not build it)
     Also report:
     - line count changed: a 2-line entry back as 1 line (not hypothetical: a model
       merged one in an early live run, on a file where a third of entries are multi-line).
     - speaker dashes lost: losing "- " on a turn blurs who speaks.
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

### PHASE 2 FINAL VERIFICATION (end of session)

  Automated: 349 unit tests pass (pytest -q, live excluded). All modules compile.
  Live (real OpenRouter, key from .env, never printed):
    - pytest -m live: 1 passed (3 entries, all Thai).
    - A real episode, entries 88-95 (8 entries, 6 of them two-line speaker turns):
      0 failures, line counts 8/8 correct, dashes present on every dashed entry, Thai
      natural. The raw cached replies already had the right line count, so layout had
      nothing to repair that time; the repair paths are covered by unit tests.
    - The same episode, entries 12-15: the only three-line entry (with <i> on every
      line) came back as three lines with tags restored per line.
    (That test file has since been discarded; the repo fixtures cover the same shapes.)
    - Rerun of the same slice: 0 tokens, byte-identical output (cache v2 keys work).
    - Two-file run: progress headers, per-file lines, "total: 2 files 2 translated
      6 entries 1,161 tokens", exit 0. Cache sidecar written next to the source.
  CLI hiccups found and fixed: "1 entries" in --dry-run (now singular).
  Pre-flight confirmed against a real shell: collision + *.th.srt + missing file are all
  reported together, exit 2, nothing written and no API call. No args: exit 2.
  Secret check: the only key-shaped string in git history is a 30-char synthetic value in
  tests/test_llm.py (the "pasted into key_env" test). The real key is 73 chars, lives in
  .env only, is not in any tracked file, and does not share that prefix. .env is untracked.

### DEV NOTES (for a fresh session)

Read AGENT.md, then SPEC.md, then this file. Phase 1 is complete (items 1-10 plus the
post-phase line-break work). Phase 2 is complete (items 11-14). Phase 3 is complete
(item 15 pre-pass, item 16 reload the files each run, item 17 per-batch glossary +
cache keys). Next is Phase 4, item 18: the verification checks (report only, no
auto-fix); item 19 writes report.txt.

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
  bible      pre-pass: chunk the script, prompt + merge the notes, write both files
  glossary   terms: validate, render, parse, load; per-batch selection + cache fingerprint
  layout     rewrap translated text to the source line count (dash / proportional)
  pipeline   prepare -> batch -> translate -> restore -> rebuild -> write
  config     config.toml loading/validation, build_client
  translate  argparse CLI, multi-file pre-flight and run summary

Open threads carried forward
  - Item 17 done: each batch gets only the terms whose word is in its own items
    (pipeline.batch_terms), the cache callable is
    glossary.fingerprint(matching(term, entry), bible), and --dry-run counts both.
    Editing a term re-translates the lines using it; editing the bible re-translates the
    file. With no notes the fingerprint is "" so older caches stay valid.
  - Item 16 done: both files are read once per run by bible.load_notes(); a broken
    glossary exits 2 before any call; missing files get one hint per run. A normal run
    still never calls the pre-pass (agreed).
  - A cache written before step 17 for a file that has a bible or glossary is stale once
    (the key gains the notes digest). With no notes, nothing is invalidated.
  - PROMPT_VERSION is still 1 and was deliberately NOT bumped for the line-break prompt rule:
    old entries are repaired on read by layout.py, so bumping would have thrown away every
    cache for a layout fix.
  - No max chars-per-line rule exists. layout.py splits at the source's break points only.
  - layout.py repairs line counts and speaker dashes; both are flagged per entry
    (rewrapped / dashes_restored) for the item 18 report.
  - report.txt is not written yet (item 19). Failures and rewraps live in
    EntryResult.reason / .rewrapped and TranslatedFile.failures / .rewrapped.
  - verify.py is still empty; bible.py and glossary.py are written (items 15-17).
  - The external test file used in Phase 2 has been discarded. The suite is self-contained:
    tests/fixtures/ (simple, bom, windows, malformed, empty, edge_cases) covers every shape.
  - Item 15 decisions, now in SPEC: whole file in ~12k-char parts then one merge call;
    English prose with Thai forms in Thai script; actions keep/transliterate/translate;
    --bible-only only (a normal run never builds the files); bible digest in the cache key.
  - Item 15 does not touch config.toml: CHUNK_CHARS, the two temperatures and the re-ask
    count are constants in bible.py. Promote them to a [bible] section only if the live
    behaviour asks for it.
