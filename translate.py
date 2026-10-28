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
        "-v", "--verbose", action="store_true", help="Show per-batch progress"
    )
    return parser


def translate_one(
    client: llm.ChatClient,
    source: Path,
    settings: config_module.Settings,
    output: Path | None = None,
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
    )


def _summary(source: Path, target: Path, result: pipeline.TranslatedFile) -> str:
    tokens = result.usage.prompt_tokens + result.usage.completion_tokens
    parts = [f"{source.name} -> {target.name}", f"{len(result.subtitles)} entries"]
    if result.dropped:
        parts.append(f"{len(result.dropped)} dropped")
    if result.failures:
        parts.append(f"{len(result.failures)} FAILED")
    parts.append(f"{tokens} tokens")
    return "  ".join(parts)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(message)s",
    )

    if args.output and len(args.files) > 1:
        print("error: --output can only be used with a single input file", file=sys.stderr)
        return 2

    try:
        settings = config_module.load_settings(args.config)
    except config_module.ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

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
                client, source, settings, Path(args.output) if args.output else None
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
