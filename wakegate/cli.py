"""Command line: run, report, validate."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from wakegate.duration import parse_since
from wakegate.errors import ConfigError, StateError
from wakegate.redact import scrub


def main(argv=None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        return 0 if code is None else int(code)

    try:
        if args.command == "validate":
            return _validate(args)
        if args.command == "run":
            return _run(args)
        if args.command == "report":
            return _report(args)
    except ConfigError as exc:
        for message in exc.messages:
            print(f"config error: {scrub(message)}", file=sys.stderr, flush=True)
        return 2
    except StateError as exc:
        print(f"state error: {scrub(str(exc))}", file=sys.stderr, flush=True)
        return 1
    except BrokenPipeError:
        return 0
    print("config error: unknown command", file=sys.stderr, flush=True)
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wakegate",
        description="Evaluate cheap signals and wake an agent only when one fires.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="evaluate checks and wake targets")
    _add_config(run_parser)
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="evaluate and print; do not wake, write state, or append the run log",
    )

    report_parser = sub.add_parser("report", help="summarize the run log")
    _add_config(report_parser)
    report_parser.add_argument(
        "--since",
        default="7d",
        help="duration (7d, 12h, 30m) or ISO timestamp (default: 7d)",
    )

    validate_parser = sub.add_parser("validate", help="validate the config and exit")
    _add_config(validate_parser)
    return parser


def _add_config(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        default="wakegate.yaml",
        help="path to the config file (default: wakegate.yaml)",
    )


def _validate(args) -> int:
    from wakegate.config import load_config

    load_config(Path(args.config))
    print("ok", flush=True)
    return 0


def _run(args) -> int:
    from wakegate.config import load_config
    from wakegate.run import execute

    config = load_config(Path(args.config))
    return execute(config, dry_run=args.dry_run)


def _report(args) -> int:
    from wakegate.config import configured_log_path
    from wakegate.report import render_report

    config_path = Path(args.config)
    if config_path.is_file():
        log_path = configured_log_path(config_path)
    elif args.config != "wakegate.yaml":
        raise ConfigError(f"config not found: {config_path}")
    else:
        log_path = Path.cwd() / ".wakegate" / "runs.jsonl"
    try:
        since = parse_since(args.since, datetime.now(timezone.utc))
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    print(render_report(log_path, since), end="")
    return 0
