from __future__ import annotations

import ast
import heapq
import io
import itertools
import os
import re
import shlex
import shutil
import stat
import tempfile
import tokenize
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml


SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    "build",
    "dist",
    ".tox",
    ".nox",
}
PLATFORM_MODULES = {"pwd", "grp", "fcntl", "termios", "resource", "pty", "winreg"}
PLATFORM_APIS = {
    "os.uname",
    "os.getuid",
    "os.geteuid",
    "os.getgid",
    "os.getegid",
    "os.getgroups",
    "os.getpgid",
    "os.setsid",
    "os.fork",
    "os.forkpty",
    "os.killpg",
    "os.pathconf",
}
PLATFORM_CONSTANTS = {"signal.SIGKILL", "signal.SIGHUP", "signal.SIGTSTP"}
OS_GUARD_MARKERS = (
    "sys.platform",
    "os.name",
    "platform.system",
    "platform.machine",
    "win32",
    "windows",
    "linux",
    "darwin",
    "posix",
)
SKIP_MARKERS = (
    "sys.platform",
    "os.name",
    "win32",
    "windows",
    "linux",
    "darwin",
    "posix",
)
MAX_METADATA_BYTES = 1024 * 1024
MAX_WORKFLOW_BYTES = 2 * 1024 * 1024
MAX_WORKFLOW_FILES = 1_000
MAX_TOTAL_WORKFLOW_BYTES = 32 * 1024 * 1024
MAX_GIT_MARKER_BYTES = 4 * 1024
MAX_PYTHON_SOURCE_BYTES = 8 * 1024 * 1024
MAX_PYTHON_FILES = 20_000
MAX_TOTAL_SOURCE_BYTES = 128 * 1024 * 1024


class _InputTooLarge(Exception):
    """Raised when a source file exceeds its bounded scan size."""


