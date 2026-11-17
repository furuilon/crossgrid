# CrossGrid project specification

## Product definition

- **One sentence:** Show whether a Python project's declared OS support has visible GitHub Actions test-runner evidence and point maintainers to selected portability risks.
- **Target users:** Python maintainers who publish or claim Windows, Linux, or macOS support and want a compact local review before relying on CI coverage.
- **Problem:** Platform claims, workflow matrices, platform-specific skipped tests, and portability-sensitive code live in separate files, making missing coverage difficult to spot.
- **Use cases:** Check a new cross-OS support claim; see whether declared targets appear in test-like Actions jobs; locate simple platform skips and selected AST signals.
- **Existing approaches:** OS-specific API catalogs, linters, and CI itself. CrossGrid does not replace those tools or execute each OS. It combines a small set of evidence layers into one traceable report.
- **Differentiation:** A coverage map connects project metadata, explicit Actions runner labels, simple skip markers, and a narrowly selected AST ruleset, with locations and confidence labels.

## MVP and implementation

- **Core mechanism:** Parse Python source using `ast`, read TOML platform classifiers and YAML workflows, normalize explicit OS labels, and associate evidence without executing project code.
- **Stack:** Python 3.11+ and PyYAML 6.x.
- **Data:** In-memory findings contain rule ID, severity, confidence, relative path, line/column, subject, message, and evidence. JSON is the primary structured report.
- **Safety boundary:** It does not execute code, tests, workflow commands, plugins, or package installation, and it does not overwrite scanned inputs. Symlinked and out-of-root Python, metadata, and workflow paths are skipped before reads. Selected report outputs use atomic replacement and reject input aliases, symlink/junction/reparse-point paths and Git metadata. Input sizes/file counts are bounded. It is not a handle-based sandbox against concurrent file replacement.
- **MVP output:** Text plus optional JSON, SARIF 2.1.0, and self-contained searchable HTML.
- **Non-goals:** Proving portability; supporting every CI provider or GitHub expression; evaluating arbitrary skip expressions; reproducing a complete platform-specific API database; modifying source or workflows.

## Validation and completion criteria

- Test OS classifiers, explicit matrix runners, runner/name heuristics, skip conditions, and missing-coverage signals.
- Exercise AST findings for platform imports/APIs, signal constants, encoding, temporary files, and shell calls, including guarded/review cases.
- Test empty projects, malformed Python/TOML/YAML, report escaping, SARIF source locations, CLI output files, and fail thresholds.
- Run Windows, Ubuntu and macOS CI for Python 3.11–3.14 when repository hosting is available; configured matrix entries do not count as passing runs.
- Keep the project `PARTIAL` pending hosted CI and human acceptance. On Windows/Python 3.14.2, the wheel/sdist built successfully; the wheel installed in an isolated venv with PyYAML 6.0.3, `pip check`, CLI help/version and all 38 tests then present passing. After adding Windows junction/reparse output-path regressions, all 40 source-tree tests passed on Windows/Python 3.11.0; the two added tests have not been rerun on Python 3.14.2. Python 3.12–3.13, macOS/Linux, hosted CI, real repository accuracy review and report viewport acceptance remain unverified.

## Risks

Test-job detection and OS label normalization are heuristic. Nearby guards are not control-flow proof. Static rules can miss dynamic behavior and create false positives. Only explicit GitHub Actions runners are understood.

## Maintenance

Keep rules few, evidence-bearing, and individually tested. Add a rule only with concrete platform-specific examples and a documented confidence rationale; retain “unknown” when evidence is absent.
