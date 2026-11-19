from __future__ import annotations

import tempfile
import unittest
import json
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from crossgrid import __version__
from crossgrid.scanner import (
    MAX_METADATA_BYTES,
    report_exit_code,
    render_html,
    render_sarif,
    scan_project,
)


class CrossGridTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parents[1])
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self, name: str, content: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_maps_declared_os_to_explicit_test_ci_runners_and_skips(self) -> None:
        self.write(
            "pyproject.toml",
            """
[project]
name = "fixture"
classifiers = [
  "Operating System :: Microsoft :: Windows",
  "Operating System :: POSIX :: Linux",
  "Operating System :: MacOS",
]
""",
        )
        self.write(
            ".github/workflows/tests.yml",
            """
name: test matrix
on: [push]
jobs:
  tests:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest]
    steps:
      - name: Run tests
        run: python -m pytest
  style:
    runs-on: ubuntu-latest
    steps:
      - run: ruff check .
""",
        )
        self.write(
            "tests/test_sample.py",
            """
import sys
import pytest

@pytest.mark.skipif(sys.platform == "win32", reason="uses a Unix socket")
def test_unix_socket():
    pass
""",
        )

        report = scan_project(self.root)
        rows = {row["platform"]: row for row in report["platforms"]}
        self.assertEqual(report["support_scope"], ["linux", "macos", "windows"])
        self.assertTrue(rows["windows"]["test_ci_present"])
        self.assertTrue(rows["linux"]["test_ci_present"])
        self.assertFalse(rows["macos"]["test_ci_present"])
        self.assertEqual(rows["windows"]["platform_skip_count"], 1)
        self.assertIn(
            "macos",
            [row["subject"] for row in report["findings"] if row["rule_id"] == "CG901"],
        )
        self.assertIn("windows", report["summary"]["declared_platforms"])

    def test_test_job_detection_uses_names_and_test_tools_not_substrings(self) -> None:
        self.write(
            ".github/workflows/ci.yml",
            """
jobs:
  release-latest:
    runs-on: ubuntu-latest
    steps:
      - name: attest build provenance
        run: echo provenance
  windows-check:
    runs-on: windows-latest
    steps:
      - run: python -m pytest
""",
        )
        report = scan_project(self.root, supported_override=["linux", "windows"])
        self.assertEqual(report["summary"]["test_ci_platforms"], ["windows"])

    def test_installing_or_mentioning_test_tools_does_not_count_as_running_tests(
        self,
    ) -> None:
        self.write(
            ".github/workflows/ci.yml",
            """
jobs:
  build:
    runs-on: windows-latest
    steps:
      - name: Install pytest
        run: python -m pip install pytest
      - name: Print command example
        run: echo pytest
""",
        )
        report = scan_project(self.root, supported_override=["windows"])
        self.assertEqual(report["summary"]["test_ci_platforms"], [])

    def test_help_version_and_collection_only_commands_do_not_count_as_test_runs(
        self,
    ) -> None:
        self.write(
            ".github/workflows/ci.yml",
            """
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: pytest --version
      - run: pytest -V
      - run: pytest --help
      - run: pytest --collect-only
  package:
    runs-on: windows-latest
    steps:
      - run: python -m unittest --help
      - run: tox --version
      - run: nox --list-sessions
      - run: tox list
      - run: tox config
      - run: tox config -e unit-tests
      - run: tox run -e lint
      - run: tox -e unit-tests --notest
      - run: tox -e unit-tests --pkg-only
      - run: tox -e unit-tests --sdistonly
      - run: tox -e unit-tests --devenv=/tmp/test-env
      - run: tox -e unit-tests --devenv /tmp/test-env
      - run: nox -s lint
""",
        )
        report = scan_project(self.root, supported_override=["linux", "windows"])
        self.assertEqual(report["summary"]["test_ci_platforms"], [])

    def test_test_commands_support_environment_and_common_wrappers(self) -> None:
        self.write(
            ".github/workflows/ci.yml",
            """
jobs:
  linux-check:
    runs-on: ubuntu-latest
    steps:
      - run: env COVERAGE=1 pytest -v
  windows-check:
    runs-on: windows-latest
    steps:
      - run: py -3.12 -m unittest discover
  mac-check:
    runs-on: macos-latest
    steps:
      - run: coverage run -m pytest -q
      - run: tox run -e unit-tests
      - run: nox -s integration-tests
""",
        )
        report = scan_project(
            self.root, supported_override=["linux", "windows", "macos"]
        )
        self.assertEqual(
            report["summary"]["test_ci_platforms"], ["linux", "macos", "windows"]
        )

    def test_runner_matrix_include_is_counted(self) -> None:
        self.write(
            ".github/workflows/tests.yml",
            """
jobs:
  test:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        include:
          - os: ubuntu-latest
          - os: windows-latest
    steps:
      - run: pytest
""",
        )
        report = scan_project(self.root, supported_override=["linux", "windows"])
        self.assertEqual(report["summary"]["test_ci_platforms"], ["linux", "windows"])

    def test_static_runs_on_does_not_inherit_unrelated_matrix_os_values(self) -> None:
        self.write(
            ".github/workflows/tests.yml",
            """
jobs:
  tests:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        os: [windows-latest]
        include:
          - os: macos-latest
    steps:
      - run: pytest
""",
        )
        report = scan_project(
            self.root, supported_override=["linux", "windows", "macos"]
        )
        self.assertEqual(report["summary"]["test_ci_platforms"], ["linux"])

    def test_matrix_exclusions_remove_only_excluded_runner_combinations(self) -> None:
        self.write(
            ".github/workflows/tests.yml",
            """
jobs:
  tests:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest]
        python: ["3.11", "3.12"]
        exclude:
          - os: windows-latest
            python: "3.11"
    steps:
      - run: pytest
""",
        )
        report = scan_project(self.root, supported_override=["linux", "windows"])
        self.assertEqual(report["summary"]["test_ci_platforms"], ["linux", "windows"])

    def test_oversized_matrix_marks_coverage_unknown_instead_of_missing(self) -> None:
        self.write(
            "pyproject.toml",
            """
[project]
name = "fixture"
classifiers = [
  "Operating System :: Microsoft :: Windows",
  "Operating System :: POSIX :: Linux",
  "Operating System :: MacOS",
]
""",
        )
        versions = ", ".join(f'"{index}"' for index in range(86))
        self.write(
            ".github/workflows/tests.yml",
            f"""
jobs:
  tests:
    runs-on: ${{{{ matrix.os }}}}
    strategy:
      matrix:
        os: [windows-latest, ubuntu-latest, macos-latest]
        python: [{versions}]
    steps:
      - run: python -m unittest
""",
        )

        report = scan_project(self.root)
        rows = {row["platform"]: row for row in report["platforms"]}
        self.assertEqual(
            {platform: row["test_ci_status"] for platform, row in rows.items()},
            {"windows": "unknown", "linux": "unknown", "macos": "unknown"},
        )
        self.assertTrue(report["summary"]["test_ci_unknown"])
        rule_ids = {item["rule_id"] for item in report["findings"]}
        self.assertIn("CG906", rule_ids)
        self.assertNotIn("CG901", rule_ids)
        self.assertIn("MATRIX NOT ANALYZED", render_html(report))
        from crossgrid.cli import _text

        text_report = _text(report)
        self.assertIn("coverage unknown; matrix not analyzed", text_report)
        self.assertNotIn("no test runner found", text_report)

        from crossgrid.cli import run

        json_path = self.root / "matrix-report.json"
        cli_output = StringIO()
        with redirect_stdout(cli_output):
            self.assertEqual(
                run(
                    Namespace(
                        root=str(self.root),
                        platform=None,
                        html=None,
                        json_path=str(json_path),
                        sarif=None,
                        fail_on="none",
                        overwrite=False,
                    )
                ),
                0,
            )
        json_report = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(
            {row["test_ci_status"] for row in json_report["platforms"]}, {"unknown"}
        )
        json_rule_ids = {item["rule_id"] for item in json_report["findings"]}
        self.assertIn("CG906", json_rule_ids)
        self.assertNotIn("CG901", json_rule_ids)
        self.assertIn("coverage unknown; matrix not analyzed", cli_output.getvalue())

    def test_unrecognized_matrix_runner_labels_remain_unknown(self) -> None:
        self.write(
            "pyproject.toml",
            """
[project]
name = "fixture"
classifiers = [
  "Operating System :: Microsoft :: Windows",
  "Operating System :: POSIX :: Linux",
  "Operating System :: MacOS",
]
""",
        )
        self.write(
            ".github/workflows/tests.yml",
            """
jobs:
  tests:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [self-hosted, custom-runner]
    steps:
      - run: python -m unittest
""",
        )

        report = scan_project(self.root)
        self.assertTrue(report["summary"]["test_ci_unknown"])
        self.assertEqual(
            {row["test_ci_status"] for row in report["platforms"]}, {"unknown"}
        )
        rule_ids = {item["rule_id"] for item in report["findings"]}
        self.assertIn("CG906", rule_ids)
        self.assertNotIn("CG901", rule_ids)

    def test_derived_matrix_expression_is_left_unclassified(self) -> None:
        self.write(
            ".github/workflows/tests.yml",
            """
jobs:
  tests:
    runs-on: ${{ matrix.os == 'windows-latest' && 'ubuntu-latest' || 'macos-latest' }}
    strategy:
      matrix:
        os: [windows-latest]
    steps:
      - run: pytest
""",
        )
        report = scan_project(
            self.root, supported_override=["linux", "windows", "macos"]
        )
        self.assertEqual(report["summary"]["test_ci_platforms"], [])

    def test_generic_posix_classifier_does_not_infer_linux_and_macos(self) -> None:
        self.write(
            "pyproject.toml",
            '[project]\nname="fixture"\nclassifiers=["Operating System :: POSIX"]\n',
        )
        report = scan_project(self.root)
        self.assertEqual(report["summary"]["declared_platforms"], [])
        self.assertEqual(report["platforms"], [])

    def test_reads_multiline_setuptools_setup_cfg_classifiers(self) -> None:
        self.write(
            "setup.cfg",
            """
[metadata]
classifiers =
    Operating System :: Microsoft :: Windows
    Operating System :: POSIX :: Linux
    Programming Language :: Python :: 3
""",
        )
        report = scan_project(self.root)
        self.assertEqual(report["summary"]["declared_platforms"], ["linux", "windows"])

    def test_reads_comma_list_legacy_classifier_alias_and_interpolation(self) -> None:
        self.write(
            "setup.cfg",
            """
[metadata]
windows_classifier = Operating System :: Microsoft :: Windows
classifier = %(windows_classifier)s, Operating System :: MacOS
""",
        )
        report = scan_project(self.root)
        self.assertEqual(report["summary"]["declared_platforms"], ["macos", "windows"])

    def test_setup_cfg_interpolation_output_is_bounded(self) -> None:
        self.write(
            "setup.cfg",
            """
[metadata]
base = 0123456789012345678901234567890123456789
amplified = %(base)s%(base)s
classifiers = %(amplified)s
""",
        )
        with patch("crossgrid.scanner.MAX_SETUP_CFG_EXPANDED_BYTES", 64):
            report = scan_project(self.root)
        self.assertEqual(report["summary"]["declared_platforms"], [])
        self.assertTrue(
            any(
                finding["rule_id"] == "CG902" and finding["file"] == "setup.cfg"
                for finding in report["findings"]
            )
        )

    def test_setup_cfg_empty_interpolation_work_is_bounded(self) -> None:
        empty_references = "".join("%(empty)s" for _ in range(16))
        nested_references = "".join("%(level1)s" for _ in range(16))
        self.write(
            "setup.cfg",
            f"""
[metadata]
empty =
level1 = {empty_references}
level2 = {nested_references}
classifiers = %(level2)s
""",
        )
        with patch("crossgrid.scanner.MAX_SETUP_CFG_INTERPOLATION_REFERENCES", 20):
            report = scan_project(self.root)
        self.assertEqual(report["summary"]["declared_platforms"], [])
        self.assertTrue(
            any(
                finding["rule_id"] == "CG902" and finding["file"] == "setup.cfg"
                for finding in report["findings"]
            )
        )

    def test_setup_cfg_interpolation_text_scan_is_bounded(self) -> None:
        long_key = "k" * 120
        classifier_refs = "".join("%(leaf)s" for _ in range(5))
        self.write(
            "setup.cfg",
            f"""
[metadata]
{long_key} =
leaf = %({long_key})s
classifiers = {classifier_refs}
""",
        )
        with patch("crossgrid.scanner.MAX_SETUP_CFG_INTERPOLATION_SCAN_CHARS", 256):
            report = scan_project(self.root)
        self.assertEqual(report["summary"]["declared_platforms"], [])
        self.assertTrue(
            any(
                finding["rule_id"] == "CG902" and finding["file"] == "setup.cfg"
                for finding in report["findings"]
            )
        )

    def test_reads_bounded_in_root_setup_cfg_classifier_file(self) -> None:
        self.write(
            "setup.cfg",
            "[metadata]\nclassifiers = file: windows.txt, linux.txt\n",
        )
        self.write("windows.txt", "Operating System :: Microsoft :: Windows\n")
        self.write("linux.txt", "Operating System :: POSIX :: Linux\n")
        report = scan_project(self.root)
        self.assertEqual(report["summary"]["declared_platforms"], ["linux", "windows"])

    def test_skips_symlinked_setup_cfg_classifier_file(self) -> None:
        self.write(
            "setup.cfg",
            "[metadata]\nclassifiers = file: classifiers.txt\n",
        )
        self.write("classifiers.txt", "Operating System :: Microsoft :: Windows\n")
        source = self.root / "classifiers.txt"
        original = Path.is_symlink
        with patch.object(
            Path, "is_symlink", lambda path: path == source or original(path)
        ):
            report = scan_project(self.root)
        self.assertEqual(report["summary"]["declared_platforms"], [])
        self.assertTrue(
            any(
                finding["rule_id"] == "CG902" and finding["file"] == "setup.cfg"
                for finding in report["findings"]
            )
        )

    def test_setup_cfg_classifier_file_is_an_output_protected_input(self) -> None:
        from crossgrid.cli import run

        self.write(
            "setup.cfg",
            "[metadata]\nclassifiers = file: classifiers.txt\n",
        )
        content = "Operating System :: Microsoft :: Windows\n"
        self.write("classifiers.txt", content)
        source = self.root / "classifiers.txt"
        args = Namespace(
            root=str(self.root),
            platform=[],
            html=None,
            json_path=str(source),
            sarif=None,
            overwrite=True,
            fail_on="none",
        )
        errors = StringIO()
        with redirect_stderr(errors):
            code = run(args)
        self.assertEqual(code, 2)
        self.assertIn("scan input", errors.getvalue())
        self.assertEqual(source.read_text(encoding="utf-8"), content)

    def test_missing_setup_cfg_classifier_file_is_an_output_protected_input(
        self,
    ) -> None:
        from crossgrid.cli import run

        self.write(
            "setup.cfg",
            "[metadata]\nclassifiers = file: classifiers.txt\n",
        )
        source = self.root / "classifiers.txt"
        args = Namespace(
            root=str(self.root),
            platform=[],
            html=None,
            json_path=str(source),
            sarif=None,
            overwrite=True,
            fail_on="none",
        )
        errors = StringIO()
        with redirect_stderr(errors):
            code = run(args)
        self.assertEqual(code, 2)
        self.assertIn("scan input", errors.getvalue())
        self.assertFalse(source.exists())

    def test_refuses_non_regular_setup_cfg_classifier_file(self) -> None:
        self.write(
            "setup.cfg",
            "[metadata]\nclassifiers = file: classifiers.txt\n",
        )
        source = self.root / "classifiers.txt"
        self.write("classifiers.txt", "Operating System :: Microsoft :: Windows\n")
        original = Path.is_file
        with patch.object(
            Path, "is_file", lambda path: False if path == source else original(path)
        ):
            report = scan_project(self.root)
        self.assertEqual(report["summary"]["declared_platforms"], [])
        self.assertTrue(
            any(
                finding["rule_id"] == "CG902" and finding["file"] == "setup.cfg"
                for finding in report["findings"]
            )
        )

    def test_setup_cfg_classifier_file_outside_root_is_unresolved(self) -> None:
        self.write(
            "setup.cfg",
            "[metadata]\nclassifiers = file: ../classifiers.txt\n",
        )
        report = scan_project(self.root)
        self.assertTrue(
            any(
                finding["rule_id"] == "CG902" and finding["file"] == "setup.cfg"
                for finding in report["findings"]
            )
        )

    def test_static_pyproject_classifiers_take_precedence_over_setup_cfg(self) -> None:
        self.write(
            "pyproject.toml",
            '[project]\nname="fixture"\nclassifiers=[]\n',
        )
        self.write(
            "setup.cfg",
            "[metadata]\nclassifiers =\n    Operating System :: Microsoft :: Windows\n",
        )
        report = scan_project(self.root)
        self.assertEqual(report["summary"]["declared_platforms"], [])

    def test_poetry_classifiers_take_precedence_over_setup_cfg(self) -> None:
        self.write(
            "pyproject.toml",
            '[tool.poetry]\nname="fixture"\nclassifiers=["Operating System :: MacOS"]\n',
        )
        self.write(
            "setup.cfg",
            "[metadata]\nclassifiers =\n    Operating System :: Microsoft :: Windows\n",
        )
        report = scan_project(self.root)
        self.assertEqual(report["summary"]["declared_platforms"], ["macos"])

    def test_dynamic_pyproject_classifiers_are_not_read_from_setup_cfg(self) -> None:
        self.write(
            "pyproject.toml",
            '[project]\nname="fixture"\ndynamic=["classifiers"]\n',
        )
        self.write(
            "setup.cfg",
            "[metadata]\nclassifiers =\n    Operating System :: Microsoft :: Windows\n",
        )
        report = scan_project(self.root)
        self.assertEqual(report["summary"]["declared_platforms"], [])
        self.assertTrue(
            any(
                finding["rule_id"] == "CG902"
                and finding["subject"] == "dynamic classifiers"
                for finding in report["findings"]
            )
        )

    def test_malformed_and_oversized_setup_cfg_are_reported(self) -> None:
        oversized = "x" * (MAX_METADATA_BYTES + 1)
        for content in (
            "[metadata\nclassifiers = Operating System :: Microsoft :: Windows\n",
            oversized,
        ):
            with self.subTest(size=len(content)):
                self.write("setup.cfg", content)
                report = scan_project(self.root)
                self.assertTrue(
                    any(
                        finding["rule_id"] == "CG902" and finding["file"] == "setup.cfg"
                        for finding in report["findings"]
                    )
                )

    def test_skips_symlinked_setup_cfg(self) -> None:
        setup_cfg = self.root / "setup.cfg"
        self.write(
            "setup.cfg",
            "[metadata]\nclassifiers =\n    Operating System :: Microsoft :: Windows\n",
        )
        original = Path.is_symlink
        with patch.object(
            Path, "is_symlink", lambda path: path == setup_cfg or original(path)
        ):
            report = scan_project(self.root)
        self.assertEqual(report["summary"]["declared_platforms"], [])
        self.assertTrue(
            any(
                finding["rule_id"] == "CG902" and finding["file"] == "setup.cfg"
                for finding in report["findings"]
            )
        )

    def test_skips_out_of_root_setup_cfg(self) -> None:
        setup_cfg = self.root / "setup.cfg"
        self.write(
            "setup.cfg",
            "[metadata]\nclassifiers =\n    Operating System :: Microsoft :: Windows\n",
        )
        original_resolve = Path.resolve
        outside = self.root.parent / "outside-setup.cfg"
        with patch.object(
            Path,
            "resolve",
            lambda path, strict=False: (
                outside if path == setup_cfg else original_resolve(path, strict=strict)
            ),
        ):
            report = scan_project(self.root)
        self.assertEqual(report["summary"]["declared_platforms"], [])
        self.assertTrue(
            any(
                finding["rule_id"] == "CG902" and finding["file"] == "setup.cfg"
                for finding in report["findings"]
            )
        )

    def test_valid_toml_with_non_table_project_or_tool_is_reported_not_crashed(
        self,
    ) -> None:
        for content in ('project = "fixture"\n', 'tool = "fixture"\n'):
            with self.subTest(content=content):
                self.write("pyproject.toml", content)
                report = scan_project(self.root)
                metadata = [
                    item
                    for item in report["findings"]
                    if item["rule_id"] == "CG902" and item["file"] == "pyproject.toml"
                ]
                self.assertTrue(metadata)

    def test_pathlib_open_mode_and_archive_open_are_classified_by_api(self) -> None:
        self.write(
            "source.py",
            """
from pathlib import Path
from zipfile import ZipFile

Path("binary.bin").open("rb")
Path("text.txt").open("r")
Path("explicit.txt").open("r", -1, "utf-8")
Path.open(Path("unbound.txt"), "r", -1, "utf-8")
open("explicit-builtins.txt", "r", -1, "utf-8")
handle = Path("variable.txt")
handle.open("rb")
handle.open()
ZipFile("archive.zip").open("member.bin")
""",
        )
        report = scan_project(self.root)
        findings = [item for item in report["findings"] if item["rule_id"] == "CG201"]
        self.assertEqual(len(findings), 2)
        self.assertEqual({item["subject"] for item in findings}, {"pathlib.Path.open"})

    def test_path_variable_inference_is_disabled_for_shadowed_or_reassigned_names(
        self,
    ) -> None:
        self.write(
            "source.py",
            """
from pathlib import Path

def build_path():
    item = Path("safe.txt")
    item.open()

def custom_reader(item):
    item.open()

item = Path("safe.txt")
item = object()
item.open()
""",
        )
        report = scan_project(self.root)
        self.assertFalse(any(item["rule_id"] == "CG201" for item in report["findings"]))

    def test_pattern_capture_bindings_disable_path_variable_inference(self) -> None:
        self.write(
            "source.py",
            """
from pathlib import Path

as_path = Path("one.txt")
star_path = Path("two.txt")
rest_path = Path("three.txt")
match value:
    case as_path:
        pass
    case [*star_path]:
        pass
    case {"value": _, **rest_path}:
        pass
as_path.open()
star_path.open()
rest_path.open()
""",
        )
        report = scan_project(self.root)
        self.assertFalse(any(item["rule_id"] == "CG201" for item in report["findings"]))

    def test_pep263_source_encoding_is_parsed_and_ast_columns_are_character_based(
        self,
    ) -> None:
        source = '# coding: latin-1\ncafé = 1; open("data.txt")\n'
        (self.root / "encoded.py").write_bytes(source.encode("latin-1"))
        report = scan_project(self.root)
        finding = next(
            item for item in report["findings"] if item["rule_id"] == "CG201"
        )
        self.assertEqual(finding["column"], source.splitlines()[1].index("open") + 1)

    def test_skip_reason_is_not_mistaken_for_the_platform_condition(self) -> None:
        self.write(
            "tests/test_skip.py",
            """
import os
import pytest

@pytest.mark.skipif(os.getenv("CI"), reason="Windows runner unavailable")
def test_ci_only():
    pass
""",
        )
        report = scan_project(self.root, supported_override=["windows"])
        self.assertFalse(any(item["rule_id"] == "CG801" for item in report["findings"]))

    def test_keyword_skip_condition_and_underscored_test_job_are_recognized(
        self,
    ) -> None:
        self.write(
            ".github/workflows/tests.yml",
            """
jobs:
  unit_tests:
    runs-on: windows-latest
    steps:
      - run: make check
""",
        )
        self.write(
            "tests/test_skip.py",
            """
import pytest

@pytest.mark.skipif(condition="sys.platform == 'win32'", reason="Windows-specific reason")
def test_something():
    pass
""",
        )
        report = scan_project(self.root, supported_override=["windows"])
        self.assertEqual(report["summary"]["test_ci_platforms"], ["windows"])
        self.assertEqual(report["platforms"][0]["platform_skip_count"], 1)

    def test_ast_detects_platform_api_import_encoding_and_shell_with_guard_evidence(
        self,
    ) -> None:
        self.write(
            "pyproject.toml",
            '[project]\nname="fixture"\nclassifiers=["Operating System :: Microsoft :: Windows"]\n',
        )
        self.write(
            ".github/workflows/tests.yaml",
            "jobs:\n  tests:\n    runs-on: windows-latest\n    steps:\n      - run: pytest\n",
        )
        self.write(
            "src/app.py",
            """
import os as operating
import pwd
import subprocess
import signal

def identity():
    return operating.getuid()

def guarded():
    if __import__("sys").platform == "win32":
        return operating.uname()
    return "else"

def files():
    open("settings.txt")
    open("payload.bin", "rb")
    open("payload-keyword.bin", mode="rb")
    open("default-encoding.txt", encoding=None)
    signal.SIGKILL

def shell():
    subprocess.run("echo hi", shell=True)
""",
        )
        report = scan_project(self.root)
        ids = [item["rule_id"] for item in report["findings"]]
        self.assertIn("CG101", ids)
        self.assertIn("CG102", ids)
        self.assertIn("CG201", ids)
        self.assertIn("CG301", ids)
        self.assertEqual(
            ids.count("CG201"),
            2,
            "binary opens should not need an encoding; encoding=None still uses the platform default",
        )
        self.assertIn(
            "signal.SIGKILL", [item["subject"] for item in report["findings"]]
        )
        unguarded = next(
            item
            for item in report["findings"]
            if item["rule_id"] == "CG102" and item["subject"] == "os.getuid"
        )
        self.assertEqual(unguarded["confidence"], "high")

    def test_importerror_and_platform_guards_are_marked_as_review_not_unprotected(
        self,
    ) -> None:
        self.write(
            "src/guarded.py",
            """
import sys
if sys.platform == "win32":
    import fcntl
try:
    import pwd
except ImportError:
    pwd = None
""",
        )
        report = scan_project(self.root, supported_override=["windows"])
        rows = [item for item in report["findings"] if item["rule_id"] == "CG101"]
        self.assertEqual(
            {item["subject"]: item["confidence"] for item in rows},
            {"fcntl": "review", "pwd": "review"},
        )

    def test_detects_current_windows_registry_module_import_forms(self) -> None:
        self.write(
            "src/windows_registry.py", "import winreg\nfrom winreg import OpenKey\n"
        )
        report = scan_project(self.root, supported_override=["windows"])
        rows = [item for item in report["findings"] if item["rule_id"] == "CG101"]
        self.assertEqual([item["subject"] for item in rows], ["winreg", "winreg"])
        self.assertEqual([item["line"] for item in rows], [1, 2])

    def test_bad_syntax_and_bad_workflow_are_reported_without_stopping_scan(
        self,
    ) -> None:
        self.write(".github/workflows/broken.yml", "jobs: [this is not a mapping")
        self.write("broken.py", "def unfinished(:\n")
        report = scan_project(self.root, supported_override=["linux"])
        ids = {item["rule_id"] for item in report["findings"]}
        self.assertIn("CG903", ids)
        self.assertIn("CG905", ids)
        self.assertEqual(report["summary"]["python_files"], 1)

    def test_deep_yaml_and_python_inputs_are_reported_without_tracebacks(self) -> None:
        self.write(".github/workflows/deep.yml", "jobs: {}\n")
        self.write("deep.py", "value = True\n")
        with (
            patch("crossgrid.scanner.yaml.safe_load", side_effect=RecursionError),
            patch("crossgrid.scanner.ast.parse", side_effect=RecursionError),
        ):
            report = scan_project(self.root)
        findings = {(item["rule_id"], item["file"]) for item in report["findings"]}
        self.assertIn(("CG903", ".github/workflows/deep.yml"), findings)
        self.assertIn(("CG905", "deep.py"), findings)

    def test_python_scan_enforces_file_and_total_byte_limits(self) -> None:
        from crossgrid import scanner

        self.write("a.py", "a=1\n")
        self.write("b.py", "b=2\n")
        with patch.object(scanner, "MAX_PYTHON_FILES", 1):
            file_limited = scan_project(self.root)
        self.assertEqual(file_limited["summary"]["python_files"], 1)
        self.assertTrue(
            any(
                item["subject"] == "source file limit"
                for item in file_limited["findings"]
            )
        )

        with patch.object(scanner, "MAX_TOTAL_SOURCE_BYTES", 4):
            byte_limited = scan_project(self.root)
        self.assertTrue(
            any(
                item["subject"] == "total source size limit"
                for item in byte_limited["findings"]
            )
        )

    def test_oversized_source_is_reported_without_reading_the_whole_file(self) -> None:
        from crossgrid import scanner

        self.write("large.py", "import os\n")
        source = self.root / "large.py"
        with patch.object(scanner, "MAX_PYTHON_SOURCE_BYTES", 4):
            report = scan_project(self.root)
        self.assertTrue(
            any(
                item["rule_id"] == "CG904" and item["subject"] == "source size limit"
                for item in report["findings"]
            )
        )
        self.assertEqual(source.read_text(encoding="utf-8"), "import os\n")

    def test_workflow_scan_enforces_file_and_aggregate_size_limits(self) -> None:
        from crossgrid import scanner

        self.write(".github/workflows/a.yml", "jobs: {}\n")
        self.write(".github/workflows/b.yaml", "jobs: {}\n")
        with patch.object(scanner, "MAX_WORKFLOW_FILES", 1):
            file_limited = scan_project(self.root)
        self.assertTrue(
            any(
                item["subject"] == "workflow file limit"
                for item in file_limited["findings"]
            )
        )

        with patch.object(scanner, "MAX_TOTAL_WORKFLOW_BYTES", len("jobs: {}\n")):
            byte_limited = scan_project(self.root)
        self.assertTrue(
            any(
                item["subject"] == "total workflow size limit"
                for item in byte_limited["findings"]
            )
        )

    def test_git_pointer_marker_is_bounded(self) -> None:
        from crossgrid.cli import run
        from crossgrid import scanner

        marker = self.root / ".git"
        marker.write_text("x" * (scanner.MAX_GIT_MARKER_BYTES + 1), encoding="utf-8")
        output = self.root.parent / "marker-check.json"
        args = Namespace(
            root=str(self.root),
            platform=None,
            html=None,
            json_path=str(output),
            sarif=None,
            fail_on="error",
        )
        errors = StringIO()
        with redirect_stderr(errors):
            self.assertEqual(run(args), 2)
        self.assertIn("size limit", errors.getvalue())
        self.assertFalse(output.exists())

    def test_skips_python_symlinks_and_does_not_read_outside_scan_root(self) -> None:
        symlink_candidate = self.root / "linked.py"
        escape_candidate = self.root / "escaped.py"
        self.write("linked.py", "import fcntl\n")
        self.write("escaped.py", "import pwd\n")
        original_is_symlink = Path.is_symlink
        original_resolve = Path.resolve

        def fake_is_symlink(path: Path) -> bool:
            return path == symlink_candidate or original_is_symlink(path)

        def fake_resolve(path: Path, strict: bool = False) -> Path:
            if path == escape_candidate:
                return self.root.parent / "outside-project.py"
            return original_resolve(path, strict=strict)

        with (
            patch.object(Path, "is_symlink", fake_is_symlink),
            patch.object(Path, "resolve", fake_resolve),
        ):
            report = scan_project(self.root)

        self.assertEqual(report["summary"]["python_files"], 0)
        skipped = [item for item in report["findings"] if item["rule_id"] == "CG904"]
        self.assertEqual(
            {item["file"] for item in skipped}, {"linked.py", "escaped.py"}
        )
        self.assertFalse(
            any(item["subject"] in {"fcntl", "pwd"} for item in report["findings"])
        )

    def test_skips_symlinked_project_metadata(self) -> None:
        metadata = self.root / "pyproject.toml"
        self.write(
            "pyproject.toml",
            '[project]\nname="linked"\nclassifiers=["Operating System :: Microsoft :: Windows"]\n',
        )
        original = Path.is_symlink
        with patch.object(
            Path, "is_symlink", lambda path: path == metadata or original(path)
        ):
            report = scan_project(self.root)
        self.assertEqual(report["summary"]["declared_platforms"], [])
        self.assertIn("CG902", {item["rule_id"] for item in report["findings"]})

    def test_skips_symlinked_workflow_directory(self) -> None:
        self.write(
            ".github/workflows/tests.yml",
            "jobs:\n  tests:\n    runs-on: windows-latest\n    steps:\n      - run: pytest\n",
        )
        workflow_dir = self.root / ".github" / "workflows"
        original = Path.is_symlink
        with patch.object(
            Path, "is_symlink", lambda path: path == workflow_dir or original(path)
        ):
            report = scan_project(self.root, supported_override=["windows"])
        self.assertEqual(report["summary"]["test_ci_platforms"], [])
        self.assertIn("CG903", {item["rule_id"] for item in report["findings"]})

    def test_skips_symlinked_workflow_file(self) -> None:
        workflow = self.root / ".github" / "workflows" / "tests.yml"
        self.write(
            ".github/workflows/tests.yml",
            "jobs:\n  tests:\n    runs-on: windows-latest\n    steps:\n      - run: pytest\n",
        )
        original = Path.is_symlink
        with patch.object(
            Path, "is_symlink", lambda path: path == workflow or original(path)
        ):
            report = scan_project(self.root, supported_override=["windows"])
        self.assertEqual(report["summary"]["test_ci_platforms"], [])
        self.assertTrue(
            any(
                item["rule_id"] == "CG903" and item["file"].endswith("tests.yml")
                for item in report["findings"]
            )
        )

    def test_skips_out_of_root_metadata_workflow_directory_and_workflow_file(
        self,
    ) -> None:
        original_resolve = Path.resolve
        outside = self.root.parent / "outside-project-input"

        metadata_root = self.root / "metadata-case"
        metadata_root.mkdir()
        metadata = metadata_root / "pyproject.toml"
        metadata.write_text(
            '[project]\nname="external"\nclassifiers=["Operating System :: Microsoft :: Windows"]\n',
            encoding="utf-8",
        )
        with patch.object(
            Path,
            "resolve",
            lambda path, strict=False: (
                outside if path == metadata else original_resolve(path, strict=strict)
            ),
        ):
            metadata_report = scan_project(metadata_root)
        self.assertEqual(metadata_report["summary"]["declared_platforms"], [])
        self.assertIn(
            "CG902", {item["rule_id"] for item in metadata_report["findings"]}
        )

        directory_root = self.root / "workflow-directory-case"
        workflow_dir = directory_root / ".github" / "workflows"
        workflow_dir.mkdir(parents=True)
        (workflow_dir / "tests.yml").write_text(
            "jobs:\n  tests:\n    runs-on: windows-latest\n    steps:\n      - run: pytest\n",
            encoding="utf-8",
        )
        with patch.object(
            Path,
            "resolve",
            lambda path, strict=False: (
                outside
                if path == workflow_dir
                else original_resolve(path, strict=strict)
            ),
        ):
            directory_report = scan_project(
                directory_root, supported_override=["windows"]
            )
        self.assertEqual(directory_report["summary"]["test_ci_platforms"], [])
        self.assertIn(
            "CG903", {item["rule_id"] for item in directory_report["findings"]}
        )

        file_root = self.root / "workflow-file-case"
        workflow_file = file_root / ".github" / "workflows" / "tests.yml"
        workflow_file.parent.mkdir(parents=True)
        workflow_file.write_text(
            "jobs:\n  tests:\n    runs-on: windows-latest\n    steps:\n      - run: pytest\n",
            encoding="utf-8",
        )
        with patch.object(
            Path,
            "resolve",
            lambda path, strict=False: (
                outside
                if path == workflow_file
                else original_resolve(path, strict=strict)
            ),
        ):
            file_report = scan_project(file_root, supported_override=["windows"])
        self.assertEqual(file_report["summary"]["test_ci_platforms"], [])
        self.assertIn("CG903", {item["rule_id"] for item in file_report["findings"]})

    def test_empty_project_and_cli_override_make_scope_explicit(self) -> None:
        report = scan_project(self.root, supported_override=["macos"])
        self.assertEqual(report["support_scope"], ["macos"])
        self.assertEqual(report["platforms"][0]["platform"], "macos")
        self.assertIn("CG900", {item["rule_id"] for item in report["findings"]})
        self.assertEqual(report_exit_code(report, "none"), 0)
        self.assertEqual(report_exit_code(report, "warning"), 1)

    def test_report_outputs_are_escaped_and_sarif_has_locations(self) -> None:
        self.write("source-at-risk.py", 'def danger():\n    open("x")\n')
        report = scan_project(self.root)
        next(item for item in report["findings"] if item["rule_id"] == "CG201")[
            "file"
        ] = "<source>.py"
        html = render_html(report)
        self.assertIn("&lt;source&gt;.py", html)
        self.assertNotIn("<source>.py", html)
        sarif = render_sarif(report)
        self.assertTrue(sarif["runs"][0]["results"])
        result = next(
            row for row in sarif["runs"][0]["results"] if row["ruleId"] == "CG201"
        )
        self.assertEqual(
            result["locations"][0]["physicalLocation"]["region"]["startLine"], 2
        )

    def test_cli_writes_html_json_sarif_and_applies_exit_threshold(self) -> None:
        from crossgrid.cli import run

        self.write(
            "pyproject.toml",
            '[project]\nname="fixture"\nclassifiers=["Operating System :: Microsoft :: Windows"]\n',
        )
        self.write(
            "src/app.py", 'def read_config():\n    return open("settings.ini")\n'
        )
        html_path = self.root / "report.html"
        json_path = self.root / "report.json"
        sarif_path = self.root / "report.sarif"
        output = StringIO()
        with redirect_stdout(output):
            code = run(
                Namespace(
                    root=str(self.root),
                    platform=None,
                    html=str(html_path),
                    json_path=str(json_path),
                    sarif=str(sarif_path),
                    fail_on="warning",
                )
            )

        self.assertEqual(code, 1)
        self.assertIn("CrossGrid:", output.getvalue())
        findings = json.loads(json_path.read_text(encoding="utf-8"))["findings"]
        self.assertTrue(any(item["rule_id"] == "CG201" for item in findings))
        sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
        self.assertEqual(sarif["version"], "2.1.0")
        self.assertEqual(sarif["runs"][0]["tool"]["driver"]["version"], __version__)
        self.assertIn("CG201", {item["ruleId"] for item in sarif["runs"][0]["results"]})
        html = html_path.read_text(encoding="utf-8")
        self.assertIn("CrossGrid", html)
        self.assertIn("prefers-color-scheme:dark", html)

    def test_cli_rejects_report_outputs_that_alias_inputs_or_each_other(self) -> None:
        from crossgrid.cli import run

        metadata = self.root / "pyproject.toml"
        original_metadata = '[project]\nname="fixture"\n'
        self.write("pyproject.toml", original_metadata)
        args = Namespace(
            root=str(self.root),
            platform=None,
            html=None,
            json_path=str(metadata),
            sarif=None,
            fail_on="error",
        )
        errors = StringIO()
        with redirect_stderr(errors):
            code = run(args)
        self.assertEqual(code, 2)
        self.assertIn("scan input", errors.getvalue())
        self.assertEqual(metadata.read_text(encoding="utf-8"), original_metadata)

        setup_cfg = self.root / "setup.cfg"
        original_setup_cfg = (
            "[metadata]\nclassifiers =\n    Operating System :: Microsoft :: Windows\n"
        )
        self.write("setup.cfg", original_setup_cfg)
        args.json_path = str(setup_cfg)
        args.sarif = None
        args.overwrite = True
        errors = StringIO()
        with redirect_stderr(errors):
            code = run(args)
        self.assertEqual(code, 2)
        self.assertIn("scan input", errors.getvalue())
        self.assertEqual(setup_cfg.read_text(encoding="utf-8"), original_setup_cfg)

        report = self.root / "same-report.json"
        args.json_path = str(report)
        args.sarif = str(report)
        with redirect_stderr(StringIO()):
            code = run(args)
        self.assertEqual(code, 2)
        self.assertFalse(report.exists())

    def test_cli_requires_explicit_overwrite_without_bypassing_input_guard(
        self,
    ) -> None:
        from crossgrid.cli import run

        metadata = self.root / "pyproject.toml"
        original_metadata = '[project]\nname="fixture"\n'
        self.write("pyproject.toml", original_metadata)
        self.write("src/app.py", "def main():\n    return None\n")
        report = self.root / "report.json"
        report.write_text("preserve this file", encoding="utf-8")
        args = Namespace(
            root=str(self.root),
            platform=None,
            html=None,
            json_path=str(report),
            sarif=None,
            fail_on="error",
            overwrite=False,
        )

        errors = StringIO()
        with redirect_stderr(errors):
            self.assertEqual(run(args), 2)
        self.assertIn("already exists", errors.getvalue())
        self.assertEqual(report.read_text(encoding="utf-8"), "preserve this file")

        args.overwrite = True
        with redirect_stderr(StringIO()):
            self.assertEqual(run(args), 0)
        self.assertEqual(
            json.loads(report.read_text(encoding="utf-8"))["tool"], "CrossGrid"
        )

        args.json_path = str(metadata)
        errors = StringIO()
        with redirect_stderr(errors):
            self.assertEqual(run(args), 2)
        self.assertIn("scan input", errors.getvalue())
        self.assertEqual(metadata.read_text(encoding="utf-8"), original_metadata)

    def test_report_write_does_not_clobber_a_file_created_during_write(self) -> None:
        from crossgrid.scanner import _write_report

        destination = self.root / "concurrent-report.json"

        def concurrent_create(_temporary: str, target: str) -> None:
            Path(target).write_text("concurrent writer", encoding="utf-8")
            raise FileExistsError("created concurrently")

        with (
            patch("crossgrid.scanner.os.link", side_effect=concurrent_create),
            self.assertRaisesRegex(ValueError, "already exists.*--overwrite"),
        ):
            _write_report(destination, '{"new": true}\n')

        self.assertEqual(destination.read_text(encoding="utf-8"), "concurrent writer")

    def test_report_write_uses_exclusive_fallback_without_hard_links(self) -> None:
        from crossgrid.scanner import _write_report

        destination = self.root / "exclusive-report.json"
        with patch("crossgrid.scanner.os.link", side_effect=OSError("unsupported")):
            _write_report(destination, '{"fallback": true}\n')
        self.assertEqual(
            json.loads(destination.read_text(encoding="utf-8")), {"fallback": True}
        )

    def test_report_fallback_preserves_a_target_created_after_preflight(self) -> None:
        from crossgrid.scanner import _write_report

        destination = self.root / "fallback-race.json"

        def publish_existing_report_then_disable_links(
            _temporary: str, target: str
        ) -> None:
            Path(target).write_text("report path already claimed", encoding="utf-8")
            raise OSError("hard links unavailable")

        with (
            patch(
                "crossgrid.scanner.os.link",
                side_effect=publish_existing_report_then_disable_links,
            ),
            self.assertRaisesRegex(ValueError, "already exists.*--overwrite"),
        ):
            _write_report(destination, '{"new": true}\n')

        self.assertEqual(
            destination.read_text(encoding="utf-8"), "report path already claimed"
        )

    def test_cli_rejects_git_metadata_and_linked_worktree_outputs(self) -> None:
        from crossgrid.cli import run

        git_metadata = self.root / ".git"
        git_metadata.mkdir()
        target = git_metadata / "refs" / "tags" / "v1.0.0"
        target.parent.mkdir(parents=True)
        target.write_text("original ref\n", encoding="utf-8")
        args = Namespace(
            root=str(self.root),
            platform=None,
            html=str(target),
            json_path=None,
            sarif=None,
            fail_on="error",
        )
        with redirect_stderr(StringIO()):
            self.assertEqual(run(args), 2)
        self.assertEqual(target.read_text(encoding="utf-8"), "original ref\n")

        linked_root = self.root / "linked-project"
        linked_root.mkdir()
        external_admin = self.root / "linked-admin" / "worktrees" / "active"
        external_admin.mkdir(parents=True)
        (external_admin / "gitdir").write_text("../worktree/.git\n", encoding="utf-8")
        common_dir = self.root / "linked-admin"
        (external_admin / "commondir").write_text("../..\n", encoding="utf-8")
        (linked_root / ".git").write_text(
            f"gitdir: {external_admin}\n", encoding="utf-8"
        )
        external_ref = external_admin / "refs" / "tags" / "v1.0.0"
        external_ref.parent.mkdir(parents=True)
        external_ref.write_text("original linked ref\n", encoding="utf-8")
        args.root = str(linked_root)
        args.html = str(external_ref)
        with redirect_stderr(StringIO()):
            self.assertEqual(run(args), 2)
        self.assertEqual(
            external_ref.read_text(encoding="utf-8"), "original linked ref\n"
        )
        self.assertTrue(common_dir.exists())

    def test_cli_rejects_output_under_symlinked_parent(self) -> None:
        from crossgrid.cli import run

        output_parent = self.root / "reports"
        output_parent.mkdir()
        output = output_parent / "report.json"
        original = Path.is_symlink
        with patch.object(
            Path,
            "is_symlink",
            lambda path: path == output_parent or original(path),
        ):
            args = Namespace(
                root=str(self.root),
                platform=None,
                html=None,
                json_path=str(output),
                sarif=None,
                fail_on="error",
            )
            errors = StringIO()
            with redirect_stderr(errors):
                self.assertEqual(run(args), 2)
        self.assertIn("symlink or reparse point", errors.getvalue())
        self.assertFalse(output.exists())

    def test_cli_rejects_output_under_junctioned_parent(self) -> None:
        from crossgrid.cli import run

        output_parent = self.root / "junction-reports"
        output_parent.mkdir()
        output = output_parent / "report.json"
        is_junction = getattr(Path, "is_junction", None)

        def fake_is_junction(path: Path) -> bool:
            return path == output_parent or bool(is_junction and is_junction(path))

        args = Namespace(
            root=str(self.root),
            platform=None,
            html=None,
            json_path=str(output),
            sarif=None,
            fail_on="error",
        )
        errors = StringIO()
        with (
            patch.object(Path, "is_junction", fake_is_junction, create=True),
            redirect_stderr(errors),
        ):
            self.assertEqual(run(args), 2)
        self.assertIn("symlink or reparse point", errors.getvalue())
        self.assertFalse(output.exists())

    def test_windows_reparse_attribute_is_detected_without_is_junction_api(
        self,
    ) -> None:
        from types import SimpleNamespace

        from crossgrid import scanner

        candidate = self.root / "junction-reports"
        original_lstat = Path.lstat

        def fake_lstat(path: Path):
            if path == candidate:
                return SimpleNamespace(st_file_attributes=0x400)
            return original_lstat(path)

        with (
            patch.object(scanner.os, "name", "nt"),
            patch.object(Path, "is_symlink", return_value=False),
            patch.object(Path, "is_junction", return_value=False, create=True),
            patch.object(Path, "lstat", fake_lstat),
        ):
            self.assertTrue(scanner._is_link_or_reparse_point(candidate))

    def test_cli_returns_a_clean_error_when_root_resolution_fails(self) -> None:
        from crossgrid.cli import run

        args = Namespace(
            root=str(self.root),
            platform=None,
            html=None,
            json_path=None,
            sarif=None,
            fail_on="error",
        )
        errors = StringIO()
        with (
            patch.object(Path, "resolve", side_effect=RuntimeError("symlink loop")),
            redirect_stderr(errors),
        ):
            self.assertEqual(run(args), 2)
        self.assertIn("symlink loop", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