def _read_limited_bytes(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        contents = stream.read(limit + 1)
    if len(contents) > limit:
        raise _InputTooLarge
    return contents


def _read_git_marker(path: Path) -> str:
    with path.open("rb") as stream:
        contents = stream.read(MAX_GIT_MARKER_BYTES + 1)
    if len(contents) > MAX_GIT_MARKER_BYTES:
        raise ValueError("Git metadata marker exceeds the safe size limit.")
    return contents.decode("utf-8").strip()


def _is_link_or_reparse_point(path: Path) -> bool:
    """Detect symlinks and Windows reparse points in paths used for reports."""
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    if os.name == "nt":
        try:
            attributes = path.lstat().st_file_attributes
        except (FileNotFoundError, NotADirectoryError):
            return False
        reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        return bool(attributes & reparse_attribute)
    return False


@dataclass(frozen=True, slots=True)
class Finding:
    rule_id: str
    severity: str
    confidence: str
    file: str
    line: int
    column: int
    subject: str
    message: str
    evidence: str


def _safe_project_path(root: Path, path: Path) -> bool:
    """Allow reads only through non-symlink paths that resolve inside the scan root."""
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    if not resolved.is_relative_to(root):
        return False
    current = path
    while current != root:
        if current.is_symlink():
            return False
        current = current.parent
        if not current.is_relative_to(root) and current != root:
            return False
    return True


def _git_metadata_roots(root: Path) -> set[Path]:
    """Find Git administrative directories for the selected worktree."""
    metadata_roots: set[Path] = set()
    for ancestor in (root, *root.parents):
        marker = ancestor / ".git"
        try:
            if marker.is_dir():
                metadata_roots.add(marker.resolve(strict=True))
                break
            if marker.is_file():
                contents = _read_git_marker(marker)
                match = re.fullmatch(r"gitdir:\s*(.+)", contents, re.IGNORECASE)
                if match is None:
                    raise ValueError("Could not safely parse the project's .git file.")
                git_dir = Path(match.group(1))
                if not git_dir.is_absolute():
                    git_dir = ancestor / git_dir
                git_dir = git_dir.resolve(strict=True)
                metadata_roots.add(git_dir)
                common_dir_file = git_dir / "commondir"
                if common_dir_file.is_file():
                    common_dir = Path(_read_git_marker(common_dir_file))
                    if not common_dir.is_absolute():
                        common_dir = git_dir / common_dir
                    metadata_roots.add(common_dir.resolve(strict=True))
                break
        except (OSError, RuntimeError, UnicodeError) as exc:
            raise ValueError(
                "Could not safely resolve the project's Git metadata."
            ) from exc

    if all((root / name).exists() for name in ("HEAD", "config", "objects", "refs")):
        try:
            metadata_roots.add(root.resolve(strict=True))
        except (OSError, RuntimeError) as exc:
            raise ValueError(
                "Could not safely resolve the project's Git metadata."
            ) from exc
    return metadata_roots


def _same_path(first: Path, second: Path) -> bool:
    try:
        if first.resolve(strict=False) == second.resolve(strict=False):
            return True
        if first.exists() and second.exists():
            return os.path.samefile(first, second)
    except (OSError, RuntimeError) as exc:
        raise ValueError("A report output path could not be checked safely.") from exc
    return False


def _validate_report_targets(
    root: Path,
    outputs: list[str | None],
    input_paths: list[Path],
    *,
    overwrite: bool = False,
) -> list[Path]:
    destinations = [Path(output).expanduser() for output in outputs if output]
    metadata_roots = _git_metadata_roots(root)
    for index, destination in enumerate(destinations):
        destination = Path(os.path.abspath(destination))
        destinations[index] = destination
        try:
            resolved = destination.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise ValueError(
                "A report output path could not be resolved safely."
            ) from exc
        for ancestor in (destination, *destination.parents):
            if _is_link_or_reparse_point(ancestor):
                raise ValueError(
                    f"Report output cannot traverse a symlink or reparse point: {ancestor}"
                )
        if destination.exists() and destination.is_dir():
            raise ValueError(f"Report output path is a directory: {destination}")
        if any(
            resolved == git_dir or resolved.is_relative_to(git_dir)
            for git_dir in metadata_roots
        ):
            raise ValueError("Report output cannot be inside Git metadata.")
        if any(part.casefold() == ".git" for part in resolved.parts):
            raise ValueError("Report output cannot be inside Git metadata.")
        try:
            relative = resolved.relative_to(root)
        except ValueError:
            relative = None
        if relative is not None and (
            relative.suffix == ".py"
            or relative.name.casefold() == "pyproject.toml"
            or (
                len(relative.parts) >= 3
                and tuple(part.casefold() for part in relative.parts[:2])
                == (".github", "workflows")
                and relative.suffix.lower() in {".yml", ".yaml"}
            )
        ):
            raise ValueError(
                f"Report output cannot overwrite a scan input: {destination}"
            )
        if any(_same_path(resolved, source) for source in input_paths):
            raise ValueError(
                f"Report output cannot overwrite a scan input: {destination}"
            )
        try:
            os.lstat(destination)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise ValueError(
                "A report output path could not be checked safely."
            ) from exc
        else:
            if not overwrite:
                raise ValueError(
                    "Report output already exists; pass --overwrite to replace it."
                )

    for index, destination in enumerate(destinations):
        if any(_same_path(destination, other) for other in destinations[index + 1 :]):
            raise ValueError("Report output paths must be distinct.")
    return destinations


def _write_report(path: Path, content: str, *, overwrite: bool = False) -> None:
    temporary_name: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
        if overwrite:
            os.replace(temporary_name, path)
        else:
            try:
                os.link(temporary_name, path)
            except FileExistsError as exc:
                raise ValueError(
                    "Report output already exists; pass --overwrite to replace it."
                ) from exc
            except OSError:
                # Fall back on an exclusive create for filesystems that do not
                # support hard links. This prevents clobbering, though an
                # interruption during the copy can leave a partial new file.
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                flags |= getattr(os, "O_BINARY", 0)
                try:
                    output_fd = os.open(path, flags, 0o600)
                except FileExistsError as exc:
                    raise ValueError(
                        "Report output already exists; pass --overwrite to replace it."
                    ) from exc
                with (
                    os.fdopen(output_fd, "wb") as output_stream,
                    open(temporary_name, "rb") as source_stream,
                ):
                    shutil.copyfileobj(source_stream, output_stream)
                    output_stream.flush()
                    os.fsync(output_stream.fileno())
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError(f"Could not write report output file: {path}") from exc
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass


def _platforms_from_runner(value: Any) -> set[str]:
    text = str(value).lower()
    result: set[str] = set()
    if any(word in text for word in ("windows", "win32", "win-latest")):
        result.add("windows")
    if any(word in text for word in ("ubuntu", "linux")):
        result.add("linux")
    if any(word in text for word in ("macos", "macosx", "darwin")):
        result.add("macos")
    return result


def _platforms_from_classifiers(classifiers: list[str]) -> set[str]:
    result: set[str] = set()
    for classifier in classifiers:
        value = classifier.lower()
        if "microsoft :: windows" in value:
            result.add("windows")
        if "posix :: linux" in value or "operating system :: linux" in value:
            result.add("linux")
        if (
            "operating system :: macos" in value
            or "operating system :: mac os" in value
        ):
            result.add("macos")
    return result


def _read_project_platforms(
    root: Path, input_paths: list[Path] | None = None
) -> tuple[set[str], list[Finding]]:
    path = root / "pyproject.toml"
    if input_paths is not None and (path.exists() or path.is_symlink()):
        input_paths.append(path)
    if not path.exists():
        return set(), []
    if not _safe_project_path(root, path):
        return set(), [
            Finding(
                "CG902",
                "warning",
                "high",
                "pyproject.toml",
                1,
                1,
                "metadata path",
                "Project metadata symlink or path escape was skipped.",
                "CrossGrid reads only non-symlink files inside the selected project root.",
            )
        ]
    try:
        metadata = tomllib.loads(
            _read_limited_bytes(path, MAX_METADATA_BYTES).decode("utf-8")
        )
    except (
        OSError,
        UnicodeError,
        tomllib.TOMLDecodeError,
        RecursionError,
        _InputTooLarge,
    ) as exc:
        return set(), [
            Finding(
                "CG902",
                "warning",
                "high",
                "pyproject.toml",
                1,
                1,
                "metadata",
                f"Could not read project classifiers: {exc.__class__.__name__}.",
                "TOML metadata could not be parsed.",
            )
        ]
    project = metadata.get("project", {})
    tool = metadata.get("tool", {})
    if not isinstance(project, dict) or not isinstance(tool, dict):
        return set(), [
            Finding(
                "CG902",
                "warning",
                "high",
                "pyproject.toml",
                1,
                1,
                "metadata shape",
                "Project metadata tables have an invalid shape.",
                "Expected [project] and [tool] values to be TOML tables.",
            )
        ]
    poetry = tool.get("poetry", {})
    if not isinstance(poetry, dict):
        return set(), [
            Finding(
                "CG902",
                "warning",
                "high",
                "pyproject.toml",
                1,
                1,
                "metadata shape",
                "Poetry metadata has an invalid shape.",
                "Expected [tool.poetry] to be a TOML table.",
            )
        ]
    classifiers = project.get("classifiers", []) or poetry.get("classifiers", [])
    if not isinstance(classifiers, list):
        return set(), [
            Finding(
                "CG902",
                "warning",
                "high",
                "pyproject.toml",
                1,
                1,
                "classifiers",
                "Project classifiers must be a TOML array.",
                "Classifiers could not be used to infer declared platform scope.",
            )
        ]
    return _platforms_from_classifiers([str(value) for value in classifiers]), []


def _command_invokes_test_tool(command: str) -> bool:
    assignment = r"(?:[A-Za-z_][A-Za-z0-9_]*=(?:\"[^\"]*\"|'[^']*'|[^\s]+)\s+)*"
    invocation = re.compile(
        r"^(?:env\s+)?"
        + assignment
        + r"(?:env\s+)?"
        + assignment
        + r"(?:(?:uv\s+run|poetry\s+run|pipenv\s+run)(?:\s+--[^\s]+)*\s+)*"
        + r"(?:(?:coverage|coverage3)\s+run(?:\s+-m)?\s+)?"
        + r"(?:(?:python(?:\d+(?:\.\d+)*)?|py(?:\s+-\d+(?:\.\d+)*)?)\s+-m\s+(?P<module>pytest|unittest)|(?P<tool>pytest|tox|nox))"
        + r"(?P<args>(?:\s+.*)?)$",
        re.IGNORECASE,
    )
    for line in command.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for segment in re.split(r"\s*(?:&&|\|\||[;|&])\s*", stripped):
            match = invocation.search(segment.strip())
            if match is None:
                continue
            test_tool = (match.group("module") or match.group("tool")).lower()
            if test_tool in {"tox", "nox"}:
                if test_tool == "tox" and _tox_has_non_run_subcommand(
                    match.group("args")
                ):
                    continue
                selector_values = re.findall(
                    r"(?<!\S)(?:-e|--env|--envlist|-s|--session|--sessions)(?:=|\s+)([A-Za-z0-9_.-]+)",
                    match.group("args"),
                    re.IGNORECASE,
                )
                selected_test_environment = any(
                    re.search(
                        r"(?<![a-z0-9])(?:test|tests|unit|unittest|pytest)(?![a-z0-9])",
                        name,
                        re.IGNORECASE,
                    )
                    for value in selector_values
                    for name in value.split(",")
                )
                if not selected_test_environment:
                    continue
            options = re.findall(
                r"(?<!\S)(--[A-Za-z][A-Za-z-]*(?:=[^\s]+)?|-[A-Za-z]+)(?=\s|$)",
                match.group("args"),
            )
            long_options = {
                option.split("=", 1)[0].lower()
                for option in options
                if option.startswith("--")
            }
            short_options = {
                option for option in options if not option.startswith("--")
            }
            non_execution_long = {
                "pytest": {
                    "--help",
                    "--version",
                    "--collect-only",
                    "--co",
                    "--fixtures",
                    "--fixtures-per-test",
                    "--markers",
                },
                "unittest": {"--help"},
                "tox": {
                    "--help",
                    "--version",
                    "--listenvs",
                    "--listenvs-all",
                    "--notest",
                    "--pkg-only",
                    "--sdistonly",
                    "--devenv",
                },
                "nox": {"--help", "--version", "--list", "--list-sessions"},
            }
            non_execution_short = {
                "pytest": {"-h", "-V"},
                "unittest": {"-h"},
                "tox": {"-h", "-V", "-n", "-b"},
                "nox": {"-h", "-V"},
            }
            if not (
                long_options.intersection(non_execution_long[test_tool])
                or short_options.intersection(non_execution_short[test_tool])
            ):
                return True
    return False


def _tox_has_non_run_subcommand(arguments: str) -> bool:
    try:
        tokens = shlex.split(arguments)
    except ValueError:
        return True
    value_options = {
        "-e",
        "--env",
        "--envlist",
        "-m",
        "--label",
        "-f",
        "--factor",
        "--skip-env",
        "-c",
        "--conf",
        "--workdir",
        "--root",
        "--runner",
        "-x",
        "--override",
        "--result-json",
        "--hashseed",
        "--discover",
        "--format",
        "-o",
    }
    run_commands = {"run", "r", "run-parallel", "p"}
    known_commands = run_commands | {
        "depends",
        "de",
        "man",
        "list",
        "l",
        "devenv",
        "d",
        "schema",
        "config",
        "c",
        "quickstart",
        "q",
        "exec",
        "e",
        "legacy",
        "le",
    }
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in known_commands:
            return token not in run_commands
        if token in value_options:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return True
    return False


def _is_test_job(name: str, job: dict) -> bool:
    label_parts = [name, str(job.get("name", ""))]
    commands: list[str] = []
    for step in job.get("steps", []) if isinstance(job.get("steps", []), list) else []:
        if isinstance(step, dict):
            label_parts.append(str(step.get("name", "")))
            commands.append(str(step.get("run", "")))
    test_label = re.compile(r"(?<![a-z0-9])test(?:s|ing)?(?![a-z0-9])", re.IGNORECASE)
    normalized_labels = [re.sub(r"[_-]+", " ", label) for label in label_parts]
    return any(test_label.search(label) for label in normalized_labels) or any(
        _command_invokes_test_tool(command) for command in commands
    )


def _runner_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, (str, int, float))]
    return []


