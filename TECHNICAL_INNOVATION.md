# CrossGrid technical mechanism

## Technical problem

Python OS support is expressed across metadata, CI YAML, test skip conditions, and source code. None of those sources alone proves the full support claim, but gathering them manually is tedious and easy to misread.

## Usual approach

Maintainers inspect classifiers and workflow jobs separately, then search for platform checks or known OS-specific APIs. General linters may flag a code construct without showing whether the claimed platforms are exercised by CI.

## CrossGrid method

1. Parse `pyproject.toml` classifiers and normalize known Windows/Linux/macOS claims.
2. Parse explicit `.github/workflows/*.yml` runner labels and common `strategy.matrix.os` forms.
3. Treat a job as test-like only when job/step names or commands match a small set of test-runner tokens; label this evidence as heuristic.
4. Parse Python files with `ast`, preserving path and source location for selected portability signals.
5. Recognize simple platform-related test skip markers from source text and report their locations rather than evaluating the expressions.
6. Join the three evidence layers into one per-platform coverage table; preserve missing/unknown states and confidence labels.
7. Emit text, JSON, SARIF, and escaped HTML without executing the scanned project.

## Hard parts and code locations

- TOML classifiers and OS normalization: `src/crossgrid/scanner.py` (`_read_project_platforms`, `_platforms_from_classifiers`).
- Workflow and matrix parsing: `_scan_workflows`, `_runner_values`, `_platforms_from_runner`.
- AST rule locations and guard review classification: `_scan_python` and AST helpers.
- Coverage join and summary construction: `scan_project`.
- Stable reports and source escaping: `render_sarif`, `render_html`.

The advance is primarily integration and traceability: each signal is intentionally bounded and displayed beside the evidence that produced it. This is not a new proof of cross-platform correctness.

## Correctness evidence

`tests/test_scanner.py` uses synthetic Python projects with TOML/YAML/source fixtures. It tests matrix extraction, missing runner evidence, platform and import guards, encoding and shell signals, malformed input handling, report escaping, SARIF line locations, CLI output files, and warning exit thresholds.

## Performance

Scanning is one directory walk plus per-file AST parsing; workflow and metadata parsing scale with their input sizes. A timing against the project's own small source tree was recorded in the Hidden Gems portfolio benchmark notes; this is not a large-monorepo benchmark, and the portfolio-only record is not included in this standalone project.

## Limitations and next technical work

- Test-job and OS matching are heuristics. Dynamic matrix expressions, reusable workflows and external CI systems can be missed.
- Guard context is not full control-flow proof; the rule set can have false positives and false negatives.
- Symlinked or out-of-root sources, metadata, and workflow paths are skipped to keep reads inside the selected root.
- Containment is checked before opening a path, not through a pinned file handle; a malicious process concurrently replacing files could create a TOCTOU race. The tool is a local review aid, not a security sandbox.
- Next validation should add a manually labeled real-repository corpus, report per-rule precision/false-positive examples, and benchmark large trees and memory. Rule expansion should wait for that evidence.
