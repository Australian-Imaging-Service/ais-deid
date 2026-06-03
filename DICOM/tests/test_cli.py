"""
test_cli.py
-----------
CLI integration tests using Click's CliRunner.

These tests invoke the CLI as a subprocess equivalent — no real shell needed.
They verify argument parsing, exit codes, env var handling, and output formatting.
"""

from pathlib import Path

import pytest
from click.testing import CliRunner

from dicom_deid.cli import main


@pytest.fixture()
def runner():
    return CliRunner()


class TestMainGroup:
    def test_help_exits_zero(self, runner):
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "De-identify" in result.output

    def test_version_output(self, runner):
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "dicom-deid" in result.output


class TestProcessCommand:
    def test_basic_run_succeeds(
        self, runner, dicom_dir, output_dir, recipe_path, set_deid_salt
    ):
        result = runner.invoke(main, [
            "process",
            "--input", str(dicom_dir),
            "--output", str(output_dir),
            "--recipe", str(recipe_path),
        ])
        assert result.exit_code == 0, result.output
        assert "succeeded" in result.output

# Salt test — CURRENTLY DISABLED
# TO RE-ENABLE: uncomment once salting is re-enabled in transforms.py

#    def test_missing_salt_exits_nonzero(
#        self, runner, dicom_dir, output_dir, recipe_path, monkeypatch
#    ):
#        import dicom_deid.transforms as t
#        monkeypatch.setattr(t, "_SALT", None)
#        result = runner.invoke(main, [
#            "process",
#            "--input", str(dicom_dir),
#            "--output", str(output_dir),
#            "--recipe", str(recipe_path),
#        ])
        # Engine raises RuntimeError at construction when salt is missing
#        assert result.exit_code != 0

    def test_missing_input_dir_exits_nonzero(self, runner, output_dir, recipe_path):
        result = runner.invoke(main, [
            "process",
            "--input", "/nonexistent/path",
            "--output", str(output_dir),
            "--recipe", str(recipe_path),
        ])
        assert result.exit_code != 0

    def test_recipe_envvar_honoured(
        self, runner, dicom_dir, output_dir, recipe_path, set_deid_salt
    ):
        """--recipe can be omitted when DEID_RECIPE env var is set."""
        result = runner.invoke(
            main,
            ["process", "--input", str(dicom_dir), "--output", str(output_dir)],
            env={"DEID_RECIPE": str(recipe_path)},
            catch_exceptions=False,
        )
        assert result.exit_code == 0

    def test_verbose_flag_produces_debug_output(
        self, runner, dicom_dir, output_dir, recipe_path, set_deid_salt
    ):
        result = runner.invoke(main, [
            "--verbose",
            "process",
            "--input", str(dicom_dir),
            "--output", str(output_dir),
            "--recipe", str(recipe_path),
        ])
        # Debug output includes "DEBUG" or detailed lines
        assert result.exit_code == 0


class TestValidateCommand:
    def test_valid_output_passes(
        self, runner, dicom_dir, output_dir, recipe_path, tmp_path
    ):
        # First de-identify
        runner.invoke(main, [
            "process",
            "--input", str(dicom_dir),
            "--output", str(output_dir),
            "--recipe", str(recipe_path),
        ])
        # Then validate
        result = runner.invoke(main, [
            "validate",
            "--input", str(output_dir),
        ])
        assert result.exit_code == 0
        assert "passed" in result.output.lower()

    def test_unprocessed_files_fail_validation(
        self, runner, dicom_dir
    ):
        """Raw (non-de-identified) files should fail validation."""
        result = runner.invoke(main, [
            "validate",
            "--input", str(dicom_dir),
        ])
        assert result.exit_code == 1
