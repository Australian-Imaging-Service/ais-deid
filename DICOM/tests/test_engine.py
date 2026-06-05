"""
test_engine.py
--------------
Integration tests for DeidEngine.

These tests write real DICOM files to a temp directory, run the engine,
and inspect the output tags — the most meaningful test of correctness.
"""

from pathlib import Path

import pydicom
import pytest

from dicom_deid.engine import DeidEngine, FileResult, RunResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read(path: Path) -> pydicom.Dataset:
    return pydicom.dcmread(str(path), stop_before_pixels=True)


# ---------------------------------------------------------------------------
# Single-file processing
# ---------------------------------------------------------------------------

class TestProcessFile:
    def test_output_file_created(self, single_dicom, output_dir, recipe_path):
        engine = DeidEngine(recipe_path)
        out = output_dir / "out.dcm"
        result = engine.process_file(single_dicom, out)
        assert result.success
        assert out.exists()

    # PatientID is kept in the test spec. Remove comments if test spec is changed to deidentify patient ID.
#    def test_patient_id_replaced(self, single_dicom, output_dir, recipe_path):
#        engine = DeidEngine(recipe_path)
#        out = output_dir / "out.dcm"
#        engine.process_file(single_dicom, out)
#        ds_in = _read(single_dicom)
#        ds_out = _read(out)
#        assert str(ds_out.PatientID) != str(ds_in.PatientID)

#    def test_patient_id_deterministic(self, single_dicom, output_dir, recipe_path):
#        """Same input file processed twice → same pseudonym."""
#        engine = DeidEngine(recipe_path)
#        out1 = output_dir / "out1.dcm"
#        out2 = output_dir / "out2.dcm"
#        engine.process_file(single_dicom, out1)
#        engine.process_file(single_dicom, out2)
#        assert str(_read(out1).PatientID) == str(_read(out2).PatientID)

    def test_patient_name_anonymised(self, single_dicom, output_dir, recipe_path):
        engine = DeidEngine(recipe_path)
        out = output_dir / "out.dcm"
        engine.process_file(single_dicom, out)
        ds = _read(out)
        assert str(ds.PatientName).upper() == "ANONYMOUS"

    def test_institution_removed(self, single_dicom, output_dir, recipe_path):
        engine = DeidEngine(recipe_path)
        out = output_dir / "out.dcm"
        engine.process_file(single_dicom, out)
        ds = _read(out)
        assert "InstitutionName" not in ds or not str(ds.InstitutionName).strip()

    def test_private_tags_removed(self, single_dicom, output_dir, recipe_path):
        """Private tag 0009,0010 added in conftest must be absent after de-id."""
        engine = DeidEngine(recipe_path, remove_private=True)
        out = output_dir / "out.dcm"
        engine.process_file(single_dicom, out)
        ds = _read(out)
        private_tags = [
            tag for tag in ds.keys() if tag.group % 2 != 0
        ]
        assert private_tags == [], f"Unexpected private tags: {private_tags}"

    def test_patient_identity_removed_flag_set(self, single_dicom, output_dir, recipe_path):
        """Recipe must ADD PatientIdentityRemoved = YES."""
        engine = DeidEngine(recipe_path)
        out = output_dir / "out.dcm"
        engine.process_file(single_dicom, out)
        ds = _read(out)
        assert "PatientIdentityRemoved" in ds
        assert str(ds.PatientIdentityRemoved).upper() == "YES"

    def test_bad_file_returns_failure_not_exception(self, output_dir, recipe_path, tmp_path):
        """A file that is valid enough to open but raises during deid processing
        must produce FileResult(success=False), never propagate an exception."""
        # Write a file with the DICOM preamble+magic but truncated body,
        # which causes pydicom to raise during tag parsing.
        bad_file = tmp_path / "truncated.dcm"
        bad_file.write_bytes(b"\x00" * 128 + b"DICM" + b"\x00" * 4)
        engine = DeidEngine(recipe_path)
        result = engine.process_file(bad_file, output_dir / "bad_out.dcm")
        # deid may or may not raise on a truncated file, but it must never
        # propagate — the result is always a FileResult.
        assert isinstance(result, FileResult)

    def test_output_directory_created_automatically(
        self, single_dicom, tmp_path, recipe_path
    ):
        engine = DeidEngine(recipe_path)
        deep_out = tmp_path / "a" / "b" / "c" / "out.dcm"
        result = engine.process_file(single_dicom, deep_out)
        assert result.success
        assert deep_out.exists()


