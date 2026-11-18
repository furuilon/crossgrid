# CrossGrid

[![GitHub Actions Tests](https://github.com/furuilon/crossgrid/actions/workflows/tests.yml/badge.svg?branch=main&event=push)](https://github.com/furuilon/crossgrid/actions/workflows/tests.yml)

CrossGrid connects four kinds of evidence in a Python repository: operating systems declared in `pyproject.toml`, explicit GitHub Actions test runners, simple platform-related test skips, and a small set of source-level portability signals. Its report answers a narrow question: **does the repository's stated OS scope have visible test-runner evidence, and where should a maintainer review assumptions?**

CrossGrid is static analysis. It does not execute your project on Windows, Linux or macOS, and a clean report is not proof of portability.

Local verification used Windows: all 40 tests in the current source tree passed on Python 3.11.0. GitHub Actions runs [#2](https://github.com/furuilon/crossgrid/actions/runs/44207813048) and [#3](https://github.com/furuilon/crossgrid/actions/runs/44211496002) passed all 12 Ubuntu/macOS/Windows × Python 3.11–3.14 combinations on commits `ece8383cf04e734c93051dd247830f3fdb81c2d8` and `0b24739f790a99c5efa722a3260524628044854f`, respectively. Each job completed package installation, CLI smoke checks, `pip check`, and all 40 automated tests, including regression tests that simulate Windows junction/reparse-point metadata. See the [Actions page](https://github.com/furuilon/crossgrid/actions/workflows/tests.yml) for later commits. Earlier run [#1](https://github.com/furuilon/crossgrid/actions/runs/44198425891) also passed on commit `33717f6b328325e343469723242a033787c7b483`. A standalone sdist build and inspection remain unverified, and the project is not published to PyPI. Passing the automated test suite does not establish scanner accuracy on every real repository, and the report has not been visually inspected in a browser.

简体中文主页：[README.md](README.md) · [架构](ARCHITECTURE.md) · [项目规格](PROJECT_SPEC.md) · [技术机制](TECHNICAL_INNOVATION.md)

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

If support classifiers are missing, state the scope explicitly:

```powershell
crossgrid . --platform windows --platform linux
```

The report shows declared targets and test-like `runs-on` evidence separately. It also lists simple `pytest.mark.skipif` platform conditions and source locations.

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
| CG902–905 | Workflow, metadata or source file could not be analyzed |

The rules are conservative and incomplete. Every finding includes confidence, source location and the evidence used.

## Related work

[CrossPortability](https://github.com/neelpatel-png/crossportability)'s public README describes AST checks for Python OS-specific paths, APIs/commands, imports and environment variables. [PSASpotter](https://github.com/ricardojob/PSASpotter) and its [research paper](https://arxiv.org/abs/2607.17462) analyze platform-specific APIs and defensive guards. CrossGrid combines declared OS support, explicit GitHub Actions runners, simple test skip markers and selected source signals in one report.

### Rule sources and curation

The Python official standard-library pages below document platform restrictions for the listed APIs; the scanner uses a limited subset and does not bundle an external rule database or vendored linter implementation. The selected rules and matching logic do not provide complete compatibility analysis. Sources include [`os`](https://docs.python.org/3.11/library/os.html), [`signal`](https://docs.python.org/3.11/library/signal.html), [`platform`](https://docs.python.org/3.11/library/platform.html), and the platform-specific [`pwd`](https://docs.python.org/3.11/library/pwd.html), [`grp`](https://docs.python.org/3.11/library/grp.html), [`fcntl`](https://docs.python.org/3.11/library/fcntl.html), [`termios`](https://docs.python.org/3.11/library/termios.html), [`resource`](https://docs.python.org/3.11/library/resource.html), [`pty`](https://docs.python.org/3.11/library/pty.html), and Windows-only [`winreg`](https://docs.python.org/3.11/library/winreg.html) module references.

## Inputs and limits

- Python source files; hidden and common generated/dependency directories are skipped.
- Symlinked source, metadata and workflow paths are not followed; resolved inputs must stay inside the selected project root.
- `pyproject.toml` OS classifiers, including common Poetry metadata.
- `.github/workflows/*.yml` and `.yaml` with explicit `runs-on`, or scalar `strategy.matrix.os`/`include[].os` values referenced directly by `runs-on`.
- Simple pytest/unittest platform skip markers that contain recognizable OS names.
- Bounded inputs: each Python file up to 8 MiB, 128 MiB total Python source and 20,000 Python files; each workflow up to 2 MiB, 32 MiB total workflow data and 1,000 workflow files; `pyproject.toml` up to 1 MiB. Oversized inputs produce CG90x findings instead of unbounded reads.
- Selected reports are written by atomic replacement. Output targets must be distinct and cannot point to scan inputs, symbolic links, Windows junction/reparse-point paths, or Git administrative metadata. Existing report files may be replaced safely.

### Workflow and runner scope

- Derived or complex YAML expressions, reusable workflow inheritance, GitLab/CircleCI, custom marker evaluation, third-party dependency wheel support, and actual execution on other operating systems are outside the current scope.
- Matrix runners are counted only when `runs-on` directly uses `${{ matrix.os }}` or `${{ matrix['os'] }}`; scalar `exclude` and `include` are applied. Matrices with more than 256 combinations or non-scalar dimensions are not analyzed.
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
