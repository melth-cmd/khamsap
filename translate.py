"""Command line entry point.

    python translate.py input.en.srt
    python translate.py *.en.srt --config my.toml

The API key comes from the environment variable named in [llm] key_env. It is
read only when the client is built, and never printed.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import batcher
import config as config_module
import llm
import pipeline
import srtio

log = logging.getLogger("translate")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="translate.py",
        description="Translate English .srt subtitles into Thai with an LLM.",
    )
    parser.add_argument("files", nargs="+", metavar="FILE", help="English .srt file(s)")
    parser.add_argument(
        "--config", metavar="PATH", help="Path to config.toml (default: next to this script)"
    )
    parser.add_argument(
        "--output", metavar="PATH", help="Write here instead of FILE.th.srt (one file only)"
    )
    parser.add_argument(
        "--limit", type=int, metavar="N", help="Translate only the first N entries"
    )
    parser.add_argument(
        "--range", dest="range_", metavar="A-B",
        help="Translate only entries with subtitle indexes A to B (inclusive)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show batches, tokens and estimated cost without calling the API",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Also accept inputs that look like existing translations (*.th.srt)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Show per-batch progress"
    )
    return parser


def planned_output(
    source: Path, settings: config_module.Settings, output: Path | None = None
) -> Path:
    """Where translating `source` would be written."""
    return output or srtio.output_path_for(
        source, strip_en_suffix=settings.output.strip_en_suffix
    )


def looks_translated(path: Path) -> bool:
    """True for names this program would produce: Movie.th.srt, Movie.en.th.srt."""
    return path.suffix.lower() == ".srt" and path.stem.lower().endswith(".th")


def check_inputs(
    files: list[str],
    settings: config_module.Settings,
    output: Path | None = None,
    force: bool = False,
) -> list[str]:
    """Every reason the file list cannot be translated, checked before any call.

    One batch run is a single instruction, so it is all or nothing: reporting a
    collision after three files were translated has already overwritten one.
    """
    problems: list[str] = []
    seen: dict[Path, Path] = {}
    targets: dict[Path, Path] = {}

    for name in files:
        source = Path(name)
        if not source.exists():
            problems.append(f"{source} not found")
            continue
        if not source.is_file():
            problems.append(f"{source} is not a file")
            continue

        resolved = source.resolve()
        if resolved in seen:
            problems.append(f"{source} is given more than once")
            continue
        seen[resolved] = source

        if not force and looks_translated(source):
            problems.append(
                f"{source} looks like an existing translation (name ends in .th); "
                "use --force to translate it anyway"
            )

        target = planned_output(source, settings, output)
        key = target.resolve()
        if key == resolved:
            problems.append(f"{source} would be overwritten by its own output")
            continue
        if key in targets:
            problems.append(
                f"{source} and {targets[key]} would both write {target.name}; "
                "translate them one at a time"
            )
            continue
        targets[key] = source

    return problems


def translate_one(
    client: llm.ChatClient,
    source: Path,
    settings: config_module.Settings,
    output: Path | None = None,
    limit: int | None = None,
    range_: str | None = None,
) -> tuple[pipeline.TranslatedFile, Path]:
    """Translate a single file using the configured settings."""
    target = planned_output(source, settings, output)
    return pipeline.translate_file(
        client,
        source,
        target,
        encoding=settings.output.encoding,
        strip_hi=settings.cleanup.strip_hi,
        model=settings.llm.model,
        size=settings.batch.size,
        context_before=settings.batch.context_before,
        context_after=settings.batch.context_after,
        max_retries=settings.llm.max_retries,
        limit=limit,
        range_=range_,
    )


def _summary(source: Path, target: Path, result: pipeline.TranslatedFile) -> str:
    tokens = result.usage.prompt_tokens + result.usage.completion_tokens
    count = len(result.subtitles)
    parts = [
        f"{source.name} -> {target.name}",
        f"{count} {'entry' if count == 1 else 'entries'}",
    ]
    if result.dropped:
        parts.append(f"{len(result.dropped)} dropped")
    if result.failures:
        parts.append(f"{len(result.failures)} FAILED")
    parts.append(f"{tokens} tokens")
    return "  ".join(parts)


def _dry_run(args, settings: config_module.Settings) -> int:
    """Report what the run would do. Needs no API key and makes no calls."""
    status = 0
    for name in args.files:
        source = Path(name)
        if not source.exists():
            print(f"error: {source} not found", file=sys.stderr)
            status = 2
            continue

        loaded = srtio.load_srt(source)
        estimate = pipeline.estimate(
            loaded.subtitles,
            strip_hi=settings.cleanup.strip_hi,
            size=settings.batch.size,
            context_before=settings.batch.context_before,
            context_after=settings.batch.context_after,
            limit=args.limit,
            range_=args.range_,
            input_usd_per_mtok=settings.llm.input_usd_per_mtok,
            output_usd_per_mtok=settings.llm.output_usd_per_mtok,
        )
        dropped = f", {estimate.dropped} dropped" if estimate.dropped else ""
        batches = "batch" if estimate.batches == 1 else "batches"
        print(
            f"{source.name}: {estimate.entries} entries{dropped} in "
            f"{estimate.batches} {batches}, ~{estimate.prompt_tokens:,} + "
            f"{estimate.completion_tokens:,} tokens, ~${estimate.cost_usd:.4f}"
        )
    return status


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(message)s",
    )

    if args.output and len(args.files) > 1:
        print("error: --output can only be used with a single input file", file=sys.stderr)
        return 2

    if args.limit is not None and args.limit < 0:
        print("error: --limit must not be negative", file=sys.stderr)
        return 2
    if args.range_ is not None:
        try:
            batcher.parse_range(args.range_)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    try:
        settings = config_module.load_settings(args.config)
    except config_module.ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    output = Path(args.output) if args.output else None
    problems = check_inputs(args.files, settings, output, force=args.force)
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 2

    if args.dry_run:
        return _dry_run(args, settings)

    if settings.output.bilingual:
        print("warning: bilingual output is not implemented yet; writing Thai only",
              file=sys.stderr)

    sources = [Path(name) for name in args.files]
    if len(sources) > 1 and (args.limit is not None or args.range_ is not None):
        print("warning: --limit/--range apply to every file given", file=sys.stderr)

    try:
        client = config_module.build_client(settings)
    except (config_module.ConfigError, llm.AuthError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    status = 0
    failed_files = 0
    entries = 0
    tokens = 0
    leftover = 0

    for number, source in enumerate(sources, 1):
        if len(sources) > 1:
            print(f"[{number}/{len(sources)}] {source.name}", file=sys.stderr)
        try:
            result, target = translate_one(
                client,
                source,
                settings,
                output,
                limit=args.limit,
                range_=args.range_,
            )
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the rest
            print(f"error: {source.name}: {exc}", file=sys.stderr)
            status = 1
            failed_files += 1
            continue

        entries += len(result.subtitles)
        tokens += result.usage.prompt_tokens + result.usage.completion_tokens
        leftover += len(result.failures)

        print(_summary(source, target, result))
        if result.failures:
            print(
                f"  {len(result.failures)} entr"
                f"{'y' if len(result.failures) == 1 else 'ies'} left in English "
                "(see report in a later step)",
                file=sys.stderr,
            )

    if len(sources) > 1:
        print(_total(len(sources), failed_files, entries, leftover, tokens))
    return status


def _total(files: int, failed: int, entries: int, leftover: int, tokens: int) -> str:
    """One line for the whole run, so 20 files do not hide the totals."""
    parts = [f"{files} files", f"{files - failed} translated"]
    if failed:
        parts.append(f"{failed} FAILED")
    parts.append(f"{entries} {'entry' if entries == 1 else 'entries'}")
    if leftover:
        parts.append(f"{leftover} left in English")
    parts.append(f"{tokens:,} tokens")
    return "total: " + "  ".join(parts)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