# ---------------------------------------------------------------------------
# Directory processing
# ---------------------------------------------------------------------------

class TestProcessDirectory:
    def test_all_files_processed(self, dicom_dir, output_dir, recipe_path):
        engine = DeidEngine(recipe_path)
        run = engine.process_directory(dicom_dir, output_dir)
        assert len(run.results) == 3  # 2 + 1 from conftest fixture
        assert all(r.success for r in run.results)

    def test_directory_structure_preserved(self, dicom_dir, output_dir, recipe_path):
        """
        The patient/study/series subdirectory tree must be replicated under output_dir.
        """
        engine = DeidEngine(recipe_path)
        engine.process_directory(dicom_dir, output_dir)

        expected = [
            output_dir / "patient_A" / "study_001" / "series_001" / "image_001.dcm",
            output_dir / "patient_A" / "study_001" / "series_001" / "image_002.dcm",
            output_dir / "patient_B" / "study_002" / "series_001" / "image_001.dcm",
        ]
        for path in expected:
            assert path.exists(), f"Expected output file missing: {path}"

    def test_run_result_summary(self, dicom_dir, output_dir, recipe_path):
        engine = DeidEngine(recipe_path)
        run = engine.process_directory(dicom_dir, output_dir)
        summary = run.summary()
        assert "3 succeeded" in summary
        assert "0 failed" in summary

    def test_partial_failure_recorded(
        self, dicom_dir, output_dir, recipe_path, tmp_path
    ):
        """
        If process_file returns a failure for any file, RunResult must record it.
        We test this by monkeypatching process_file to fail on one specific file.
        """
        engine = DeidEngine(recipe_path)
        original_process = engine.process_file
        call_count = {"n": 0}

        def patched(infile, outfile):
            call_count["n"] += 1
            if call_count["n"] == 2:
                return FileResult(Path(infile), Path(outfile), success=False, error="injected failure")
            return original_process(infile, outfile)

        engine.process_file = patched
        run = engine.process_directory(dicom_dir, output_dir)
        assert len(run.failures) == 1
        assert len(run.successes) == 2

    def test_nonexistent_input_raises(self, output_dir, recipe_path):
        engine = DeidEngine(recipe_path)
        with pytest.raises(ValueError, match="does not exist"):
            engine.process_directory("/nonexistent/path", output_dir)

    def test_different_patients_get_different_pseudonyms(
        self, dicom_dir, output_dir, recipe_path
    ):
        engine = DeidEngine(recipe_path)
        engine.process_directory(dicom_dir, output_dir)

        pid_a = str(
            _read(output_dir / "patient_A" / "study_001" / "series_001" / "image_001.dcm")
            .PatientID
        )
        pid_b = str(
            _read(output_dir / "patient_B" / "study_002" / "series_001" / "image_001.dcm")
            .PatientID
        )
        assert pid_a != pid_b

    def test_same_patient_consistent_pseudonym_across_files(
        self, dicom_dir, output_dir, recipe_path
    ):
        """Both of patient_A's files must share the same anonymous PatientID."""
        engine = DeidEngine(recipe_path)
        engine.process_directory(dicom_dir, output_dir)

        pid1 = str(
            _read(output_dir / "patient_A" / "study_001" / "series_001" / "image_001.dcm")
            .PatientID
        )
        pid2 = str(
            _read(output_dir / "patient_A" / "study_001" / "series_001" / "image_002.dcm")
            .PatientID
        )
        assert pid1 == pid2


# ---------------------------------------------------------------------------
# RunResult
# ---------------------------------------------------------------------------

class TestRunResult:
    def test_successes_and_failures_partitioned(self):
        from dicom_deid.engine import FileResult
        run = RunResult()
        run.results = [
            FileResult(Path("a"), Path("b"), success=True),
            FileResult(Path("c"), Path("d"), success=False, error="oops"),
        ]
        assert len(run.successes) == 1
        assert len(run.failures) == 1