def _runs_on_uses_matrix_os(value: Any) -> bool:
    pattern = re.compile(
        r"^\s*\$\{\{\s*matrix\s*(?:\.\s*os\b|\[\s*['\"]os['\"]\s*\])\s*\}\}\s*$",
        re.IGNORECASE,
    )
    return any(pattern.search(item) for item in _runner_values(value))


def _matrix_os_values(matrix: Any) -> tuple[list[str], str | None]:
    if not isinstance(matrix, dict):
        return [], "matrix is not a mapping"
    dimensions: dict[str, list[Any]] = {}
    for key, values in matrix.items():
        if key in {"include", "exclude"}:
            continue
        if not isinstance(values, list) or any(
            not isinstance(value, (str, int, float, bool)) for value in values
        ):
            return [], f"matrix dimension {key!r} is not a scalar list"
        dimensions[str(key)] = values

    combinations_count = 1
    for values in dimensions.values():
        combinations_count *= len(values)
        if combinations_count > 256:
            return [], "matrix exceeds the 256-combination analysis limit"
    keys = list(dimensions)
    combinations = (
        [
            dict(zip(keys, values))
            for values in itertools.product(*(dimensions[key] for key in keys))
        ]
        if keys
        else [{}]
    )
    exclusions = matrix.get("exclude", [])
    if isinstance(exclusions, list):
        combinations = [
            combination
            for combination in combinations
            if not any(
                isinstance(exclusion, dict)
                and exclusion
                and all(
                    key in combination and combination[key] == value
                    for key, value in exclusion.items()
                )
                for exclusion in exclusions
            )
        ]
    values = [
        str(combination["os"])
        for combination in combinations
        if isinstance(combination.get("os"), (str, int, float, bool))
    ]
    includes = matrix.get("include", [])
    if isinstance(includes, list):
        values.extend(
            str(entry["os"])
            for entry in includes
            if isinstance(entry, dict)
            and isinstance(entry.get("os"), (str, int, float, bool))
        )
    if not values:
        return [], "matrix.os has no values after supported include/exclude handling"
    return values, None


def _scan_workflows(
    root: Path, input_paths: list[Path] | None = None
) -> tuple[list[dict], set[str], list[Finding]]:
    workflow_dir = root / ".github" / "workflows"
    if not workflow_dir.is_dir():
        return [], set(), []
    runners: list[dict] = []
    findings: list[Finding] = []
    all_test_platforms: set[str] = set()
    if not _safe_project_path(root, workflow_dir):
        return (
            [],
            set(),
            [
                Finding(
                    "CG903",
                    "warning",
                    "high",
                    ".github/workflows",
                    1,
                    1,
                    "workflow path",
                    "Workflow directory symlink or path escape was skipped.",
                    "CrossGrid reads only non-symlink paths inside the selected project root.",
                )
            ],
        )
    workflow_paths = heapq.nsmallest(
        MAX_WORKFLOW_FILES + 1,
        itertools.chain(workflow_dir.glob("*.yml"), workflow_dir.glob("*.yaml")),
    )
    if len(workflow_paths) > MAX_WORKFLOW_FILES:
        findings.append(
            Finding(
                "CG903",
                "warning",
                "high",
                ".github/workflows",
                1,
                1,
                "workflow file limit",
                f"Workflow scan stopped at the {MAX_WORKFLOW_FILES}-file limit.",
                "The directory contains more GitHub Actions workflows than CrossGrid analyzes in one run.",
            )
        )
        workflow_paths = workflow_paths[:MAX_WORKFLOW_FILES]

    workflow_bytes = 0
    for path in workflow_paths:
        relative = path.relative_to(root).as_posix()
        if input_paths is not None:
            input_paths.append(path)
        if not _safe_project_path(root, path):
            findings.append(
                Finding(
                    "CG903",
                    "warning",
                    "high",
                    relative,
                    1,
                    1,
                    "workflow path",
                    "Workflow symlink or path escape was skipped.",
                    "CrossGrid reads only non-symlink files inside the selected project root.",
                )
            )
            continue
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if size > MAX_WORKFLOW_BYTES:
            findings.append(
                Finding(
                    "CG903",
                    "warning",
                    "high",
                    relative,
                    1,
                    1,
                    "workflow size limit",
                    f"Workflow exceeds the {MAX_WORKFLOW_BYTES}-byte per-file limit.",
                    "This workflow was not read or analyzed.",
                )
            )
            continue
        if workflow_bytes + size > MAX_TOTAL_WORKFLOW_BYTES:
            findings.append(
                Finding(
                    "CG903",
                    "warning",
                    "high",
                    relative,
                    1,
                    1,
                    "total workflow size limit",
                    f"Workflow scan stopped at the {MAX_TOTAL_WORKFLOW_BYTES}-byte total input limit.",
                    "Remaining workflow files were not read or analyzed.",
                )
            )
            break
        workflow_bytes += size
        try:
            data = yaml.safe_load(
                _read_limited_bytes(path, MAX_WORKFLOW_BYTES).decode("utf-8")
            )
        except (
            OSError,
            UnicodeError,
            yaml.YAMLError,
            RecursionError,
            _InputTooLarge,
        ) as exc:
            findings.append(
                Finding(
                    "CG903",
                    "warning",
                    "high",
                    relative,
                    1,
                    1,
                    "workflow",
                    f"Could not parse workflow YAML ({exc.__class__.__name__}).",
                    "The workflow was not included in runner coverage.",
                )
            )
            continue
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), dict):
            continue
        for job_id, job in data["jobs"].items():
            if not isinstance(job, dict):
                continue
            test_job = _is_test_job(str(job_id), job)
            strategy = job.get("strategy", {})
            matrix = strategy.get("matrix", {}) if isinstance(strategy, dict) else {}
            runs_on = job.get("runs-on")
            matrix_unknown_reason = None
            if _runs_on_uses_matrix_os(runs_on):
                raw_values, matrix_unknown_reason = _matrix_os_values(matrix)
            else:
                raw_values = [
                    value for value in _runner_values(runs_on) if "${{" not in value
                ]
            # Matrix OS labels count only when runs-on actually depends on matrix.os.
            unique = sorted(set(raw_values))
            platforms = sorted(
                set().union(*(_platforms_from_runner(value) for value in unique))
                if unique
                else set()
            )
            if (
                _runs_on_uses_matrix_os(runs_on)
                and matrix_unknown_reason is None
                and any(not _platforms_from_runner(value) for value in unique)
            ):
                matrix_unknown_reason = (
                    "matrix.os contains runner labels that do not map to a supported OS"
                )
            if not unique:
                if matrix_unknown_reason:
                    findings.append(
                        Finding(
                            "CG906",
                            "warning",
                            "medium",
                            relative,
                            1,
                            1,
                            str(job_id),
                            "The matrix runner could not be mapped to explicit OS labels.",
                            matrix_unknown_reason,
                        )
                    )
                    runners.append(
                        {
                            "workflow": relative,
                            "job": str(job_id),
                            "test_evidence": test_job,
                            "runners": [],
                            "platforms": [],
                            "unknown_platforms": True,
                        }
                    )
                continue
            if matrix_unknown_reason:
                findings.append(
                    Finding(
                        "CG906",
                        "warning",
                        "medium",
                        relative,
                        1,
                        1,
                        str(job_id),
                        "The matrix runner could not be mapped to explicit OS labels.",
                        matrix_unknown_reason,
                    )
                )
            runner = {
                "workflow": relative,
                "job": str(job_id),
                "test_evidence": test_job,
                "runners": unique,
                "platforms": platforms,
                "unknown_platforms": bool(matrix_unknown_reason),
            }
            runners.append(runner)
            if test_job:
                all_test_platforms.update(platforms)
    return runners, all_test_platforms, findings


