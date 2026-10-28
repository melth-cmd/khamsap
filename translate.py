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
        "-v", "--verbose", action="store_true", help="Show per-batch progress"
    )
    return parser


def translate_one(
    client: llm.ChatClient,
    source: Path,
    settings: config_module.Settings,
    output: Path | None = None,
    limit: int | None = None,
    range_: str | None = None,
) -> tuple[pipeline.TranslatedFile, Path]:
    """Translate a single file using the configured settings."""
    target = output or srtio.output_path_for(
        source, strip_en_suffix=settings.output.strip_en_suffix
    )
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

    if args.dry_run:
        return _dry_run(args, settings)

    if settings.output.bilingual:
        print("warning: bilingual output is not implemented yet; writing Thai only",
              file=sys.stderr)

    try:
        client = config_module.build_client(settings)
    except (config_module.ConfigError, llm.AuthError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    status = 0
    for name in args.files:
        source = Path(name)
        if not source.exists():
            print(f"error: {source} not found", file=sys.stderr)
            status = 2
            continue
        try:
            result, target = translate_one(
                client,
                source,
                settings,
                Path(args.output) if args.output else None,
                limit=args.limit,
                range_=args.range_,
            )
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the rest
            print(f"error: {source.name}: {exc}", file=sys.stderr)
            status = 1
            continue

        print(_summary(source, target, result))
        if result.failures:
            print(
                f"  {len(result.failures)} entr"
                f"{'y' if len(result.failures) == 1 else 'ies'} left in English "
                "(see report in a later step)",
                file=sys.stderr,
            )

    return status


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
