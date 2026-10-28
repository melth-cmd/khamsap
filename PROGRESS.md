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
 2b. HI stripping (hi.py) - NOT DONE, decided with user:
      remove bracketed sound descriptions only ([door slams], (laughs), [MUSIC PLAYING]);
      music markers and speaker labels stay. Entries left empty are dropped from the output.
      Config [cleanup] strip_hi, default off. SPEC.md line 152 still needs updating to match.
 3. Split into ~60-entry batches, never mid-entry
 4. Tagged-line wire format; send to OpenRouter; decode
 5. Validate by ID; retry only failed entries with backoff and Retry-After
 6. Reassemble with original timestamps; write .th.srt (UTF-8, optional BOM)
 7. API key from env var; model and settings in config.toml
 8. JSON cache saved after each batch (resume on rerun)
 9. Context lines (previous English+Thai, next English)
 10. --limit/--range and --dry-run
 Test on a tiny sample first.

Phase 2: Reliability polish
 11. Per-entry cache keys (source + model + prompt version + glossary)
 12. Rate-limit handling: jitter, Retry-After, timeouts
 13. Multi-line subtitles, speaker dashes, sound-effect brackets edge cases
 14. Multi-file batch processing

Phase 3: Story bible and glossary
 15. Pre-pass producing bible.md and glossary.toml
 16. Hand-editable files; reload on each run
 17. Inject bible (short form) and per-batch glossary entries into every request

Phase 4: Verification
 18. Leftover English, glossary consistency, reading speed, tag mismatch
 19. report.txt only, no auto-fix

Phase 5: Extras
 20. --fix (stricter prompt, flagged lines only)
 21. --review second pass
 22. Bilingual output option
 23. Other providers (different base URL + key env var)
 24. Saved settings are already covered by config.toml