def _skip_platforms(text: str) -> set[str]:
    lowered = text.lower()
    if not any(marker in lowered for marker in SKIP_MARKERS):
        return set()
    result: set[str] = set()
    if any(
        token in lowered
        for token in ("win32", "windows", "os.name == 'nt'", 'os.name == "nt"')
    ):
        result.add("windows")
    if any(token in lowered for token in ("linux", "ubuntu")):
        result.add("linux")
    if any(token in lowered for token in ("darwin", "macos", "mac os")):
        result.add("macos")
    if "posix" in lowered:
        result.update({"linux", "macos"})
    return result


def _node_text(node: ast.AST, source: str) -> str:
    return ast.get_source_segment(source, node) or ""


def _ast_column(source: str, node: ast.AST) -> int:
    """Convert Python AST's UTF-8 byte offset to a 1-based character column."""
    line_number = getattr(node, "lineno", 0)
    byte_column = getattr(node, "col_offset", 0)
    lines = source.splitlines()
    if not 1 <= line_number <= len(lines):
        return byte_column + 1
    prefix = (
        lines[line_number - 1]
        .encode("utf-8")[:byte_column]
        .decode("utf-8", errors="ignore")
    )
    return len(prefix) + 1


def _is_os_guard(ancestors: list[ast.AST], source: str, node: ast.AST) -> bool:
    for parent in reversed(ancestors):
        if isinstance(parent, ast.If):
            text = _node_text(parent.test, source).lower()
            if any(marker in text for marker in OS_GUARD_MARKERS):
                return True
        if isinstance(parent, ast.Try):
            for handler in parent.handlers:
                text = _node_text(handler.type, source).lower() if handler.type else ""
                if "importerror" in text or "modulenotfounderror" in text:
                    # Try/except only protects imports in its body, not names used later.
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        return True
    return False


def _alias_maps(tree: ast.Module) -> tuple[dict[str, str], dict[str, str]]:
    modules: dict[str, str] = {}
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                modules[local] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return modules, names


def _canonical_name(
    node: ast.AST, modules: dict[str, str], names: dict[str, str]
) -> str:
    if isinstance(node, ast.Name):
        return names.get(node.id, modules.get(node.id, node.id))
    if isinstance(node, ast.Attribute):
        parent = _canonical_name(node.value, modules, names)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _open_api_name(
    node: ast.Call,
    modules: dict[str, str],
    names: dict[str, str],
    path_instances: set[str],
) -> str | None:
    called = _canonical_name(node.func, modules, names)
    if called == "open" and isinstance(node.func, ast.Name):
        return "open"
    known = {"builtins.open", "io.open", "pathlib.Path.open", "pathlib.PurePath.open"}
    if called in known:
        return called
    # Handle Path("file").open(...) while leaving arbitrary archive and third-party .open() calls alone.
    if (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "open"
        and isinstance(node.func.value, ast.Call)
    ):
        constructor = _canonical_name(node.func.value.func, modules, names)
        if constructor in {"pathlib.Path", "pathlib.PurePath"}:
            return f"{constructor}.open"
    if (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "open"
        and isinstance(node.func.value, ast.Name)
    ):
        if node.func.value.id in path_instances:
            return "pathlib.Path.open"
    return None


def _path_instance_names(
    tree: ast.Module, modules: dict[str, str], names: dict[str, str]
) -> set[str]:
    """Conservatively track names whose every visible binding is a direct Path construction."""
    bindings: dict[str, list[bool]] = {}

    def target_names(target: ast.AST) -> list[str]:
        if isinstance(target, ast.Name):
            return [target.id]
        if isinstance(target, (ast.Tuple, ast.List)):
            return [name for item in target.elts for name in target_names(item)]
        if isinstance(target, ast.Starred):
            return target_names(target.value)
        return []

    def bind(target: ast.AST, is_path: bool = False) -> None:
        for name in target_names(target):
            bindings.setdefault(name, []).append(is_path)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
            value = node.value
        elif isinstance(node, ast.NamedExpr):
            targets = [node.target]
            value = node.value
        else:
            if isinstance(node, ast.arg):
                bindings.setdefault(node.arg, []).append(False)
            elif isinstance(node, (ast.AugAssign, ast.For, ast.AsyncFor)):
                bind(node.target, False)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if item.optional_vars:
                        bind(item.optional_vars, False)
            elif isinstance(node, ast.comprehension):
                bind(node.target, False)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bindings.setdefault(node.name, []).append(False)
            elif isinstance(node, ast.MatchAs) and node.name:
                bindings.setdefault(node.name, []).append(False)
            elif isinstance(node, ast.MatchStar) and node.name:
                bindings.setdefault(node.name, []).append(False)
            elif isinstance(node, ast.MatchMapping) and node.rest:
                bindings.setdefault(node.rest, []).append(False)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    bindings.setdefault(
                        alias.asname or alias.name.split(".")[0], []
                    ).append(False)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    bindings.setdefault(alias.asname or alias.name, []).append(False)
            elif isinstance(node, ast.Delete):
                bind(ast.Tuple(elts=node.targets, ctx=ast.Load()), False)
            continue
        constructor = (
            _canonical_name(value.func, modules, names)
            if isinstance(value, ast.Call)
            else ""
        )
        is_path = constructor in {"pathlib.Path", "pathlib.PurePath"}
        for target in targets:
            bind(target, is_path)
    return {name for name, states in bindings.items() if states and all(states)}


