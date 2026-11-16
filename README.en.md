# CrossGrid

CrossGrid connects four kinds of evidence in a Python repository: operating systems declared in `pyproject.toml`, explicit GitHub Actions test runners, simple platform-related test skips, and a small set of source-level portability signals. Its report answers a narrow question: **does the repository's stated OS scope have visible test-runner evidence, and where should a maintainer review assumptions?**

CrossGrid is static analysis. It does not execute your project on Windows, Linux or macOS, and a clean report is not proof of portability.

简体中文主页：[README.md](README.md) · [验收指南](USER_ACCEPTANCE.md) · [架构](ARCHITECTURE.md) · [项目规格](PROJECT_SPEC.md) · [技术机制](TECHNICAL_INNOVATION.md)

## Install

Requirements: Python 3.11+ and PyYAML 6.x.

```powershell
python -m pip install .
crossgrid --version
```

## Quick start

From a Python project root:

```powershell
crossgrid . --html crossgrid-report.html --json crossgrid-report.json --sarif crossgrid.sarif
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

[CrossPortability](https://github.com/neelpatel-png/crossportability)'s public README describes AST checks for Python OS-specific paths, APIs/commands, imports and environment variables. [PSASpotter](https://github.com/ricardojob/PSASpotter) and its [research paper](https://arxiv.org/abs/2607.17462) analyze platform-specific APIs and defensive guards. CrossGrid overlaps with both prior works in source-level platform signals; it does not claim to introduce platform-specific API analysis. CrossGrid combines declared OS support, explicit GitHub Actions runners, simple test skip markers and selected source signals in one report. This describes its workflow, not proven product uniqueness or validated user demand.

### Rule sources and curation

The platform-specific module, API and constant lists are maintainer-curated from Python’s public standard-library documentation. This is a provenance statement, not a guarantee produced by the similarity scan. The checked source tree contains no imported external rule database or vendored linter implementation. A project-level Git history begins with an initial snapshot commit on 2026-11-16; it does not establish authorship history before that date, and no internet-wide code scan was run. Sources include [`os`](https://docs.python.org/3.11/library/os.html), [`signal`](https://docs.python.org/3.11/library/signal.html), [`platform`](https://docs.python.org/3.11/library/platform.html), and the platform-specific [`pwd`](https://docs.python.org/3.11/library/pwd.html), [`grp`](https://docs.python.org/3.11/library/grp.html), [`fcntl`](https://docs.python.org/3.11/library/fcntl.html), [`termios`](https://docs.python.org/3.11/library/termios.html), [`resource`](https://docs.python.org/3.11/library/resource.html), [`pty`](https://docs.python.org/3.11/library/pty.html), and Windows-only [`winreg`](https://docs.python.org/3.11/library/winreg.html) module references. These links establish documented API/platform facts; the selected subset and matching logic are this project’s rule design and remain intentionally incomplete.

## Inputs and limits

- Python source files; hidden and common generated/dependency directories are skipped.
- Symlinked source, metadata and workflow paths are not followed; resolved inputs must stay inside the selected project root.
- `pyproject.toml` OS classifiers, including common Poetry metadata.
- `.github/workflows/*.yml` and `.yaml` with explicit `runs-on`, or scalar `strategy.matrix.os`/`include[].os` values referenced directly by `runs-on`.
- Simple pytest/unittest platform skip markers that contain recognizable OS names.
- Bounded inputs: each Python file up to 8 MiB, 128 MiB total Python source and 20,000 Python files; each workflow up to 2 MiB, 32 MiB total workflow data and 1,000 workflow files; `pyproject.toml` up to 1 MiB. Oversized inputs produce CG90x findings instead of unbounded reads.
- Selected reports are written by atomic replacement. Output targets must be distinct and cannot point to scan inputs, symbolic-link paths or Git administrative metadata. Existing report files may be replaced safely.

Derived YAML expressions, matrices exceeding 256 combinations or using non-scalar dimensions, reusable workflow inheritance, GitLab/CircleCI, custom marker evaluation, third-party dependency wheel support and actual OS execution are not analyzed. Matrix values count only when `runs-on` is the direct `${{ matrix.os }}` or `${{ matrix['os'] }}` expression; scalar exclusions and includes are applied. Test-job detection uses whole-word job/step labels and recognizes direct invocations of a short list of Python test tools (including `python -m`, `py -3.x -m`, `env VAR=value`, simple `coverage run -m`, and `uv`/`poetry`/`pipenv` wrappers); tox/nox count only when an explicitly selected environment or session name contains a test/unit marker. Opaque default tox/nox runs, list/config inspection commands (including `tox config -e ...`), lint-only selections, tox `--notest`/package-only and devenv modes, help/version modes, and pytest collection-only mode do not count, nor do install/echo/mention commands. This command-text heuristic is evidence of likely test execution, not proof tests passed. Generic POSIX/Unix package classifiers are not treated as Linux or macOS declarations. `CG201`, `CG202` and `CG301` are review signals, not automatic defect claims; CG201 recognizes built-in, `io.open`, and common pathlib open calls. Path-variable inference is disabled when same-named bindings are shadowed, reassigned, or captured by structural patterns, and arbitrary `.open()` methods are not analyzed.

Exit codes: default `--fail-on error` returns `0` for this tool's warning/review signals and `2` for an input or output error. Choose `--fail-on warning` or `--fail-on review` if your team wants a stricter CI gate.

## Development

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

## License

MIT. See [LICENSE](LICENSE).

CrossGrid depends on PyYAML (`>=6.0,<7`), which is separately installed and is released under the MIT license. See the [upstream license notice](https://github.com/yaml/pyyaml/blob/main/README.md#license). CrossGrid does not vendor PyYAML source.
