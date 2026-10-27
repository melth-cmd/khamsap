### BUILD ORDER

(Each phase must work, and have passing tests, before starting the next.)

Phase 1: Core translator, command line
 1. Read .srt (utf-8-sig, charset fallback), parse with srt (ignore_errors=True, log skipped)
 2. Tag/placeholder handling (tags.py) with tag-count validation
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