def _walk_with_ancestors(tree: ast.AST):
    stack: list[tuple[ast.AST, list[ast.AST]]] = [(tree, [])]
    while stack:
        node, ancestors = stack.pop()
        yield node, ancestors
        for child in reversed(list(ast.iter_child_nodes(node))):
            stack.append((child, [*ancestors, node]))


def _module_imports(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name.split(".")[0] for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        return [node.module.split(".")[0]]
    return []


def _scan_python(path: Path, root: Path) -> tuple[list[Finding], list[dict]]:
    relative = path.relative_to(root).as_posix()
    try:
        encoded = _read_limited_bytes(path, MAX_PYTHON_SOURCE_BYTES)
        encoding, _ = tokenize.detect_encoding(io.BytesIO(encoded).readline)
        source = encoded.decode(encoding)
    except (OSError, UnicodeError, SyntaxError, _InputTooLarge) as exc:
        return [
            Finding(
                "CG904",
                "warning",
                "high",
                relative,
                1,
                1,
                "source",
                f"Could not read Python source ({exc.__class__.__name__}).",
                "Source file was not analyzed.",
            )
        ], []
    try:
        tree = ast.parse(source, filename=relative)
    except RecursionError:
        return [
            Finding(
                "CG905",
                "warning",
                "high",
                relative,
                1,
                1,
                "source",
                "Python syntax exceeded the parser's nesting limit.",
                "This file was not included in source risk analysis.",
            )
        ], []
    except SyntaxError as exc:
        return [
            Finding(
                "CG905",
                "warning",
                "high",
                relative,
                exc.lineno or 1,
                exc.offset or 1,
                "source",
                "Python syntax could not be parsed.",
                "This file was not included in source risk analysis.",
            )
        ], []

    modules, names = _alias_maps(tree)
    path_instances = _path_instance_names(tree, modules, names)
    findings: list[Finding] = []
    skipped: list[dict] = []
    for node, ancestors in _walk_with_ancestors(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for module in _module_imports(node):
                if module in PLATFORM_MODULES:
                    guarded = _is_os_guard(ancestors, source, node)
                    findings.append(
                        Finding(
                            "CG101",
                            "warning",
                            "high" if not guarded else "review",
                            relative,
                            node.lineno,
                            _ast_column(source, node),
                            module,
                            "Platform-specific module import; review its availability and fallback on declared target OSes.",
                            "Import appears inside an ImportError guard."
                            if guarded
                            else "No nearby platform guard or import fallback was identified.",
                        )
                    )
        elif isinstance(node, ast.Call):
            called = _canonical_name(node.func, modules, names)
            if called in PLATFORM_APIS:
                guarded = _is_os_guard(ancestors, source, node)
                findings.append(
                    Finding(
                        "CG102",
                        "warning",
                        "high" if not guarded else "review",
                        relative,
                        node.lineno,
                        _ast_column(source, node),
                        called,
                        "Platform-specific API call; confirm it is available or guarded on every declared OS.",
                        "An OS-related if guard was found in an ancestor."
                        if guarded
                        else "No OS-related ancestor guard was identified.",
                    )
                )
            open_api = _open_api_name(node, modules, names, path_instances)
            if open_api:
                mode = None
                path_api = open_api in {"pathlib.Path.open", "pathlib.PurePath.open"}
                receiver = (
                    node.func.value if isinstance(node.func, ast.Attribute) else None
                )
                unbound_path_call = (
                    path_api
                    and isinstance(receiver, (ast.Name, ast.Attribute))
                    and _canonical_name(receiver, modules, names)
                    in {"pathlib.Path", "pathlib.PurePath"}
                )
                mode_index = 1 if unbound_path_call else 0 if path_api else 1
                encoding_index = 3 if unbound_path_call else 2 if path_api else 3
                if len(node.args) > mode_index and isinstance(
                    node.args[mode_index], ast.Constant
                ):
                    mode = str(node.args[mode_index].value)
                if mode is None:
                    mode_keyword = next(
                        (keyword for keyword in node.keywords if keyword.arg == "mode"),
                        None,
                    )
                    if mode_keyword and isinstance(mode_keyword.value, ast.Constant):
                        mode = str(mode_keyword.value.value)
                encoding = next(
                    (keyword for keyword in node.keywords if keyword.arg == "encoding"),
                    None,
                )
                binary = isinstance(mode, str) and "b" in mode
                if encoding is not None:
                    encoding_is_default = (
                        isinstance(encoding.value, ast.Constant)
                        and encoding.value.value is None
                    )
                elif len(node.args) > encoding_index:
                    encoding_is_default = (
                        isinstance(node.args[encoding_index], ast.Constant)
                        and node.args[encoding_index].value is None
                    )
                else:
                    encoding_is_default = True
                if encoding_is_default and not binary:
                    findings.append(
                        Finding(
                            "CG201",
                            "review",
                            "medium",
                            relative,
                            node.lineno,
                            _ast_column(source, node),
                            open_api,
                            "Text file open() has no explicit encoding; platform defaults can differ.",
                            "Recognized built-in, io, and pathlib open APIs; dynamic modes or encodings supplied through **kwargs may be missed.",
                        )
                    )
            if called == "os.system" or (
                called.startswith("subprocess.")
                and any(
                    key.arg == "shell"
                    and isinstance(key.value, ast.Constant)
                    and key.value.value is True
                    for key in node.keywords
                )
            ):
                findings.append(
                    Finding(
                        "CG301",
                        "review",
                        "medium",
                        relative,
                        node.lineno,
                        _ast_column(source, node),
                        called,
                        "Shell execution can vary between cmd.exe and POSIX shells; review commands and prefer argument lists where possible.",
                        "Static analysis cannot determine whether the command is portable.",
                    )
                )
            if called.endswith("NamedTemporaryFile"):
                findings.append(
                    Finding(
                        "CG202",
                        "review",
                        "low",
                        relative,
                        node.lineno,
                        _ast_column(source, node),
                        called,
                        "Temporary-file reopen/locking behavior differs across platforms; review tests that reopen this path before close.",
                        "Potential Windows file-locking risk; no reopen behavior is inferred.",
                    )
                )
        elif isinstance(node, (ast.Attribute, ast.Name)):
            called = _canonical_name(node, modules, names)
            if called in PLATFORM_CONSTANTS:
                guarded = _is_os_guard(ancestors, source, node)
                findings.append(
                    Finding(
                        "CG102",
                        "warning",
                        "high" if not guarded else "review",
                        relative,
                        node.lineno,
                        _ast_column(source, node),
                        called,
                        "Platform-specific signal constant; confirm availability or guard it on every declared OS.",
                        "An OS-related if guard was found in an ancestor."
                        if guarded
                        else "No OS-related ancestor guard was identified.",
                    )
                )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for decorator in node.decorator_list:
                text = _node_text(decorator, source)
                lowered = text.lower()
                if "skipif" in lowered or "skipunless" in lowered:
                    if isinstance(decorator, ast.Call) and decorator.args:
                        condition = decorator.args[0]
                    elif isinstance(decorator, ast.Call):
                        condition = next(
                            (
                                keyword.value
                                for keyword in decorator.keywords
                                if keyword.arg == "condition"
                            ),
                            decorator,
                        )
                    else:
                        condition = decorator
                    condition_text = _node_text(condition, source)
                    platforms_set = _skip_platforms(condition_text)
                    if "skipunless" in lowered:
                        platforms_set = {"windows", "linux", "macos"} - platforms_set
                    platforms = sorted(platforms_set)
                    if platforms:
                        skipped.append(
                            {
                                "file": relative,
                                "line": decorator.lineno,
                                "subject": getattr(node, "name", "test"),
                                "platforms": platforms,
                                "condition": text[:240],
                            }
                        )
                        findings.append(
                            Finding(
                                "CG801",
                                "note",
                                "medium",
                                relative,
                                decorator.lineno,
                                _ast_column(source, decorator),
                                getattr(node, "name", "test"),
                                "Test has an explicit platform-related skip condition.",
                                f"Detected target platform(s): {', '.join(platforms)}. Condition is shown as source text only.",
                            )
                        )
    return findings, skipped


def scan_project(
    root_path: str | Path,
    supported_override: list[str] | None = None,
    *,
    _input_paths: list[Path] | None = None,
) -> dict:
    try:
        root = Path(root_path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("Could not resolve project path safely.") from exc
    if not root.is_dir():
        raise ValueError(f"Project path is not a directory: {root}")
    code_findings: list[Finding] = []
    skips: list[dict] = []
    python_files: list[Path] = []
    file_limit_reached = False
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        allowed_dirs = []
        for name in dirs:
            candidate = current_path / name
            if name in SKIP_DIRS or name.startswith(".") or candidate.is_symlink():
                continue
            try:
                resolved = candidate.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if resolved.is_relative_to(root):
                allowed_dirs.append(name)
        dirs[:] = sorted(allowed_dirs)
        for name in sorted(files):
            if name.endswith(".py"):
                candidate = current_path / name
                relative = candidate.relative_to(root).as_posix()
                if _input_paths is not None:
                    _input_paths.append(candidate)
                if len(python_files) >= MAX_PYTHON_FILES:
                    code_findings.append(
                        Finding(
                            "CG904",
                            "warning",
                            "high",
                            relative,
                            1,
                            1,
                            "source file limit",
                            f"Python scan stopped at the {MAX_PYTHON_FILES}-file limit.",
                            "The project contains more Python files than CrossGrid analyzes in one run.",
                        )
                    )
                    file_limit_reached = True
                    break
                if not _safe_project_path(root, candidate):
                    code_findings.append(
                        Finding(
                            "CG904",
                            "warning",
                            "high",
                            relative,
                            1,
                            1,
                            "source path",
                            "Unsafe or symlinked Python source was skipped.",
                            "CrossGrid does not follow symlinks or read paths outside the scan root.",
                        )
                    )
                    continue
                try:
                    python_files.append(candidate.resolve(strict=True))
                except (OSError, RuntimeError):
                    code_findings.append(
                        Finding(
                            "CG904",
                            "warning",
                            "high",
                            relative,
                            1,
                            1,
                            "source path",
                            "Python source path could not be resolved safely.",
                            "CrossGrid does not follow paths that change or fail to resolve during scanning.",
                        )
                    )
        if file_limit_reached:
            dirs[:] = []
            break

    source_bytes = 0
    for path in sorted(python_files):
        try:
            file_size = path.stat().st_size
        except OSError:
            file_size = 0
        if file_size > MAX_PYTHON_SOURCE_BYTES:
            code_findings.append(
                Finding(
                    "CG904",
                    "warning",
                    "high",
                    path.relative_to(root).as_posix(),
                    1,
                    1,
                    "source size limit",
                    f"Python source exceeds the {MAX_PYTHON_SOURCE_BYTES}-byte per-file limit.",
                    "This file was not read or analyzed.",
                )
            )
            continue
        if source_bytes + file_size > MAX_TOTAL_SOURCE_BYTES:
            code_findings.append(
                Finding(
                    "CG904",
                    "warning",
                    "high",
                    path.relative_to(root).as_posix(),
                    1,
                    1,
                    "total source size limit",
                    f"Python source scan stopped at the {MAX_TOTAL_SOURCE_BYTES}-byte total input limit.",
                    "Remaining Python files were not read or analyzed.",
                )
            )
            break
        source_bytes += file_size
        findings, file_skips = _scan_python(path, root)
        code_findings.extend(findings)
        skips.extend(file_skips)

    declared, metadata_findings = _read_project_platforms(root, _input_paths)
    runners, ci_platforms, workflow_findings = _scan_workflows(root, _input_paths)
    target_platforms = set(supported_override) if supported_override else declared
    has_unknown_test_matrix = any(
        row.get("unknown_platforms") and row["test_evidence"] for row in runners
    )
    scope_source = (
        "command-line override"
        if supported_override
        else "project classifiers"
        if declared
        else "not declared"
    )
    if not target_platforms and ci_platforms:
        scope_source = "CI runners only; support is not declared"
    findings = [*metadata_findings, *workflow_findings, *code_findings]

    if not python_files:
        findings.append(
            Finding(
                "CG900",
                "note",
                "high",
                ".",
                1,
                1,
                "project",
                "No Python source files were found.",
                "Check the scan root and ignored-directory rules.",
            )
        )
    if not runners:
        findings.append(
            Finding(
                "CG902",
                "warning",
                "high",
                ".github/workflows",
                1,
                1,
                "test CI",
                "No explicit GitHub Actions runner was identified.",
                "Other CI providers and dynamically inherited workflows are not analyzed.",
            )
        )
    elif not ci_platforms and not has_unknown_test_matrix:
        findings.append(
            Finding(
                "CG902",
                "warning",
                "high",
                ".github/workflows",
                1,
                1,
                "test CI",
                "No recognized OS runner in a test-like job was identified.",
                "The tool only counts test-like jobs with explicit OS labels.",
            )
        )
    if not target_platforms:
        findings.append(
            Finding(
                "CG903",
                "note",
                "high",
                "pyproject.toml",
                1,
                1,
                "support scope",
                "No supported OS scope was declared or supplied.",
                "CrossGrid will not infer a support guarantee from source code.",
            )
        )

    platform_rows = []
    for platform in ("windows", "linux", "macos"):
        if platform not in target_platforms:
            continue
        matches = [
            runner
            for runner in runners
            if runner["test_evidence"] and platform in runner["platforms"]
        ]
        skipped_tests = [row for row in skips if platform in row["platforms"]]
        unknown_jobs = [
            f"{runner['workflow']} · {runner['job']}"
            for runner in runners
            if runner.get("unknown_platforms") and runner["test_evidence"]
        ]
        coverage_status = (
            "covered" if matches else "unknown" if unknown_jobs else "missing"
        )
        platform_rows.append(
            {
                "platform": platform,
                "declared": platform in declared,
                "in_scope": platform in target_platforms,
                "test_ci_present": bool(matches),
                "test_ci_status": coverage_status,
                "test_jobs": [
                    {
                        "workflow": row["workflow"],
                        "job": row["job"],
                        "runners": row["runners"],
                    }
                    for row in matches
                ],
                "unknown_test_jobs": unknown_jobs,
                "platform_skip_count": len(skipped_tests),
                "platform_skips": skipped_tests,
            }
        )
        if coverage_status == "missing":
            findings.append(
                Finding(
                    "CG901",
                    "warning",
                    "high",
                    ".github/workflows",
                    1,
                    1,
                    platform,
                    f"Project support scope includes {platform}, but no explicit test-like GitHub Actions runner was found.",
                    "This is a CI coverage gap signal, not proof that tests are absent from another CI provider.",
                )
            )

    findings.sort(key=lambda item: (item.file, item.line, item.rule_id, item.subject))
    summary = {
        "python_files": len(python_files),
        "findings": len(findings),
        "warnings": sum(item.severity == "warning" for item in findings),
        "reviews": sum(item.severity == "review" for item in findings),
        "notes": sum(item.severity == "note" for item in findings),
        "declared_platforms": sorted(declared),
        "test_ci_platforms": sorted(ci_platforms),
        "test_ci_unknown": has_unknown_test_matrix,
    }
    return {
        "schema_version": 1,
        "tool": "CrossGrid",
        "root_name": root.name,
        "scope_source": scope_source,
        "support_scope": sorted(target_platforms),
        "platforms": platform_rows,
        "runners": runners,
        "summary": summary,
        "findings": [asdict(item) for item in findings],
        "limits": [
            "Static heuristics do not prove portability or replace real OS test execution.",
            "Only explicit GitHub Actions runner and simple Python marker patterns are counted.",
            "Oversized or non-scalar matrix runners are marked unknown; they do not produce per-OS missing-runner findings.",
            "Dynamic imports, shell expansion, reusable workflow inheritance, third-party CI, and package wheel availability may be missed.",
            "An explicit encoding or nearby platform guard can still be incorrect.",
        ],
    }


def report_exit_code(report: dict, fail_on: str = "error") -> int:
    if fail_on == "none":
        return 0
    severity_rank = {"note": 0, "review": 1, "warning": 2, "error": 3}
    threshold = severity_rank[fail_on]
    return (
        1
        if any(
            severity_rank[item["severity"]] >= threshold for item in report["findings"]
        )
        else 0
    )


def render_sarif(report: dict) -> dict:
    rule_ids = sorted({item["rule_id"] for item in report["findings"]})
    results = []
    for item in report["findings"]:
        results.append(
            {
                "ruleId": item["rule_id"],
                "level": {
                    "error": "error",
                    "warning": "warning",
                    "review": "note",
                    "note": "note",
                }[item["severity"]],
                "message": {"text": item["message"]},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": item["file"]},
                            "region": {
                                "startLine": item["line"],
                                "startColumn": item["column"],
                            },
                        }
                    }
                ],
                "properties": {
                    "confidence": item["confidence"],
                    "subject": item["subject"],
                    "evidence": item["evidence"],
                },
            }
        )
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "CrossGrid",
                        "version": "0.1.0",
                        "rules": [{"id": rule_id} for rule_id in rule_ids],
                    }
                },
                "results": results,
            }
        ],
    }


