# CrossGrid project specification

## Product definition

- **One sentence:** Show whether a Python project's declared OS support has visible GitHub Actions test-runner evidence and point maintainers to selected portability risks.
- **Target users:** Python maintainers who publish or claim Windows, Linux, or macOS support and want a compact local review before relying on CI coverage.
- **Problem:** Platform claims, workflow matrices, platform-specific skipped tests, and portability-sensitive code live in separate files, making missing coverage difficult to spot.
- **Use cases:** Check a new cross-OS support claim; see whether declared targets appear in test-like Actions jobs; locate simple platform skips and selected AST signals.
- **Existing approaches:** OS-specific API catalogs, linters, and CI itself. CrossGrid does not replace those tools or execute each OS. It combines a small set of evidence layers into one traceable report.
- **Differentiation:** A coverage map connects project metadata, explicit Actions runner labels, simple skip markers, and a narrowly selected AST ruleset, with locations and confidence labels.

## MVP and implementation

- **Core mechanism:** Parse Python source using `ast`, read static platform classifiers from PEP 621/Poetry metadata or setuptools `setup.cfg` (including bounded interpolation, its legacy alias and in-root `file:` references), read YAML workflows, normalize explicit OS labels, and associate evidence without executing project code or build metadata providers.
- **Stack:** Python 3.11+ and PyYAML 6.x.
- **Data:** In-memory findings contain rule ID, severity, confidence, relative path, line/column, subject, message, and evidence. JSON is the primary structured report.
- **Safety boundary:** It does not execute code, tests, workflow commands, plugins, or package installation, and it does not overwrite scanned inputs. Symlinked and out-of-root Python, metadata, and workflow paths are skipped before reads. Report outputs reject input aliases, duplicate targets, symlink/junction/reparse-point paths and Git metadata. Existing files are refused unless `--overwrite` is explicit. New reports use no-clobber creation; overwrite uses a same-directory temporary file and atomic replacement of the requested destination path. If hard links are unavailable, exclusive-create copying can leave a partial new report if interrupted or if copying fails. Input sizes/file counts are bounded. It is not a handle-based sandbox against concurrent replacement of scanned inputs or output paths; the scanner assumes a stable project tree.
- **MVP output:** Text plus optional JSON, SARIF 2.1.0, and self-contained searchable HTML.
- **Non-goals:** Proving portability; supporting every CI provider or GitHub expression; evaluating arbitrary skip expressions; reproducing a complete platform-specific API database; modifying source or workflows.

## Test coverage and verification status

- The suite covers declared OS classifiers from `pyproject.toml` and `setup.cfg`, precedence and dynamic metadata limits, bounded interpolation work/output and classifier-file references, explicit workflow runners, simple matrix extraction, platform skip markers, bounded source rules, malformed inputs, report escaping, current SARIF tool version, CLI paths, and exit thresholds. Regression cases verify that oversized, unsupported, or unrecognized runner matrices remain **unknown** instead of becoming false missing-runner claims; classifier references cannot amplify interpolation work/output without limit, alias report outputs, or block on non-regular files; and existing or concurrently created output files are not silently replaced.
- The hosted workflow tests Python 3.11–3.14 on Windows, macOS and Ubuntu. See the [Actions page](https://github.com/furuilon/crossgrid/actions/workflows/tests.yml) for results associated with each commit. The standalone current sdist has not been independently inspected, and the project is not published to PyPI.
- Automated fixtures do not establish scanner accuracy on every real repository. Generated reports have not been visually inspected in a browser; actual repository accuracy and user acceptance remain outside these automated checks.

## Risks

Test-job detection and OS label normalization are heuristic. Nearby guards are not control-flow proof. Static rules can miss dynamic behavior and create false positives. Only explicit GitHub Actions runners are understood. Matrices above the documented analysis bound, with unsupported values, or with unrecognized OS labels are reported as unknown rather than treated as full coverage or confirmed gaps.

## Maintenance

Keep rules few, evidence-bearing, and individually tested. Add a rule only with concrete platform-specific examples and a documented confidence rationale; retain “unknown” when evidence is absent.
