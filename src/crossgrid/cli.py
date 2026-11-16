from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .scanner import (
    _validate_report_targets,
    report_exit_code,
    render_html,
    render_sarif,
    scan_project,
    _write_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crossgrid",
        description="Map Python OS support declarations, explicit CI runners and conservative source risks.",
    )
    parser.add_argument(
        "--version", action="version", version=f"crossgrid {__version__}"
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=".",
        help="Python project root (default: current directory)",
    )
    parser.add_argument(
        "--platform",
        action="append",
        choices=("windows", "linux", "macos"),
        help="target OS to include when metadata is incomplete; repeatable",
    )
    parser.add_argument("--html", help="write a self-contained HTML report")
    parser.add_argument(
        "--json", dest="json_path", help="write the machine-readable JSON report"
    )
    parser.add_argument("--sarif", help="write a SARIF 2.1.0 report")
    parser.add_argument(
        "--fail-on",
        choices=("none", "review", "warning", "error"),
        default="error",
        help="exit 1 for findings at or above this level (default: error)",
    )
    return parser


def _text(report: dict) -> str:
    summary = report["summary"]
    lines = [
        f"CrossGrid: {summary['findings']} finding(s) across {summary['python_files']} Python file(s)",
        f"Scope: {', '.join(report['support_scope']) or 'not declared'} ({report['scope_source']})",
        f"Test-like CI runners: {', '.join(summary['test_ci_platforms']) or 'none identified'}",
        "",
        "Platform coverage:",
    ]
    if report["platforms"]:
        for row in report["platforms"]:
            status = (
                "test runner found"
                if row["test_ci_present"]
                else "no test runner found"
            )
            lines.append(
                f"  {row['platform']:8} {status}; {row['platform_skip_count']} platform-related skip marker(s)"
            )
    else:
        lines.append("  No target OS matrix. Declare classifiers or pass --platform.")
    lines.append("")
    for finding in report["findings"]:
        lines.append(
            f"{finding['severity'].upper():7} {finding['rule_id']} {finding['file']}:{finding['line']} — {finding['message']}"
        )
        lines.append(f"        Evidence: {finding['evidence']}")
    if not report["findings"]:
        lines.append(
            "No review signals found. This is not proof of cross-OS compatibility."
        )
    lines.extend(
        ["", "Static signals do not replace running tests on each supported OS."]
    )
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    try:
        root = Path(args.root).expanduser().resolve(strict=True)
        input_paths: list[Path] = []
        report = scan_project(
            root,
            supported_override=args.platform,
            _input_paths=input_paths,
        )
        report_contents: list[str] = []
        if args.html:
            report_contents.append(render_html(report))
        if args.json_path:
            report_contents.append(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            )
        if args.sarif:
            report_contents.append(
                json.dumps(render_sarif(report), ensure_ascii=False, indent=2) + "\n"
            )
        outputs = _validate_report_targets(
            root,
            [args.html, args.json_path, args.sarif],
            input_paths,
        )
        for destination, content in zip(outputs, report_contents, strict=True):
            _write_report(destination, content)
        print(_text(report))
        if args.html:
            print(f"HTML report: {args.html}")
        return report_exit_code(report, args.fail_on)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"crossgrid: {exc}", file=sys.stderr)
        return 2


def main() -> None:
    raise SystemExit(run(build_parser().parse_args()))


if __name__ == "__main__":
    main()