def render_html(report: dict) -> str:
    import html

    rows = []
    for item in report["findings"]:
        rows.append(
            '<tr><td><span class="severity {severity}">{severity}</span></td><td><code>{rule}</code></td>'
            "<td><strong>{subject}</strong><p>{message}</p><small>{evidence}</small></td>"
            "<td><code>{file}:{line}</code></td><td>{confidence}</td></tr>".format(
                severity=html.escape(item["severity"]),
                rule=html.escape(item["rule_id"]),
                subject=html.escape(item["subject"]),
                message=html.escape(item["message"]),
                evidence=html.escape(item["evidence"]),
                file=html.escape(item["file"]),
                line=item["line"],
                confidence=html.escape(item["confidence"]),
            )
        )
    finding_rows = (
        "\n".join(rows)
        or '<tr><td colspan="5" class="empty">No static review signals found. This is not a portability guarantee.</td></tr>'
    )
    platform_cards = []
    for row in report["platforms"]:
        coverage_status = row.get(
            "test_ci_status",
            "covered" if row["test_ci_present"] else "missing",
        )
        coverage_labels = {
            "covered": ("covered", "TEST CI FOUND"),
            "missing": ("gap", "NO TEST RUNNER FOUND"),
            "unknown": ("unknown", "MATRIX NOT ANALYZED"),
        }
        status_class, status_label = coverage_labels[coverage_status]
        job_names = [f"{job['workflow']} · {job['job']}" for job in row["test_jobs"]]
        job_names.extend(row.get("unknown_test_jobs", []))
        platform_cards.append(
            '<article class="platform"><div class="platform-name">{name}</div><div class="status {status}">{status_label}</div>'
            "<p>{jobs}</p><small>{skips} platform-related skip marker(s)</small></article>".format(
                name=html.escape(row["platform"]),
                status=status_class,
                status_label=status_label,
                jobs=html.escape(
                    ", ".join(job_names) or "No explicit test job detected"
                ),
                skips=row["platform_skip_count"],
            )
        )
    cards = (
        "".join(platform_cards)
        or '<div class="empty-card">Declare target OSes in project metadata or pass --platform to build a coverage grid.</div>'
    )
    limits = "".join(f"<li>{html.escape(limit)}</li>" for limit in report["limits"])
    summary = report["summary"]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>CrossGrid · portability coverage</title>
