Project: KhamSap - "Thai subtitles with a memory". Repo directory: khamsap.

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
 18. [x] Leftover English, glossary consistency, tag mismatch (reading speed DROPPED: not a
     priority, may add later - do not build it)
     Also report:
     - line count changed: a 2-line entry back as 1 line (not hypothetical: a model
       merged one in an early live run, on a file where a third of entries are multi-line).
     - speaker dashes lost: losing "- " on a turn blurs who speaks.
     -> verify.py: Flag(index, kind, message, source, translation) and
        Verification(flags, checked) with by_kind / entries / for_index; KINDS fixes the
        order so a report looks the same every run. verify_file(translated, terms) takes
        anything with .results + .sources (pipeline.TranslatedFile is one), never raises
        on bad content, sorts by entry then kind, and logs (never flags) a result whose
        source is missing.
        check_entry() is the unit: ok=False -> one "failed" flag carrying the retry reason,
        an empty translation -> "empty", and a non-verbal source ("Mm-hmm",
        "[MUSIC PLAYING]") stops there so it is never nagged for staying untranslated.
        Checks, each its own function: tag_mismatch (multiset of real tags, so a tag the
        model dropped and restore could not put back is visible, and an invented {N} shows
        as an extra tag), glossary_flags (keep -> word must still be there, else
        "glossary_missing"; transliterate/translate -> th must appear, else
        "glossary_inconsistent" when the English stayed, "glossary_missing" otherwise),
        leftover_english (Latin words, apostrophes kept; words covered by ANY term are left
        to the glossary check so a name is flagged once), length_flags (advisory:
        MIN_LENGTH_RATIO 0.15 / MAX_LENGTH_RATIO 4.0, not applied below 4 source chars),
        plus line_count and dashes_lost from layout.line_count / layout.dash_count.
        -> layout.py: line_count() (non-blank) and dash_count() (dash behind tags counts);
           source_line_count() now delegates to line_count().
        -> tags.py: strip_markup() so word/length checks ignore tags, placeholders and
           music notes.
        -> pipeline.py: Prepared.sources keeps the text each entry came from (after HI
           stripping, CRLF normalised, tags intact) and TranslatedFile.sources exposes the
           ones that produced results, so verification needs nothing recomputed. Cached
           entries carry their source too.
        tests: 63 new = 563 passing (tests/test_verify.py 50, including six end-to-end
        pipeline cases: sources kept, clean verify, failure flagged, HI-cleaned source,
        cached rerun; 11 parametrised layout cases for line_count/dash_count; 2 tags).
        Sanity check on a 9-entry realistic sample: only the name left in English without
        a glossary term was flagged.
        Known: length ratios are heuristics tuned for subtitles, not measured against a
        corpus; english_left flags Latin text on purpose (a kept on-screen word needs a
        "keep" term to be accepted).
 19. [x] report.txt only, no auto-fix
     -> verify.py: report_path() (Movie.en.srt -> Movie.report.txt, ".en." dropped like
        the other sidecars), render_report() and write_report(); layout_summary() puts the
        repairs on one line with the merge rate ("2 of 5 multi-line entries re-split
        (40%, 1 dash, 1 proportional), 1 entry with extra lines, 1 speaker dash put back"),
        which is what item 13 wanted the report for.
        Report layout: source/output/model/created, "Checked N entries, flagged M" plus a
        count per kind in KINDS order (or "nothing flagged"), the dropped count, the layout
        line, then one block per flag: [index] kind, reason, original and translation with
        their line breaks kept. Flags are grouped by entry and the report ends with a
        newline. All text is UTF-8 and written atomically.
        -> translate.py: translate_one() now returns (result, target, report) and calls
           write_report_for() (verify_file + render + write). The main loop still prints the
           summary, says "N entries left in English" when some failed, and always prints
           "report: PATH" on stderr. A report that cannot be written (read-only folder)
           logs a warning, returns None and leaves exit status alone: the .srt is the
           product. --dry-run and --bible-only never write one.
        -> srtio.py: write_text() (atomic UTF-8, same temp-file path as write_srt).
        Decisions: the report is written on every translating run, also when nothing is
        flagged, so the merge rate and dropped count are on record; verification runs on
        cached entries too (sources come from the cache-aware pipeline); the summary line
        is unchanged apart from the report path, so the report stays the only new output.
        tests: 38 new (4 srtio.write_text, 23 report/layout in test_verify, 11 CLI) = 601
        passing. Live-ish check with the fake client: a clean 4-entry run printed a report
        with no flag blocks; a failed entry produced "Flags: failed 1" and its block;
        strip_hi run reported "Dropped before translation: 1 entry".
        Known: flag counts by kind are totals across entries; the report does not repeat
        the bible/glossary contents. No --fix/--review yet (items 20-21), so the report is
        read by a human for now.
        Phase 4 complete.

Phase 5: Extras
 20. [x] --fix (stricter prompt, flagged lines only)
     Agreed with user: every flagged entry is fixed (all kinds, not just hard ones), and
     the fix runs in the same invocation right after the first pass, so running --fix on
     an already translated file costs only the fix pass (the first pass is cached).
     -> llm.py: FIX_RULES, appended to the system prompt when build_messages(strict=True):
        fresh wording, Thai only, glossary spelling exact, same IDs/lines/placeholders.
     -> runner.py: strict=False parameter, forwarded to build_messages; retries keep it.
     -> batcher.py: select_range(indexes=...) keeps exactly those entries (after range,
        before limit), which is the selection --fix needs.
     -> pipeline.py: translate_subtitles(indexes=, ignore_cache=, seed=); ignore_cache
        re-requests a cached entry but keeps its text as context, and still writes the new
        result back through the cache; seed feeds first-pass Thai in as context only.
        retranslate_flagged(): verify -> re-request the flagged indexes with strict=True,
        cache on, seed = the successful first-pass Thai (an English fallback is never
        seeded as "TH:"); merge only ok results and keep the earlier text for a line that
        fails again (warning); add usage; set TranslatedFile.fixed; rebuild subtitles.
        translate_file(fix=True) runs it after the whole file and before writing, so the
        .srt is never half-fixed. --limit/--range are not re-applied in the fix pass,
        because the flagged entries are a subset of what the run translated.
     -> verify.render_report(): "Fix pass: re-translated N entries (all clean now)" or
        "(X clean, Y still flagged)", so the report always describes the shipped file.
     -> translate.py: --fix flag, passed through translate_one().
     Tests: 22 new (3 batcher, 2 llm, 2 runner, 9 pipeline, 3 verify, 3 CLI) = 623 passing.
     Fake-client run: a mixed file re-requested only the flagged line, with the correction
     prompt and the first pass as context; the fixed text was written and cached, a rerun
     made 0 calls, and a fix that failed again kept the earlier Thai and said so in the
     report.
     Known: a fatal error during the fix pass aborts before the .srt is written; the first
     pass is cached, so rerunning costs nothing. PROMPT_VERSION stays 1: a fixed line is
     cached as an ordinary result, which is what a later run should keep.
 21. [x] --review second pass
     Agreed with user: the review reads the flagged lines and puts concise suggestions near
     the top of report.txt. Decision: the suggestions are advice only and are not cached or
     applied verbatim; --fix stays the only thing that changes the .srt (it re-translates
     the same flagged lines). With --fix --review the fix runs first, review then looks at
     whatever is still flagged, and both appear in the same report.
     -> llm.py: REVIEW_RULES; build_messages(current=...) adds a
        "[CURRENT TRANSLATIONS - flagged, improve or repeat]" section and switches the
        system prompt to the review rules (review wins over --fix's correction rules).
        Same tagged wire format, so a suggestion is validated and re-wrapped like any reply.
     -> runner.py: current= parameter, forwarded to build_messages.
     -> pipeline.py: translate_subtitles(current=...) passes each batch its own current Thai;
        review_flagged() re-requests only the flagged indexes (cache=None so advice is never
        stored, seed = the successful first pass, limit/range not re-applied), drops
        suggestions identical to the current line, and records TranslatedFile.reviewed /
        .suggestions. translate_file(review=True) runs it after fix, before writing.
     -> verify.render_report(): "Suggestions (from --review, nothing applied):" with one
        line per entry, "  [12] <Thai>"; multi-line suggestions collapse to "a / b"; a
        review that changed nothing reads "none, it agreed with every flagged line". Placed
        after the flag counts, before dropped/layout, so it is read first.
     -> translate.py: --review flag, passed through translate_one().
     Tests: 21 new (3 llm, 1 runner, 8 pipeline, 6 verify, 3 CLI) = 644 passing.
     Fake-client run: only the flagged line was reviewed, with its current Thai and the
     first pass as context; the suggestion appeared at the top of the report; the .srt and
     the cache were unchanged; a repeated line produced no suggestion; --fix --review fixed
     the line and made no review call; a failed fix was followed by a review of the same
     line.
     Known: review costs one call per batch of flagged lines (same max_retries budget);
     its suggestions are not machine-applied, so applying one is a hand edit for now.
 22. [x] Other providers (different base URL + key env var)
     Decided with user: support OpenAI-compatible endpoints only (base_url + key_env were
     already config), no adapters for non-compatible APIs for now. Revisit later to add
     Anthropic/Gemini/Azure adapters if a real need appears.
     What was already true: the client is the OpenAI SDK with base_url/key_env from config,
     and no OpenRouter-specific fields are sent, so e.g. DeepSeek direct, Groq, a local
     vLLM or Ollama should work by editing config alone.
     -> llm.py: error texts no longer say "OpenRouter" ("rate limited by the API", "the API
        rejected the API key (check the variable named in [llm] key_env)", "the API
        returned HTTP ..."). Added looks_like_key() (used by load_api_key and config);
        OpenRouterClient takes extra_headers and passes them as OpenAI default_headers.
        Class name kept: renaming would churn imports for no behaviour.
     -> config.py: [llm] extra_headers table (string -> string), passed to build_client;
        refused: a non-table, non-string values, an Authorization header, and any value
        that looks like an API key (message never echoes it).
     -> config.toml / SPEC: a short "another provider" example and the provider rules.
     NOT done, on purpose: no adapter layer, no per-provider settings, no extra live tests.
     A provider that needs a non-Bearer auth header or a different request body (Anthropic,
     Gemini native, Azure with api-key + api-version) still needs real work.
     Tests: 13 new (7 config: default/table/type/authorization/key-shaped/plumbing;
     6 llm: default_headers, none, looks_like_key cases) = 657 passing.
 23. [x] Easy to understand user manual
     Agreed with user: README.md at the root, short sections, simple English, every command
     with a one-line explanation, plus four short sections (report, bible/glossary, other
     providers, troubleshooting + exit codes). No internals, no dev setup.
     -> README.md (170 lines): what it does, install (uv and plain venv), first run,
        commands (basic, several files, --dry-run, --bible-only, --force, --fix, --review,
        --limit, --range, --output, --config, -v, and strip_hi as a config setting), the
        five files it makes and their sidecar naming, reading report.txt with the common
        flags, editing the bible/glossary (what re-translates what), another provider
        (base_url/key_env/model + extra_headers), troubleshooting, exit codes.
     -> SPEC.md: the CLI section no longer lists --strip-hi and --bilingual as flags; they
        are config settings (strip_hi works, bilingual is planned item 25 and warns).
        That was a SPEC/code mismatch, found while writing the manual.
     Tests: none (docs only); suite still 657 passing.
 24. [x] Discuss options for GUI
     Discussion only, no code, as agreed. Options weighed: Tkinter (+tkinterdnd2),
     PySide6/Qt 6, GTK4/PyGObject, Gradio/Streamlit, pywebview, Gooey. Recommendation if a
     UI is ever built: PySide6, one window (native drag-and-drop including Wayland, built-in
     file dialogs, worker thread + signals for long batches); Tkinter is the lightest but
     its drag-and-drop is an X11 shim and looks dated; Gradio is the quickest prototype but
     lives in a browser tab. Effort: ~8-15 focused hours for Tkinter, ~15-25 for a polished
     Qt/GTK v1, ~2 hours for a bare prototype. Controls map to existing flags
     (--fix/--review/--bible-only/--dry-run; output folder needs a small addition next to
     --output). "Build bible" is a mode, not a checkbox beside Fix/Review. Prep work when
     the time comes: extract a service.py from translate.py so CLI and GUI share pre-flight,
     notes, translation and report; add an output-folder option and, if wanted, a cancel
     event. Revisit later: no GUI is being built now.
 25. Bilingual output option (English line above Thai)
     Moved to the bottom at the user's request: both languages in one subtitle is a
     viewing preference, not needed yet. config [output] bilingual exists and warns that it
     is not implemented.
     Revisit later, not now: this item is parked and will be taken up again after the
     manual and the GUI discussion.

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

### KNOWN BUGS (from the PEN15 live tests)

Live test, plain README command on PEN15 (2019) S01E01 - 720p.en.hi.srt (535 entries, no
bible or glossary): exit 0, 11m45s, 74,728 tokens, ~$0.05 on the OpenRouter dashboard,
535/535 entries, timestamps and indexes intact, 19 flagged (1 failed, 18 english_left).
The user judged the Thai good. The five bugs below (1-5) come from that run; 6-8 came from
the S02E07-E12 run and 9-10 from the South Park film. Listed in the order they matter.

1. Mid-word line splits (layout.py). When the model merges a multi-line entry and the Thai
   has no space near the proportional split point, layout.py falls back to a character
   offset and cuts inside a word: 4 of 16 repairs did this ([266] เธ|อ, [275] แค|่,
   [292] เป็|น, [293] เ|ธอ). Suggestion: never split at a character; if no acceptable
   space is within a margin of the target, leave the entry merged and report it as not
   repaired. Add these four real cases to test_layout.py first, so the rule is measurable.
   Seen again on a whole film (South Park, 2026-10-29): 1521 entries, 118 layout repairs,
   of which 46 were proportional splits where the Thai had no space at the cut (the
   character-offset fallback), and 36 of those 46 cut inside a word: กํา|ลัง, ดูถ|ูกผม,
   เทอร์แร|นซ์, อย|ู่, ท|ี่, หีไ|ดโนเสาร์. So about one repair in three is broken, and it
   only triggers when the Thai has no spaces, which is the normal case, so this is the
   common path and not a corner. All 36 were fixed by hand (break moved to a word
   boundary; cache edited, .th.srt regenerated for 0 tokens). A cheap regression check
   for test_layout.py: after a repair no line may begin with a combining mark
   (ั ิ ี ึ ื ุ ู ็ ์ ่ ้ ๊ ๋) and no line may end with เ แ โ ใ ไ or ็. The damaged file had
   19 such lines; the corrected one has 0.

2. english_left noise when there is no glossary. 18 of the 19 flags were proper nouns and
   brands the model rightly kept (UGIS x12, AOL, Care Bear, Ace Ventura, M.A.C, DD,
   "yeah"), so a clean file looks like 18 mistakes; bracketed song titles were skipped
   entirely because bracket-only text counts as non-verbal. Suggestion: with no glossary
   keep list, report leftover English as one advisory line per file instead of one flag per
   entry, and keep per-entry flags for words the glossary expects to change. Treat
   capitalised words and acronyms as a separate, quieter kind.

3. A line that is only an English acronym can never pass validation. [347] "-UGIS.\n-UGIS."
   came back in English four times and stayed English with missing_thai, although UGIS is
   kept consistently everywhere else; a glossary "keep" term would not help, because the
   validator still demands Thai. Suggestion: let an entry whose source is entirely
   glossary "keep" terms, or a bare Latin acronym, pass without Thai and be flagged as
   kept. Cover it with a test in test_llm.py.

4. --dry-run underestimates tokens and cost. The estimate said ~17,301 tokens / $0.0122;
   the run used 74,728 tokens and cost ~$0.05 (4.3x and 4x), and the summary gives only one
   total so prompt versus completion (or reasoning tokens) cannot be told apart.
   Suggestion: print prompt and completion tokens separately in the summary and report,
   and calibrate the dry-run ratios against a few real episodes, or show a range. Keep the
   ratios as constants until the numbers improve.

5. Long runs are silent. Without -v the tool printed the bible hint and the final summary
   only; an episode takes ~12 minutes, so it looks stuck. Suggestion: always print a
   one-line "batch n/m" progress to stderr for real runs (keep -v for the detail), or at
   least a "translating 535 entries in 9 batches..." line at the start. Mention the choice
   in the README first-run section.

6. Three-line cues. A two-line source cue came out as three lines: E12 [427] "Oh, my God." +
   "‐ What? Stop. Stop." -> "‐ แม่เหรอ / โอ้พระเจ้า / ‐ อะไร หยุด หยุด", reported as
   line_count (2 in source, 3 in translation) and as "1 entry with extra lines" in the
   layout repairs line. Only 1 case in E12's 667 entries, but a 3-line cue overflows the
   safe area in most players, so it is visible corruption like bug 1. The user wants this
   fixed, in one of two places (pick one): before translation, by telling the model the
   line count is fixed and retrying a 3-line answer; or after translation, in layout.py, by
   merging or re-splitting back down to 2 lines. Prefer the layout.py repair: it is
   deterministic and costs no retry. Start by adding the real [427] case to test_layout.py.

7. Speaker labels: keep them or remove them? (User decision, still open.) Across S02E07-E12
   the "NAME:" prefixes are the overwhelming majority of english_left flags: 87 of 96
   blocks in E07, 64/76 in E08, 51/79 in E10, 29/51 in E09, 26/30 in E12, 3/4 in E11 - so a
   clean file looks like ~90 mistakes, the same noise as bug 2. Today the model keeps the
   Latin name and translates the line ("MAYA: Taking a nap!" -> "MAYA: งีบอยู่!"), and the
   validator then demands Thai for "MAYA". The user wants to revisit whether labels stay at
   all. If kept: teach verify.py/report that a leading "NAME:" is expected (reuse the
   keep-list or the capitalised-word quiet rule from bug 2) instead of flagging it. If
   removed: strip "NAME:" in a pre- or post-pass, say so in the prompt, the README and the
   report. Whichever way it goes, the choice needs a test and must not touch a cue whose
   only content is the label.

8. Proper nouns: keep English or transliterate? (Policy decision, still open.) Today the model
   keeps Latin script for anything it recognises and the validator flags it, so the same class
   of term is treated inconsistently run to run and the glossary is the only thing that ever
   decides it. Seen in S02E06-E12, grouped by the decision each group needs:
   - Brands, ordinary products: Stop and Shop, Canada Dry, Swarovski, Chanel, Claire's,
     Payless, Dippin' Dots, JBL, AIM, DDR. Some have a settled Thai spelling (ชาแนล,
     สวารอฟสกี้), most do not.
   - Titles of other works, which often have an official Thai title of their own:
     "Final Destination" -> ปลายทางมรณะ (E06 [175]), "Seven Nights in Japan" (E11 [280]),
     the song "Demirep" by Bikini Kill (E12 [12]). A literal transliteration here is worse
     than useless because viewers know the Thai title, not the English sound.
   - Acronyms and model numbers: AIM, DDR, ADD, JBL, K9, UGIS (bug 2), 21L4, Model 3B,
     "'63", and the bare letters from "grade A" (E06) and "K9"/"21L4" (E08). These should stay
     as they are; the flags on the bare letters are bug 2/3 noise, not a real choice.
   - Names spoken in dialogue, not labels: Ume, "Oomi", Hector, Sooji, Shuji, Maya, Anna,
     Karen, Becca, Lola, Yolanda (E09, E10). These came back in Latin script while the E01
     glossary says names are action = "transliterate" (Anna -> แอนนา), so the default and the
     glossary disagree. E10 also has the joke Anna mishearing Ume as "Oomi", so a blanket
     rule has to keep the two distinguishable (e.g. อุเมะ against อูมิ), and quoted
     pronunciations need their quotes kept.
   Decision needed: one default per group above, not one global rule, and it should live in
   the glossary actions that already exist (keep / transliterate / translate) so the bible
   pass fills it in and a hand edit still wins. The tool should not guess an official Thai
   title. Suggested starting point: brands keep unless the bible knows a Thai spelling,
   titles keep English (translating an official title from memory risks being wrong), acronyms
   and model numbers keep, in-dialogue names transliterate to match the glossary. Whichever
   way each group goes, verify.py has to know the answer or the report keeps flagging the
   choice as a mistake, and each group needs a test.

9. A nonsense line, reported as if it were a deliberate keep. South Park [841]
   "Bullshit." came back as "ทwerp!" - a Thai ท glued to a Latin fragment, meaningless.
   Correction to what was first assumed: the checker DOES catch Latin attached to Thai.
   _ENGLISH_WORD_RE has no word boundary, so it matches "werp" inside "ทwerp" and
   leftover_english() returns the flag (checked directly, not inferred). The gap is what
   the flag says and what happens next: it reads `english_left: werp`, which looks exactly
   like the 36 deliberate keeps in the same file (USO, V-chip, Windows, forensics), and
   english_left is advisory, so nothing is retried and the junk ships. Suggestion: add a
   distinct `mixed_script` check for Latin letters immediately next to Thai (never valid
   for this project), raise it as a failure so the entry is retried like missing_thai, and
   leave english_left for words separated by a space or punctuation. Test with the real
   [841] string.

10. A cue can inherit a line from the cue above (context bleed). Twice in the same film:
   [1405] began with a copy of [1404]'s "เพื่อน กูเจอคลิตอริสแล้ว", giving three lines
   for a two-line source, and [169] likewise opened with the previous refrain line. Both
   were fixed by hand. Suggestion: add an advisory check that a translation's first line
   is not identical to the previous entry's translation, and treat "more lines than the
   source" (already flagged as line_count) as a failure worth one retry rather than an
   advisory, since the extra line is nearly always bleed.

### TO-DO (ideas, not scheduled)

1. Concurrent batch requests. (User idea, 2026-10-29.) Today
   pipeline.translate_subtitles sends one request at a time in a plain
   `for number in range(1, batch_count + 1)` loop, so wall time is batches x
   (one request). A PEN15 episode (10-15 batches) takes roughly 5-10 minutes and
   the South Park movie (1521 entries, 26 batches) longer still; sending N
   batches at once to OpenRouter could bring that to 2-5 minutes.
   - What the OpenRouter docs say (checked 2026-10-29): paid models have no
     platform-wide requests-per-minute cap; the 20 rpm / 50-1000 per day figures
     apply to ":free" variants only. Most 429s are inherited from the upstream
     provider, which enforces its own rpm and tokens-per-minute per model, and
     a 429 may carry X-RateLimit-Limit / -Remaining / -Reset, or Retry-After
     when every provider it tried sent a hint (llm.py already raises
     RateLimited from a 429 and _retry_after() reads Retry-After).
     The likelier ceiling for this project is the *in-flight spending budget*:
     OpenRouter reserves an estimate for each running request (input tokens plus
     the completion tokens allowed by max_tokens, or a fixed per-request cap when
     max_tokens is unset, which is our case), so N concurrent batches reserve N
     caps at once and a low-balance key gets a 402 with
     error.metadata.limit_source = openrouter_in_flight_budget instead of a 429.
     Concurrency therefore has to be conservative by default (start at 4), and a
     402 must stop the run and report the reason, not be retried as a rate limit.
   - Hard part is context, not the network. The loop rebuilds each batch with
     `translated=` so context_before shows the Thai produced by earlier batches.
     Concurrent batches cannot see that, so either (a) snapshot the context
     before dispatching (source English, or cached Thai for a resume; keeps the
     run deterministic and costs nothing extra), or (b) add a second pass that
     re-sends only entries the earlier batch affected (costs money, so only if
     (a) measurably hurts). Start with (a).
   - Also to handle: a shared limiter plus one backoff so five threads do not
     hammer a 429ing endpoint; save the cache as each batch lands so an
     interrupt loses only the in-flight batches; tolerate out-of-order
     completions in the log and the "batch n/m" progress line; keep token and
     cost accounting summing the way it does now; make --fix and --review go
     through the same path. The same prompts are sent either way, so cost and
     the --dry-run estimate do not change, only wall time.
   - Config: a [batch] setting (e.g. concurrency, default 1) so the old
     sequential behaviour stays the default until the rate limit is known.
   - Tests: a fake client that records the peak number of calls in flight
     (assert it equals the setting, and 1 by default); results identical to a
     sequential run at concurrency 1; one failed batch does not cancel the
     others; the cache file is complete after each batch completes.

### DEV NOTES (for a fresh session)

Read AGENTS.md, then SPEC.md, then this file. Phase 1 is complete (items 1-10 plus the
post-phase line-break work). Phase 2 is complete (items 11-14). Phase 3 is complete
(item 15 pre-pass, item 16 reload the files each run, item 17 per-batch glossary +
cache keys). Phase 4 is complete: item 18 added the checks (verify.py) and item 19 writes
report.txt after every translating run. Phase 5 has started: item 20 (--fix) and item 21
(--review suggestions in report.txt) are done. Bilingual output is deferred to the end of
Phase 5 (now item 25) at the user's request and is parked for later. Item 22 (other
providers) is done for OpenAI-compatible APIs, with a note to revisit adapters later. The
old "saved settings" item was removed: config.toml already holds every setting worth
saving, and the few constants left (retry backoff, bible chunking, advisory length limits)
are deliberately not user settings. Item 23 (the user manual, README.md) is done, and item
24 (GUI) is a finished discussion: no UI now, PySide6 if it is ever built. Only item 25
(bilingual output) is left in Phase 5, and it is parked.

Environment
  - Python 3.12 venv in .venv        (uv python install 3.12; uv venv --python 3.12 .venv)
  - Install deps:   uv pip install -r requirements.txt       (runtime)
                    uv pip install -r requirements-dev.txt   (adds pytest)
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
  verify     check a finished file against its sources; returns flags, renders report.txt
             (with --review suggestions at the top), changes nothing
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
  - layout.py repairs line counts and speaker dashes; verify.py now flags any that
    remain (line_count / dashes_lost), plus tags, glossary, leftover English, length,
    empty and failed entries.
  - Items 18-19 done: TranslatedFile.sources maps entry index -> the text it was translated
    from (HI-stripped, tags intact), cached entries included; verify.verify_file(result,
    terms) returns the flags; verify.render_report(...) + write_report() write
    Movie.report.txt. translate.py does this in write_report_for() after every translated
    file, so the CLI and the report always see the same data. Nothing reads report.txt
    back: --fix (item 20) will need the flagged indexes, and can call verify_file() again
    or remember the Verification for the current run.
  - The summary line still has no flag count: the report path is printed on stderr instead.
    Add a count there only if it turns out to be missed.
  - bible.py and glossary.py are written (items 15-17); verify.py holds checks + report
    (items 18-19); pipeline.retranslate_flagged() is item 20 (--fix).
  - Item 20 done: --fix re-translates every flagged entry after the first pass, in the
    same invocation, with llm.FIX_RULES and ignore_cache=True. Fixed lines are merged only
    when valid (a second failure keeps the earlier text) and go into the cache, so a rerun
    without --fix keeps them. The report's "Fix pass:" line describes the shipped file.
    The fix pass uses the first pass's Thai as before-context (never the English fallback).
  - Item 21 done: --review re-requests the flagged lines with their current Thai, records
    suggestions on TranslatedFile.suggestions and prints them at the top of the report.
    Advice is never cached and never applied; --fix is still the only thing that edits the
    .srt. review_flagged() runs after the fix pass, so --fix --review fixes first and
    reviews what remains.
  - Bilingual output (English above Thai) is now the last Phase 5 item (25) and is parked
    for later, by request.
  - Item 22 done for OpenAI-compatible providers: base_url + key_env + model switch the
    endpoint, extra_headers covers providers that ask for headers, errors are neutral.
    Open thread: revisit item 22 to add adapters only if an Anthropic/Gemini/Azure key
    becomes necessary (different body/headers; not worth building speculatively).
  - Item 23 done: README.md is the user manual; SPEC's CLI list was corrected (no
    --strip-hi/--bilingual flags; strip_hi is config, bilingual is item 25 and warns).
  - Housekeeping (after the item 24 discussion): runtime dependencies stay in
    requirements.txt and pytest moved to requirements-dev.txt (which includes the runtime
    file); README gained a short Development section. Ignored caches were deleted once
    (__pycache__, tests/__pycache__, .pytest_cache, two fixture .cache.json artefacts from
    old runs); they come back on the next run or pytest, which is normal.
  - Item 24 done as a discussion: no GUI now; if one is built later, PySide6 single window
    is the recommendation and service.py is the prep work (see the item note above).
    Item 25 (bilingual output) stays parked for later.
  - Known bugs from the live PEN15 run are listed in the KNOWN BUGS section above. The
    layout mid-word split (bug 1) is the first one worth fixing: it corrupts visible text.
  - Settings audit (old item 23, removed): everything worth saving is already in
    config.toml ([llm], [batch], [output], [cleanup]). Left as code constants on purpose:
    retry backoff/jitter/temperature and the Retry-After cap (runner.py), bible chunk
    size/temperatures (bible.py), dry-run token ratios (pipeline.py), and the advisory
    length ratios (verify.py). None is a user preference; promote one only if a real run
    shows it needs tuning.
  - The external test file used in Phase 2 has been discarded. The suite is self-contained:
    tests/fixtures/ (simple, bom, windows, malformed, empty, edge_cases) covers every shape.
  - Item 15 decisions, now in SPEC: whole file in ~12k-char parts then one merge call;
    English prose with Thai forms in Thai script; actions keep/transliterate/translate;
    --bible-only only (a normal run never builds the files); bible digest in the cache key.
  - Item 15 does not touch config.toml: CHUNK_CHARS, the two temperatures and the re-ask
    count are constants in bible.py. Promote them to a [bible] section only if the live
    behaviour asks for it.
