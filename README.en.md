# CrossGrid

[![GitHub Actions Tests](https://github.com/furuilon/crossgrid/actions/workflows/tests.yml/badge.svg?branch=main&event=push)](https://github.com/furuilon/crossgrid/actions/workflows/tests.yml)

CrossGrid connects four kinds of evidence in a Python repository: operating systems declared in `pyproject.toml` or `setup.cfg`, explicit GitHub Actions test runners, simple platform-related test skips, and a small set of source-level portability signals. Its report answers a narrow question: **does the repository's stated OS scope have visible test-runner evidence, and where should a maintainer review assumptions?**

CrossGrid is static analysis. It does not execute your project on Windows, Linux or macOS, and a clean report is not proof of portability.

The hosted workflow runs Python 3.11–3.14 tests on Windows, macOS and Ubuntu. Check the [Actions page](https://github.com/furuilon/crossgrid/actions/workflows/tests.yml) for results associated with a specific commit. The current sdist has not been independently inspected, and the project is not published to PyPI. Automated tests do not establish scanner accuracy on every real repository; generated reports have not been reviewed in a browser viewport.

The current package version is a **0.2.0 development snapshot** and is not published to PyPI. SARIF reports read the tool version from the package version.

简体中文主页：[README.md](README.md) · [架构](ARCHITECTURE.md) · [项目规格](PROJECT_SPEC.md) · [技术机制](TECHNICAL_INNOVATION.md) · [Changelog](CHANGELOG.md)

## Install

Requirements: Python 3.11+ and PyYAML 6.x.

```powershell
python -m pip install .
crossgrid --version
```

## Quick start

From a Python project root. This PowerShell example writes reports to the system temporary directory so generated files stay out of the scanned project:

```powershell
$reportDir = Join-Path $env:TEMP ('crossgrid-report-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $reportDir | Out-Null
crossgrid . `
  --html (Join-Path $reportDir 'report.html') `
  --json (Join-Path $reportDir 'report.json') `
  --sarif (Join-Path $reportDir 'report.sarif')
```

CrossGrid prefers static `[project].classifiers`. If the field is absent and no dynamic classifiers are declared, it checks `[tool.poetry].classifiers`, then falls back to setuptools `[metadata] classifiers` or the legacy `classifier` alias in `setup.cfg`. It supports multiline values, comma lists, ConfigParser interpolation and bounded `file:` references. Dynamic classifiers and build scripts are never executed. If none of these sources provides a usable declaration, state the scope explicitly:

```powershell
crossgrid . --platform windows --platform linux
```

`setup.cfg` and each classifier file reference are limited to 1 MiB; referenced files have a combined 1 MiB limit and a maximum of 16 files. Interpolation output is capped at 1 MiB, 10 nested levels and 4,096 references, with at most 4 Mi characters processed across recursive expansions. References must be regular, non-symlink files inside the selected root. Unsafe, missing, non-regular or oversized references produce `CG902`. A missing in-root reference remains protected as an output path, so a report cannot become classifier input on a later scan. The report shows declared targets and test-like `runs-on` evidence separately. It also lists simple `pytest.mark.skipif` platform conditions and source locations.

Existing report paths are refused by default. Add `--overwrite` only after checking the destination; it never bypasses scan-input, duplicate-path or Git-metadata guards.

## Current rules

| Rule | Signal |
|---|---|
| CG101 | Import of a small list of OS-specific modules such as `pwd`, `fcntl`, `resource`, `termios` or `winreg` |
| CG102 | Call to a small list of OS-specific APIs such as `os.getuid()` or `os.uname()`, or use of selected POSIX signals |
| CG201 | Recognized built-in, `io.open`, or `pathlib.Path.open` text mode without an explicit non-`None` encoding |
| CG202 | `NamedTemporaryFile` usage to review for Windows reopen/locking assumptions; intentionally low confidence |
| CG301 | `os.system()` or subprocess `shell=True` to review for shell differences |
| CG801 | A simple platform-related `skipif`/`skipUnless` marker |
| CG901 | Declared target OS has no recognized explicit test-like Actions runner |
| CG902–905 | Workflow, metadata or source file could not be analyzed; dynamic classifiers are reported as unresolved and build scripts are not executed |
| CG906 | Runner matrix exceeds the analysis scope or contains OS labels that cannot be mapped; affected coverage is unknown rather than a confirmed missing runner |

The rules are conservative and incomplete. Every finding includes confidence, source location and the evidence used.

## Related work

[CrossPortability](https://github.com/neelpatel-png/crossportability)'s public README describes AST checks for Python OS-specific paths, APIs/commands, imports and environment variables. [PSASpotter](https://github.com/ricardojob/PSASpotter) and its [research paper](https://arxiv.org/abs/2607.17462) analyze platform-specific APIs and defensive guards. The [PyCrossCheck PyPI page](https://pypi.org/project/pycrosscheck/) also advertises Windows/Linux/macOS compatibility checks, 56+ rules, CLI/JSON output and a Bandit plugin; this is direct overlap in static source analysis. Its listed GitHub source URL is a placeholder and currently returns 404, so this records its public claims without validating implementation or rule coverage. CrossGrid focuses on reporting the consistency between declared OS support and explicit GitHub Actions test runners, with simple skip markers and limited source signals; it does not claim to be the only or most complete compatibility checker.

### Rule sources and curation

The Python official standard-library pages below document platform restrictions for the listed APIs; the scanner uses a limited subset and does not bundle an external rule database or vendored linter implementation. The selected rules and matching logic do not provide complete compatibility analysis. Sources include [`os`](https://docs.python.org/3.11/library/os.html), [`signal`](https://docs.python.org/3.11/library/signal.html), [`platform`](https://docs.python.org/3.11/library/platform.html), and the platform-specific [`pwd`](https://docs.python.org/3.11/library/pwd.html), [`grp`](https://docs.python.org/3.11/library/grp.html), [`fcntl`](https://docs.python.org/3.11/library/fcntl.html), [`termios`](https://docs.python.org/3.11/library/termios.html), [`resource`](https://docs.python.org/3.11/library/resource.html), [`pty`](https://docs.python.org/3.11/library/pty.html), and Windows-only [`winreg`](https://docs.python.org/3.11/library/winreg.html) module references.

Static `setup.cfg` reading follows setuptools' [declarative configuration documentation](https://setuptools.pypa.io/en/stable/userguide/declarative_config.html), which documents multiline and comma-list `[metadata] classifiers`, the `classifier` compatibility alias, interpolation and `file:` references. CrossGrid reads those files but never executes build metadata code.

## Inputs and limits

- Python source files; hidden and common generated/dependency directories are skipped.
- Symlinked source, metadata and workflow paths are not followed; resolved inputs must stay inside the selected project root.
- CrossGrid is not a security sandbox and assumes that another process does not replace project files during a scan. A race can occur between path checks and file reads or writes.
- Static OS classifiers in `pyproject.toml` (PEP 621 and common Poetry metadata); when absent, setuptools `[metadata] classifiers` or the legacy `classifier` alias from `setup.cfg` are used, including comma lists, interpolation and bounded `file:` references. Dynamic classifiers are not evaluated.
- `.github/workflows/*.yml` and `.yaml` with explicit `runs-on`, or scalar `strategy.matrix.os`/`include[].os` values referenced directly by `runs-on`.
- Simple pytest/unittest platform skip markers that contain recognizable OS names.
- Bounded inputs: each Python file up to 8 MiB, 128 MiB total Python source and 20,000 Python files; each workflow up to 2 MiB, 32 MiB total workflow data and 1,000 workflow files; `pyproject.toml`, `setup.cfg` and each `file:` classifier source up to 1 MiB, interpolation output up to 1 MiB/10 levels/4,096 references, at most 4 Mi characters processed recursively, and a 1 MiB/16-file aggregate classifier-reference limit. Oversized inputs produce CG90x findings instead of unbounded reads.
- Output targets must be distinct and cannot overwrite scan inputs, symbolic links, Windows junction/reparse-point paths, or Git administrative metadata. Existing files are refused unless `--overwrite` is supplied after review. New files are created without clobbering; replacement uses a same-directory temporary file and atomic replace. If the filesystem does not support hard links, exclusive creation plus copying is used; an interrupted or failed copy may leave a partial new report but will not replace an existing target.

### Workflow and runner scope

- Derived or complex YAML expressions, reusable workflow inheritance, GitLab/CircleCI, custom marker evaluation, third-party dependency wheel support, and actual execution on other operating systems are outside the current scope.
- Matrix runners are counted only when `runs-on` directly uses `${{ matrix.os }}` or `${{ matrix['os'] }}`; scalar `exclude` and `include` are applied. Matrices with more than 256 combinations, non-scalar dimensions, or OS labels that cannot be mapped produce `CG906` and unknown coverage for affected OS targets, not a `CG901` missing-runner conclusion.
- Test jobs are inferred from whole-word job/step labels or recognized direct invocations such as `python -m`, `py -3.x -m`, `env VAR=value`, simple `coverage run -m`, and `uv`/`poetry`/`pipenv run`. `tox`/`nox` count only when an explicitly selected environment or session name contains a `test`/`unit` marker.
- Opaque default `tox`/`nox` runs, list/config inspection (including `tox config -e ...`), lint-only selections, `tox --notest`/package-only, devenv, help/version, pytest collection-only, install, echo, and mere mentions of test tools do not count. Command-text heuristics suggest that tests may run; they do not prove that tests ran or passed.
- Generic POSIX/Unix classifiers are not treated as Linux or macOS support declarations.

### Source-rule limits

- `CG201` recognizes built-in `open`, `io.open`, and common `pathlib.Path.open` calls. Path inference is disabled for shadowed, reassigned, or pattern-captured bindings; arbitrary `.open()` methods are not analyzed.
- `CG201`, `CG202`, and `CG301` are review prompts, not definitive defect claims. Rules can miss cases or produce false positives.

Exit codes: default `--fail-on error` returns `0` for this tool's warning/review signals and `2` for an input or output error. Choose `--fail-on warning` or `--fail-on review` if your team wants a stricter CI gate.

## Development

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

## License

MIT. See [LICENSE](LICENSE).

CrossGrid depends on PyYAML (`>=6.0,<7`), which is separately installed. The PyYAML 6.0.3 version used in verification is released under the MIT license; see its [upstream license file](https://github.com/yaml/pyyaml/blob/6.0.3/LICENSE). CrossGrid does not vendor PyYAML source.