<style>
:root{{--night:#102b37;--ink:#173642;--paper:#f2f3ed;--panel:#fbfcf8;--line:#cad4cc;--muted:#5d7478;--sea:#00a7a1;--sea-dark:#06716f;--orange:#bd552e;--gold:#e5b758;--mono:ui-monospace,"Cascadia Code",Consolas,monospace;--sans:Inter,"Segoe UI",sans-serif}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);line-height:1.5}}.layout{{max-width:1320px;margin:auto;padding:34px clamp(18px,4vw,58px) 60px}}
header{{display:flex;justify-content:space-between;align-items:center;border-top:5px solid var(--sea);padding:13px 0 22px;border-bottom:1px solid var(--line)}}.brand{{font:700 12px var(--mono);letter-spacing:.15em;text-transform:uppercase}}.brand b{{display:inline-block;background:var(--sea);color:white;padding:3px 7px;margin-right:9px;letter-spacing:.04em}}.scope{{font:11px var(--mono);color:var(--muted);text-align:right}}
.intro{{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(220px,.65fr);align-items:end;gap:24px;padding:36px 0 30px}}.kicker{{font:11px var(--mono);color:var(--sea-dark);letter-spacing:.12em;text-transform:uppercase}}h1{{font-size:clamp(39px,6vw,74px);line-height:.96;letter-spacing:-.06em;margin:12px 0 14px;max-width:800px}}.intro p{{max-width:690px;color:var(--muted);margin:0}}.score{{border-left:1px solid var(--line);padding:9px 0 9px 22px}}.score strong{{display:block;font:500 clamp(42px,7vw,75px) var(--mono);letter-spacing:-.08em;color:var(--sea-dark)}}.score span{{font:11px var(--mono);color:var(--muted);text-transform:uppercase;letter-spacing:.08em}}
.section-bar{{display:flex;justify-content:space-between;align-items:baseline;gap:15px;margin:17px 0 11px}}h2{{font-size:17px;letter-spacing:-.02em;margin:0}}.section-bar p{{font:11px var(--mono);color:var(--muted);margin:0}}.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}}.platform{{padding:17px 18px;background:var(--panel);border:1px solid var(--line);min-height:150px;position:relative}}.platform:before{{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--sea)}}.platform:has(.status.gap):before{{background:var(--orange)}}.platform:has(.status.unknown):before{{background:#b98d28}}.platform-name{{font:600 22px var(--mono);text-transform:uppercase}}.status{{display:inline-block;font:10px var(--mono);letter-spacing:.08em;margin:8px 0 5px;padding:3px 6px;background:#cde8e1;color:#085a55}}.status.gap{{background:#f3d4c9;color:#883c24}}.status.unknown{{background:#f3e8c5;color:#705512}}.platform p{{font-size:12px;color:var(--muted);margin:0 0 8px;overflow-wrap:anywhere}}.platform small{{font:10px var(--mono);color:var(--muted)}}.empty-card{{grid-column:1/-1;padding:22px;border:1px dashed var(--line);color:var(--muted)}}
.findings{{margin-top:35px}}.toolbar{{display:flex;justify-content:space-between;align-items:end;gap:14px;margin:14px 0}}label{{font-size:11px;color:var(--muted)}}input{{display:block;margin-top:6px;padding:9px 10px;background:var(--panel);border:1px solid var(--line);min-width:min(300px,70vw);font:inherit;color:var(--ink)}}input:focus-visible{{outline:3px solid var(--sea);outline-offset:2px}}.counter{{font:11px var(--mono);color:var(--muted)}}.table-box{{overflow:auto;border:1px solid var(--line);background:var(--panel)}}table{{border-collapse:collapse;width:100%;min-width:950px;text-align:left}}th{{padding:11px 13px;background:var(--night);color:#d7e9e2;font:10px var(--mono);letter-spacing:.1em;text-transform:uppercase}}td{{padding:13px;border-bottom:1px solid var(--line);vertical-align:top;font-size:12px}}td p{{margin:5px 0;color:var(--ink)}}td small{{color:var(--muted);display:block;max-width:430px}}td code{{font:11px var(--mono);overflow-wrap:anywhere}}.severity{{font:10px var(--mono);text-transform:uppercase;padding:3px 6px;background:#e8ede7}}.severity.warning{{background:#f2d8c8;color:#8b3d20}}.severity.review{{background:#f3e5bf;color:#765818}}.severity.note{{background:#d2e7e5;color:#155b59}}.empty{{text-align:center;padding:34px;color:var(--muted)}}
.limits{{margin-top:34px;background:#e2e9e2;padding:18px 22px;border-left:4px solid var(--gold)}}.limits h2{{margin-bottom:9px}}.limits ul{{margin:0;padding-left:18px;color:var(--muted);font-size:12px}}.limits li+li{{margin-top:4px}}footer{{margin-top:28px;padding-top:11px;border-top:1px solid var(--line);display:flex;justify-content:space-between;gap:14px;color:var(--muted);font:10px var(--mono)}}
@media(max-width:760px){{.intro{{grid-template-columns:1fr;align-items:start;padding:27px 0}}.score{{border-left:0;border-top:1px solid var(--line);padding:12px 0 0}}.score strong{{font-size:52px}}.grid{{grid-template-columns:1fr}}.toolbar{{align-items:start;flex-direction:column}}input{{width:100%}}footer{{flex-direction:column}}header{{align-items:start;gap:12px;flex-direction:column}}.scope{{text-align:left}}}}
@media(prefers-color-scheme:dark){{:root{{--night:#091d26;--ink:#dce9e5;--paper:#0e1b20;--panel:#17292e;--line:#334a4f;--muted:#a0b4b2;--sea:#35d3c6;--sea-dark:#70ded2;--orange:#f29670}}.limits{{background:#203239}}th{{background:var(--night)}}}}
@media(prefers-reduced-motion:reduce){{*,*::before,*::after{{scroll-behavior:auto!important;transition:none!important}}}}
</style></head><body><main class="layout"><header><div class="brand"><b>CG</b> CrossGrid · Python OS coverage</div><div class="scope">SCOPE: {html.escape(report["scope_source"])} · FILES: {summary["python_files"]}</div></header>
<section class="intro"><div><div class="kicker">Repository portability review</div><h1>Support is a matrix.<br>Make the gaps visible.</h1><p>This report connects declared operating systems to explicit test runners, platform skips and source review signals. It does not execute the project on other operating systems.</p></div><div class="score"><strong>{summary["findings"]:02d}</strong><span>review signals · {summary["warnings"]} warnings · {summary["reviews"]} cautions</span></div></section>
<section><div class="section-bar"><h2>Support coverage</h2><p>runner evidence ≠ successful test execution</p></div><div class="grid">{cards}</div></section>
<section class="findings"><div class="section-bar"><h2>Evidence register</h2><p>{summary["test_ci_platforms"] and html.escape(", ".join(summary["test_ci_platforms"])) or "NO TEST OS DETECTED"}</p></div><div class="toolbar"><label>Filter this report<input id="filter" type="search" placeholder="rule, path, API, or message" aria-label="Filter report findings"></label><span class="counter">{summary["findings"]} finding(s)</span></div>
<div class="table-box"><table><thead><tr><th>Kind</th><th>Rule</th><th>Signal and evidence</th><th>Location</th><th>Confidence</th></tr></thead><tbody id="findings">{finding_rows}</tbody></table></div></section>
<section class="limits"><h2>Limits of this scan</h2><ul>{limits}</ul></section>
<footer><span>CrossGrid · local static analysis · no files changed</span><span>Review each finding before changing code or CI.</span></footer></main>
<script>const input=document.getElementById('filter');input.addEventListener('input',()=>{{const q=input.value.toLowerCase();document.querySelectorAll('#findings tr').forEach(row=>row.hidden=!row.textContent.toLowerCase().includes(q));}});</script></body></html>"""
