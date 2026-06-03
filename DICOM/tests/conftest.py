"""
conftest.py
-----------
Shared pytest fixtures.

Key fixture: ``dicom_dir`` creates a temporary directory tree of synthetic
DICOM files so tests never depend on real patient data.
"""

import os
import shutil
from pathlib import Path

import pydicom
import pydicom.uid
import pytest
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.sequence import Sequence


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dicom(
    filepath: Path,
    patient_id: str = "REAL_PID_001",
    patient_name: str = "Smith^John",
    institution: str = "Test Hospital",
    study_date: str = "20230101",
    extra_tags: dict | None = None,
) -> None:
    """
    Write a minimal but standards-conformant synthetic DICOM file.
    Includes a private tag and a sequence to exercise strip_sequences/remove_private.
    """
    filepath.parent.mkdir(parents=True, exist_ok=True)

    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = pydicom.uid.SecondaryCaptureImageStorage
    file_meta.MediaStorageSOPInstanceUID = pydicom.uid.generate_uid()
    file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian

    ds = Dataset()
    ds.file_meta = file_meta

    # Patient module
    ds.PatientName = patient_name
    ds.PatientID = patient_id
    ds.PatientBirthDate = "19800101"
    ds.PatientAddress = "123 Fake Street, Brisbane QLD"

    # Study module
    ds.StudyInstanceUID = pydicom.uid.generate_uid()
    ds.StudyDate = study_date
    ds.StudyTime = "120000.000000"
    ds.AccessionNumber = "ACC123456"
    ds.StudyID = "STUDY001"
    ds.StudyDescription = "Brain MRI"
    ds.ReferringPhysicianName = "Dr Jones"

    # Series module
    ds.SeriesInstanceUID = pydicom.uid.generate_uid()
    ds.SeriesDate = study_date
    ds.SeriesTime = "120500.000000"
    ds.Modality = "MR"
    ds.SeriesDescription = "T1 axial"
    ds.InstitutionName = institution
    ds.StationName = "SCANNER01"

    # SOP common
    ds.SOPClassUID = pydicom.uid.SecondaryCaptureImageStorage
    ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID

    # Operator / physician tags (PHI)
    ds.OperatorsName = "Operator^Name"
    ds.PerformingPhysicianName = "Doctor^Performing"

    # A private tag (should be removed by remove_private=True)
    ds.add_new([0x0009, 0x0010], "LO", "VendorPrivateData")

    # A sequence tag (should be stripped by strip_sequences=True)
    seq_item = Dataset()
    seq_item.ReferencedSOPClassUID = pydicom.uid.SecondaryCaptureImageStorage
    seq_item.ReferencedSOPInstanceUID = pydicom.uid.generate_uid()
    ds.ReferencedStudySequence = Sequence([seq_item])

    # Minimal pixel data so the file is valid
    ds.Rows = 2
    ds.Columns = 2
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelData = b"\x00\x01\x02\x03"

    if extra_tags:
        for tag, value in extra_tags.items():
            setattr(ds, tag, value)

    ds.save_as(str(filepath), enforce_file_format=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def dicom_dir(tmp_path: Path) -> Path:
    """
    A temporary directory containing a realistic DICOM hierarchy:

        dicom_dir/
            patient_A/
                study_001/
                    series_001/
                        image_001.dcm
                        image_002.dcm
            patient_B/
                study_002/
                    series_001/
                        image_001.dcm
    """
    _make_dicom(
        tmp_path / "patient_A" / "study_001" / "series_001" / "image_001.dcm",
        patient_id="PID_A",
        patient_name="Alice^Smith",
    )
    _make_dicom(
        tmp_path / "patient_A" / "study_001" / "series_001" / "image_002.dcm",
        patient_id="PID_A",
        patient_name="Alice^Smith",
    )
    _make_dicom(
        tmp_path / "patient_B" / "study_002" / "series_001" / "image_001.dcm",
        patient_id="PID_B",
        patient_name="Bob^Jones",
    )
    return tmp_path


@pytest.fixture()
def single_dicom(tmp_path: Path) -> Path:
    """A single synthetic DICOM file."""
    path = tmp_path / "test.dcm"
    _make_dicom(path)
    return path


@pytest.fixture()
def output_dir(tmp_path: Path) -> Path:
    """Empty output directory."""
    out = tmp_path / "output"
    out.mkdir()
    return out

# Salt fixture — CURRENTLY DISABLED
# TO RE-ENABLE: uncomment this fixture once salting is re-enabled in transforms.py
# This fixture sets a test-only salt so tests do not require DEID_SALT in the environment.
#@pytest.fixture(autouse=True)
#def set_deid_salt(monkeypatch: pytest.MonkeyPatch) -> None:
#    """
#    Set DEID_SALT for all tests so transforms don't raise RuntimeError.
#    Never use this value outside of testing.
#    """
#    monkeypatch.setenv("DEID_SALT", "test_salt_do_not_use_in_production_abc123")


@pytest.fixture()
def recipe_path() -> Path:
    """Path to the project recipe file."""
    candidate = Path(__file__).parent.parent / "recipe.dicom"
    if not candidate.exists():
        pytest.skip("recipe.dicom not found — run from repo root")
    return candidate
