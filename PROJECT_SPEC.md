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
- **Safety boundary:** Reads project files only. It does not execute code, tests, workflow commands, plugins, or package installation; it does not modify scanned files. Symlinked and out-of-root Python, metadata, and workflow paths are skipped before reads. It is not a handle-based sandbox against concurrent file replacement.
- **MVP output:** Text plus optional JSON, SARIF 2.1.0, and self-contained searchable HTML.
- **Non-goals:** Proving portability; supporting every CI provider or GitHub expression; evaluating arbitrary skip expressions; reproducing a complete platform-specific API database; modifying source or workflows.

## Validation and completion criteria

- Test OS classifiers, explicit matrix runners, runner/name heuristics, skip conditions, and missing-coverage signals.
- Exercise AST findings for platform imports/APIs, signal constants, encoding, temporary files, and shell calls, including guarded/review cases.
- Test empty projects, malformed Python/TOML/YAML, report escaping, SARIF source locations, CLI output files, and fail thresholds.
- Run Windows and Ubuntu CI for Python 3.11–3.13 when available.
- Keep the project `PARTIAL` until core tests, local installation/entry-point checks, and required docs pass. It is now `READY FOR USER ACCEPTANCE`; the user's comparison against a repository with a known CI matrix is a separate acceptance step.

## Risks

Test-job detection and OS label normalization are heuristic. Nearby guards are not control-flow proof. Static rules can miss dynamic behavior and create false positives. Only explicit GitHub Actions runners are understood.

## Maintenance

Keep rules few, evidence-bearing, and individually tested. Add a rule only with concrete platform-specific examples and a documented confidence rationale; retain “unknown” when evidence is absent.
