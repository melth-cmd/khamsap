Read SPEC.md and PROGRESS.md first. Do one step at a time. Call existing functions, do not rewrite them. Add tests for every function. Add checkmark and short note in PROGRESS.md for each completed step.

API KEY NOTES: The API key currently in .env is NOT sensitive. It is a limited key with a low spend cap and it is set to expire soon, so a leak would be harmless and there is no need to scrub it from local files or worry about it appearing in logs. Keep the existing habits anyway (never print it, never commit it, never put it in config.toml or the cache) because they cost nothing and the next key may be real. The pre-commit hook still blocks key-shaped values.

