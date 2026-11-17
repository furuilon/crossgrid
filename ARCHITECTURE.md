# CrossGrid architecture

## Evidence flow

```text
pyproject classifiers ──────────┐
GitHub Actions explicit runners ├─ normalize OS labels ─ support coverage rows
Python AST risk signals ─────────┤
pytest skip markers ─────────────┘
                                          │
                               text / JSON / SARIF / HTML
```

The scanner walks Python source outside common hidden, generated and dependency directories. It builds AST nodes and source locations, then reports a limited set of platform-specific imports/APIs, implicit text encoding, temporary-file review signals and shell invocation. Python and workflow reads have per-file and aggregate byte limits plus file-count limits; TOML and Git marker files are also bounded.

The workflow reader only counts an OS as a test runner when the job name or its step text appears test-related and an explicit runner label can be normalized. It recognizes `windows`, `ubuntu/linux`, and `macos` label families. The project scope comes from common OS classifiers or `--platform`; if neither exists, the report says support is undeclared and does not imply all OSes are supported.

## Confidence model

- `high`: input location and syntax are explicit, such as a parsed import or missing workflow runner.
- `medium`: a pattern points to a possible issue, such as text `open()` with implicit encoding or shell execution.
- `low`: broad review prompt, such as any `NamedTemporaryFile` use.

Confidence describes the static signal, not whether the code is broken. A nearby guard changes the platform API/import finding from `high` to `review`; the scanner does not prove the guard's branch is correct.

## Deliberate limits

- The scanner does not execute tests or install dependencies.
- GitHub expressions, includes, reusable workflow inheritance and external CI systems may be missed.
- Skip markers are recognized from source text only; their Python expressions are not evaluated.
- Dynamic imports and data-driven paths can evade AST rules.
- The ruleset is a small subset of cross-OS risk categories, not an implementation of the full research taxonomy or PSASpotter's API inventory.

## Modules and interfaces

- `src/crossgrid/scanner.py` owns discovery, parsing, normalization, rule evaluation and report rendering. `scan_project(root, supported_override=None) -> dict` is the public scan operation; `report_exit_code(report, fail_on) -> int` applies the configured gate.
- `src/crossgrid/cli.py` defines `build_parser()`, `run(namespace) -> int`, and `main()`. Report outputs are checked together before writing: input aliases, duplicate targets, symlinked or Windows junction/reparse-point paths, and Git administrative metadata are rejected. Each output uses a same-directory temporary file followed by atomic replacement; expected filesystem/scan errors return exit code 2.
- `src/crossgrid/__init__.py` owns the package version. `tests/test_scanner.py` covers AST, TOML/YAML fixtures, path-boundary behavior, reports, CLI output, and failure thresholds.

## Filesystem and error boundary

The selected root is resolved once. Python sources, `pyproject.toml`, workflow directories, and workflow files are checked to remain inside the root and are not followed through symlinks. Unsafe source/workflow paths become CG904/CG903 findings; unsafe metadata becomes CG902. Parse/read failures are reported by input type instead of aborting the entire scan when safe to continue. The scanner does not execute scanned code or workflow commands.

Containment is checked before each read and write, but this is not a handle-based sandbox: a process that can replace files concurrently could race the check (TOCTOU). CrossGrid assumes a stable local tree during scanning.

Tests use synthetic local fixtures. They do not prove that every GitHub Actions expression is interpreted, that static risk signals are true defects, or that a real matrix passes on every operating system. The configured CI matrix is not evidence of a passing hosted run; check the repository's Actions results when the project is hosted.
