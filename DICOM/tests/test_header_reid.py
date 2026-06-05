"""
test_header_reid.py
-------------------
Test suite for header_reid.py.

Tests are organised into classes matching the public functions:

    TestSerialise          — _serialise() internal helper
    TestValuesEqual        — _values_equal() internal helper
    TestDiffSnapshots      — diff_snapshots() core comparison logic
    TestExtractLinkageUids — extract_linkage_uids()
    TestBuildReidDocument  — build_reid_document()
    TestWriteReidJson      — write_reid_json()
    TestSnapshotFromPydicom — snapshot_from_pydicom() DICOM helper
    TestEndToEnd           — full pipeline from raw DICOM to reid document

All tests are self-contained and use only synthetic data — no real patient
files are required.
"""

import importlib.util
import json
import copy
from pathlib import Path

import pydicom
import pydicom.uid
import pytest
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.sequence import Sequence

# ---------------------------------------------------------------------------
# Load header_reid module via importlib (handles the hyphen in filename)
# The tests directory sits alongside header_reid.py, so we resolve relative
# to this file's location.
# ---------------------------------------------------------------------------

def _load_header_reid():
    """Load header_reid.py regardless of whether it uses a hyphen or underscore."""
    candidates = [
        Path(__file__).parent / "header_reid.py",
        Path(__file__).parent / "header-reid.py",
    ]
    for candidate in candidates:
        if candidate.exists():
            spec = importlib.util.spec_from_file_location("header_reid", candidate)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise ImportError(
        "Could not find header_reid.py or header-reid.py in the same directory "
        "as test_header_reid.py. Ensure the file is present."
    )


hr = _load_header_reid()


# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def simple_pre() -> dict:
    """Minimal pre-de-identification snapshot."""
    return {
        "PatientName":    "Smith^John",
        "PatientID":      "PAT-001",
        "PatientBirthDate": "19870420",
        "InstitutionName": "Newcastle General Hospital",
        "StudyDate":      "20240315",
        "StudyDescription": "Brain MRI",
        "SOPInstanceUID": "1.2.3.4.5",
        "StudyInstanceUID": "1.2.3.4.6",
        "SeriesInstanceUID": "1.2.3.4.7",
    }


@pytest.fixture()
def simple_post() -> dict:
    """Corresponding post-de-identification snapshot."""
    return {
        "PatientName":    "ANONYMOUS",
        "PatientID":      "PAT-001",           # KEEP — unchanged
        "PatientBirthDate": "",                # BLANK
        # InstitutionName absent — REMOVE
        "StudyDate":      "20240315",          # unchanged
        "StudyDescription": "",               # BLANK
        "SOPInstanceUID": "1.2.3.4.5",        # preserved UID
        "StudyInstanceUID": "1.2.3.4.6",      # preserved UID
        "SeriesInstanceUID": "1.2.3.4.7",     # preserved UID
        "PatientIdentityRemoved": "YES",       # ADD
    }


@pytest.fixture()
def uid_keys() -> list[str]:
    return ["SOPInstanceUID", "StudyInstanceUID", "SeriesInstanceUID"]


@pytest.fixture()
def synthetic_dicom(tmp_path) -> Path:
    """Write a minimal synthetic DICOM file and return its path."""
    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = pydicom.uid.SecondaryCaptureImageStorage
    file_meta.MediaStorageSOPInstanceUID = pydicom.uid.generate_uid()
    file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian

    ds = Dataset()
    ds.file_meta = file_meta
    ds.PatientName          = "Smith^John"
    ds.PatientID            = "PAT-001"
    ds.PatientBirthDate     = "19870420"
    ds.PatientAddress       = "42 Fake Street, Newcastle NSW 2300"
    ds.InstitutionName      = "Newcastle General Hospital"
    ds.StudyDate            = "20240315"
    ds.StudyDescription     = "Brain MRI"
    ds.SOPClassUID          = pydicom.uid.SecondaryCaptureImageStorage
    ds.SOPInstanceUID       = file_meta.MediaStorageSOPInstanceUID
    ds.StudyInstanceUID     = pydicom.uid.generate_uid()
    ds.SeriesInstanceUID    = pydicom.uid.generate_uid()
    ds.Modality             = "MR"
    ds.Rows = 2; ds.Columns = 2
    ds.BitsAllocated = 8; ds.BitsStored = 8; ds.HighBit = 7
    ds.PixelRepresentation = 0; ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelData = bytes(4)

    # Private tag — should appear in snapshot by default
    ds.add_new([0x0009, 0x0010], "LO", "VendorPrivateData")

    # Sequence tag
    seq_item = Dataset()
    seq_item.ReferencedSOPClassUID = pydicom.uid.SecondaryCaptureImageStorage
    seq_item.ReferencedSOPInstanceUID = pydicom.uid.generate_uid()
    ds.ReferencedStudySequence = Sequence([seq_item])

    path = tmp_path / "synthetic.dcm"
    ds.save_as(str(path), enforce_file_format=True)
    return path


# ---------------------------------------------------------------------------
# TestSerialise
# ---------------------------------------------------------------------------

class TestSerialise:
    """Tests for _serialise() — ensures values are JSON-safe."""

    def test_string_passthrough(self):
        assert hr._serialise("hello") == "hello"

    def test_int_passthrough(self):
        assert hr._serialise(42) == 42

    def test_float_passthrough(self):
        assert hr._serialise(3.14) == 3.14

    def test_bool_passthrough(self):
        assert hr._serialise(True) is True

    def test_none_passthrough(self):
        assert hr._serialise(None) is None

    def test_list_recursed(self):
        assert hr._serialise([1, "two", 3.0]) == [1, "two", 3.0]

    def test_tuple_becomes_list(self):
        result = hr._serialise((1, 2, 3))
        assert result == [1, 2, 3]
        assert isinstance(result, list)

    def test_dict_recursed(self):
        result = hr._serialise({"a": 1, "b": [2, 3]})
        assert result == {"a": 1, "b": [2, 3]}

    def test_dict_keys_become_strings(self):
        result = hr._serialise({1: "one", 2: "two"})
        assert "1" in result
        assert "2" in result

    def test_unknown_type_becomes_string(self):
        class Custom:
            def __str__(self): return "custom_value"
        result = hr._serialise(Custom())
        assert result == "custom_value"
        assert isinstance(result, str)

    def test_nested_structure(self):
        result = hr._serialise({"key": [{"inner": 42}]})
        assert result == {"key": [{"inner": 42}]}


# ---------------------------------------------------------------------------
# TestValuesEqual
# ---------------------------------------------------------------------------

class TestValuesEqual:
    """Tests for _values_equal() — normalised string comparison."""

    def test_equal_strings(self):
        assert hr._values_equal("hello", "hello") is True

    def test_unequal_strings(self):
        assert hr._values_equal("hello", "world") is False

    def test_int_vs_string(self):
        assert hr._values_equal(0, "0") is True

    def test_whitespace_stripped(self):
        assert hr._values_equal("  hello  ", "hello") is True

    def test_different_values_not_equal(self):
        assert hr._values_equal("Smith^John", "ANONYMOUS") is False

    def test_empty_strings_equal(self):
        assert hr._values_equal("", "") is True


# ---------------------------------------------------------------------------
# TestDiffSnapshots
# ---------------------------------------------------------------------------

class TestDiffSnapshots:
    """Tests for diff_snapshots() — the core comparison logic."""

    def test_returns_four_categories(self, simple_pre, simple_post):
        result = hr.diff_snapshots(simple_pre, simple_post)
        assert set(result.keys()) == {"modified", "removed", "blanked", "added"}

    def test_modified_tag_detected(self, simple_pre, simple_post):
        result = hr.diff_snapshots(simple_pre, simple_post)
        assert "PatientName" in result["modified"]

    def test_modified_records_original_value(self, simple_pre, simple_post):
        result = hr.diff_snapshots(simple_pre, simple_post)
        assert result["modified"]["PatientName"] == "Smith^John"

    def test_removed_tag_detected(self, simple_pre, simple_post):
        result = hr.diff_snapshots(simple_pre, simple_post)
        assert "InstitutionName" in result["removed"]

    def test_removed_records_original_value(self, simple_pre, simple_post):
        result = hr.diff_snapshots(simple_pre, simple_post)
        assert result["removed"]["InstitutionName"] == "Newcastle General Hospital"

    def test_blanked_tag_detected(self, simple_pre, simple_post):
        result = hr.diff_snapshots(simple_pre, simple_post)
        assert "PatientBirthDate" in result["blanked"]
        assert "StudyDescription" in result["blanked"]

    def test_blanked_records_original_value(self, simple_pre, simple_post):
        result = hr.diff_snapshots(simple_pre, simple_post)
        assert result["blanked"]["PatientBirthDate"] == "19870420"

    def test_added_tag_detected(self, simple_pre, simple_post):
        result = hr.diff_snapshots(simple_pre, simple_post)
        assert "PatientIdentityRemoved" in result["added"]

    def test_added_records_new_value(self, simple_pre, simple_post):
        result = hr.diff_snapshots(simple_pre, simple_post)
        assert result["added"]["PatientIdentityRemoved"] == "YES"

    def test_unchanged_tag_not_recorded(self, simple_pre, simple_post):
        result = hr.diff_snapshots(simple_pre, simple_post)
        all_changed = (
            set(result["modified"])
            | set(result["removed"])
            | set(result["blanked"])
            | set(result["added"])
        )
        # StudyDate is identical in pre and post — must not appear
        assert "StudyDate" not in all_changed

    def test_kept_patient_id_not_recorded(self, simple_pre, simple_post):
        """PatientID is KEEP in the recipe — must not appear in any change category."""
        result = hr.diff_snapshots(simple_pre, simple_post)
        all_changed = (
            set(result["modified"])
            | set(result["removed"])
            | set(result["blanked"])
            | set(result["added"])
        )
        assert "PatientID" not in all_changed

    def test_uids_not_recorded_when_unchanged(self, simple_pre, simple_post):
        """Preserved UIDs must not appear in the diff."""
        result = hr.diff_snapshots(simple_pre, simple_post)
        all_changed = (
            set(result["modified"])
            | set(result["removed"])
            | set(result["blanked"])
            | set(result["added"])
        )
        assert "SOPInstanceUID" not in all_changed
        assert "StudyInstanceUID" not in all_changed
        assert "SeriesInstanceUID" not in all_changed

    def test_empty_snapshots_return_empty_diff(self):
        result = hr.diff_snapshots({}, {})
        assert all(len(v) == 0 for v in result.values())

    def test_all_tags_removed(self):
        pre  = {"TagA": "val_a", "TagB": "val_b"}
        post = {}
        result = hr.diff_snapshots(pre, post)
        assert "TagA" in result["removed"]
        assert "TagB" in result["removed"]
        assert len(result["modified"]) == 0
        assert len(result["added"]) == 0

    def test_all_tags_added(self):
        pre  = {}
        post = {"TagA": "val_a", "TagB": "val_b"}
        result = hr.diff_snapshots(pre, post)
        assert "TagA" in result["added"]
        assert "TagB" in result["added"]
        assert len(result["removed"]) == 0

    def test_type_normalisation_prevents_false_positive(self):
        """int 0 and string '0' should be considered equal."""
        pre  = {"Tag": 0}
        post = {"Tag": "0"}
        result = hr.diff_snapshots(pre, post)
        assert "Tag" not in result["modified"]

    def test_blanked_vs_removed_distinction(self):
        """A tag set to '' is blanked, not removed."""
        pre  = {"TagA": "value", "TagB": "value"}
        post = {"TagA": ""}  # blanked — present but empty
        # TagB absent from post — removed
        result = hr.diff_snapshots(pre, post)
        assert "TagA" in result["blanked"]
        assert "TagB" in result["removed"]
        assert "TagA" not in result["removed"]
        assert "TagB" not in result["blanked"]


# ---------------------------------------------------------------------------
# TestExtractLinkageUids
# ---------------------------------------------------------------------------

class TestExtractLinkageUids:
    """Tests for extract_linkage_uids()."""

    def test_extracts_present_uids(self, simple_post, uid_keys):
        uids = hr.extract_linkage_uids(simple_post, uid_keys)
        assert uids["SOPInstanceUID"] == "1.2.3.4.5"
        assert uids["StudyInstanceUID"] == "1.2.3.4.6"
        assert uids["SeriesInstanceUID"] == "1.2.3.4.7"

    def test_missing_uid_recorded_as_not_present(self):
        post = {"SOPInstanceUID": "1.2.3"}
        uids = hr.extract_linkage_uids(
            post, ["SOPInstanceUID", "StudyInstanceUID"]
        )
        assert uids["SOPInstanceUID"] == "1.2.3"
        assert uids["StudyInstanceUID"] == "<not present>"

    def test_all_uids_missing(self):
        uids = hr.extract_linkage_uids({}, ["SOPInstanceUID"])
        assert uids["SOPInstanceUID"] == "<not present>"

    def test_returns_strings(self, simple_post, uid_keys):
        uids = hr.extract_linkage_uids(simple_post, uid_keys)
        for v in uids.values():
            assert isinstance(v, str)

    def test_empty_uid_keys(self, simple_post):
        uids = hr.extract_linkage_uids(simple_post, [])
        assert uids == {}

    def test_reads_from_post_not_pre(self):
        """UIDs must come from post snapshot, not pre."""
        post = {"SOPInstanceUID": "post-uid-value"}
        uids = hr.extract_linkage_uids(post, ["SOPInstanceUID"])
        assert uids["SOPInstanceUID"] == "post-uid-value"


# ---------------------------------------------------------------------------
# TestBuildReidDocument
# ---------------------------------------------------------------------------

class TestBuildReidDocument:
    """Tests for build_reid_document()."""

    def test_returns_dict(self, simple_pre, simple_post, uid_keys):
        doc = hr.build_reid_document(simple_pre, simple_post, uid_keys)
        assert isinstance(doc, dict)

    def test_required_top_level_keys(self, simple_pre, simple_post, uid_keys):
        doc = hr.build_reid_document(simple_pre, simple_post, uid_keys)
        assert "created_at" in doc
        assert "format" in doc
        assert "source_file" in doc
        assert "linkage_uids" in doc
        assert "changes" in doc

    def test_format_label_recorded(self, simple_pre, simple_post, uid_keys):
        doc = hr.build_reid_document(
            simple_pre, simple_post, uid_keys, format_label="DICOM"
        )
        assert doc["format"] == "DICOM"

    def test_source_file_recorded(self, simple_pre, simple_post, uid_keys):
        doc = hr.build_reid_document(
            simple_pre, simple_post, uid_keys,
            source_file="input/image.dcm"
        )
        assert doc["source_file"] == "input/image.dcm"

    def test_source_file_none_when_not_provided(
        self, simple_pre, simple_post, uid_keys
    ):
        doc = hr.build_reid_document(simple_pre, simple_post, uid_keys)
        assert doc["source_file"] is None

    def test_created_at_is_utc_iso8601(self, simple_pre, simple_post, uid_keys):
        doc = hr.build_reid_document(simple_pre, simple_post, uid_keys)
        ts = doc["created_at"]
        assert ts.endswith("Z")
        assert "T" in ts
        # Should parse without error
        from datetime import datetime, timezone
        datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")

    def test_linkage_uids_present(self, simple_pre, simple_post, uid_keys):
        doc = hr.build_reid_document(simple_pre, simple_post, uid_keys)
        assert "SOPInstanceUID" in doc["linkage_uids"]
        assert doc["linkage_uids"]["SOPInstanceUID"] == "1.2.3.4.5"

    def test_changes_structure(self, simple_pre, simple_post, uid_keys):
        doc = hr.build_reid_document(simple_pre, simple_post, uid_keys)
        assert set(doc["changes"].keys()) == {
            "modified", "removed", "blanked", "added"
        }

    def test_modified_contains_original_patient_name(
        self, simple_pre, simple_post, uid_keys
    ):
        doc = hr.build_reid_document(simple_pre, simple_post, uid_keys)
        assert doc["changes"]["modified"]["PatientName"] == "Smith^John"

    def test_removed_contains_institution(
        self, simple_pre, simple_post, uid_keys
    ):
        doc = hr.build_reid_document(simple_pre, simple_post, uid_keys)
        assert "InstitutionName" in doc["changes"]["removed"]

    def test_blanked_contains_birth_date(
        self, simple_pre, simple_post, uid_keys
    ):
        doc = hr.build_reid_document(simple_pre, simple_post, uid_keys)
        assert "PatientBirthDate" in doc["changes"]["blanked"]
        assert doc["changes"]["blanked"]["PatientBirthDate"] == "19870420"

    def test_added_contains_patient_identity_removed(
        self, simple_pre, simple_post, uid_keys
    ):
        doc = hr.build_reid_document(simple_pre, simple_post, uid_keys)
        assert "PatientIdentityRemoved" in doc["changes"]["added"]

    def test_raises_on_empty_pre_snapshot(self, simple_post, uid_keys):
        with pytest.raises(ValueError, match="pre_snapshot"):
            hr.build_reid_document({}, simple_post, uid_keys)

    def test_raises_on_empty_post_snapshot(self, simple_pre, uid_keys):
        with pytest.raises(ValueError, match="post_snapshot"):
            hr.build_reid_document(simple_pre, {}, uid_keys)

    def test_raises_on_empty_uid_keys(self, simple_pre, simple_post):
        with pytest.raises(ValueError, match="uid_keys"):
            hr.build_reid_document(simple_pre, simple_post, [])

    def test_twix_format_label(self, simple_pre, simple_post):
        doc = hr.build_reid_document(
            simple_pre, simple_post,
            uid_keys=["SOPInstanceUID"],
            format_label="TWIX",
        )
        assert doc["format"] == "TWIX"

    def test_document_is_json_serialisable(
        self, simple_pre, simple_post, uid_keys
    ):
        doc = hr.build_reid_document(simple_pre, simple_post, uid_keys)
        # Should not raise
        serialised = json.dumps(doc)
        assert len(serialised) > 0


# ---------------------------------------------------------------------------
# TestWriteReidJson
# ---------------------------------------------------------------------------

class TestWriteReidJson:
    """Tests for write_reid_json()."""

    def test_file_created(self, tmp_path, simple_pre, simple_post, uid_keys):
        out = tmp_path / "test.reid.json"
        hr.write_reid_json(simple_pre, simple_post, uid_keys, out)
        assert out.exists()

    def test_returns_path(self, tmp_path, simple_pre, simple_post, uid_keys):
        out = tmp_path / "test.reid.json"
        result = hr.write_reid_json(simple_pre, simple_post, uid_keys, out)
        assert result == out

    def test_output_is_valid_json(self, tmp_path, simple_pre, simple_post, uid_keys):
        out = tmp_path / "test.reid.json"
        hr.write_reid_json(simple_pre, simple_post, uid_keys, out)
        with open(out, encoding="utf-8") as f:
            doc = json.load(f)
        assert isinstance(doc, dict)

    def test_output_has_correct_structure(
        self, tmp_path, simple_pre, simple_post, uid_keys
    ):
        out = tmp_path / "test.reid.json"
        hr.write_reid_json(simple_pre, simple_post, uid_keys, out)
        with open(out) as f:
            doc = json.load(f)
        assert "linkage_uids" in doc
        assert "changes" in doc
        assert "created_at" in doc

    def test_creates_parent_directories(
        self, tmp_path, simple_pre, simple_post, uid_keys
    ):
        out = tmp_path / "a" / "b" / "c" / "test.reid.json"
        hr.write_reid_json(simple_pre, simple_post, uid_keys, out)
        assert out.exists()

    def test_accepts_string_path(
        self, tmp_path, simple_pre, simple_post, uid_keys
    ):
        out = str(tmp_path / "test.reid.json")
        hr.write_reid_json(simple_pre, simple_post, uid_keys, out)
        assert Path(out).exists()

    def test_format_label_in_output(
        self, tmp_path, simple_pre, simple_post, uid_keys
    ):
        out = tmp_path / "test.reid.json"
        hr.write_reid_json(
            simple_pre, simple_post, uid_keys, out, format_label="DICOM"
        )
        with open(out) as f:
            doc = json.load(f)
        assert doc["format"] == "DICOM"

    def test_source_file_in_output(
        self, tmp_path, simple_pre, simple_post, uid_keys
    ):
        out = tmp_path / "test.reid.json"
        hr.write_reid_json(
            simple_pre, simple_post, uid_keys, out,
            source_file="input/image.dcm"
        )
        with open(out) as f:
            doc = json.load(f)
        assert doc["source_file"] == "input/image.dcm"

    def test_unicode_preserved(self, tmp_path, uid_keys):
        """Non-ASCII characters in tag values must survive round-trip."""
        pre  = {"PatientName": "Müller^Hans", "SOPInstanceUID": "1.2.3"}
        post = {"PatientName": "ANONYMOUS",   "SOPInstanceUID": "1.2.3"}
        out = tmp_path / "unicode.reid.json"
        hr.write_reid_json(pre, post, ["SOPInstanceUID"], out)
        with open(out, encoding="utf-8") as f:
            doc = json.load(f)
        assert doc["changes"]["modified"]["PatientName"] == "Müller^Hans"


# ---------------------------------------------------------------------------
# TestSnapshotFromPydicom
# ---------------------------------------------------------------------------

class TestSnapshotFromPydicom:
    """Tests for snapshot_from_pydicom()."""

    def test_returns_dict(self, synthetic_dicom):
        ds = pydicom.dcmread(str(synthetic_dicom), stop_before_pixels=True)
        snap = hr.snapshot_from_pydicom(ds)
        assert isinstance(snap, dict)

    def test_patient_name_included(self, synthetic_dicom):
        ds = pydicom.dcmread(str(synthetic_dicom), stop_before_pixels=True)
        snap = hr.snapshot_from_pydicom(ds)
        assert "PatientName" in snap
        assert snap["PatientName"] == "Smith^John"

    def test_patient_id_included(self, synthetic_dicom):
        ds = pydicom.dcmread(str(synthetic_dicom), stop_before_pixels=True)
        snap = hr.snapshot_from_pydicom(ds)
        assert "PatientID" in snap
        assert snap["PatientID"] == "PAT-001"

    def test_pixel_data_excluded(self, synthetic_dicom):
        ds = pydicom.dcmread(str(synthetic_dicom))
        snap = hr.snapshot_from_pydicom(ds)
        assert "PixelData" not in snap

    def test_private_tags_included_by_default(self, synthetic_dicom):
        ds = pydicom.dcmread(str(synthetic_dicom), stop_before_pixels=True)
        snap = hr.snapshot_from_pydicom(ds, include_private=True)
        private_keys = [k for k in snap if k.startswith("(")]
        assert len(private_keys) > 0

    def test_private_tags_excluded_when_requested(self, synthetic_dicom):
        ds = pydicom.dcmread(str(synthetic_dicom), stop_before_pixels=True)
        snap = hr.snapshot_from_pydicom(ds, include_private=False)
        private_keys = [k for k in snap if k.startswith("(")]
        assert len(private_keys) == 0

    def test_sequence_serialised_as_list(self, synthetic_dicom):
        ds = pydicom.dcmread(str(synthetic_dicom), stop_before_pixels=True)
        snap = hr.snapshot_from_pydicom(ds)
        assert "ReferencedStudySequence" in snap
        assert isinstance(snap["ReferencedStudySequence"], list)
        assert isinstance(snap["ReferencedStudySequence"][0], dict)

    def test_all_values_are_json_serialisable(self, synthetic_dicom):
        ds = pydicom.dcmread(str(synthetic_dicom), stop_before_pixels=True)
        snap = hr.snapshot_from_pydicom(ds)
        # Should not raise
        json.dumps(snap)

    def test_uids_present_in_snapshot(self, synthetic_dicom):
        ds = pydicom.dcmread(str(synthetic_dicom), stop_before_pixels=True)
        snap = hr.snapshot_from_pydicom(ds)
        assert "SOPInstanceUID" in snap
        assert "StudyInstanceUID" in snap
        assert "SeriesInstanceUID" in snap

    def test_empty_dataset_returns_empty_dict(self):
        ds = Dataset()
        snap = hr.snapshot_from_pydicom(ds)
        assert isinstance(snap, dict)
        assert len(snap) == 0


# ---------------------------------------------------------------------------
# TestEndToEnd
# ---------------------------------------------------------------------------

class TestEndToEnd:
    """
    Full pipeline tests: raw DICOM → pre-snapshot → de-identify →
    post-snapshot → build_reid_document → verify.
    """

    def test_full_pipeline_produces_valid_document(
        self, synthetic_dicom, tmp_path
    ):
        """Simulate the full dicom_deid + header_reid pipeline."""
        # Pre-snapshot
        original_ds = pydicom.dcmread(
            str(synthetic_dicom), stop_before_pixels=True
        )
        pre = hr.snapshot_from_pydicom(original_ds)

        # Simulate de-identification
        deid_ds = copy.deepcopy(original_ds)
        deid_ds.PatientName = "ANONYMOUS"
        deid_ds.PatientBirthDate = ""
        deid_ds.StudyDescription = ""
        if "InstitutionName" in deid_ds:
            del deid_ds.InstitutionName
        deid_ds.PatientIdentityRemoved = "YES"

        # Post-snapshot
        post = hr.snapshot_from_pydicom(deid_ds)

        # Build document
        doc = hr.build_reid_document(
            pre_snapshot=pre,
            post_snapshot=post,
            uid_keys=["SOPInstanceUID", "StudyInstanceUID", "SeriesInstanceUID"],
            source_file=str(synthetic_dicom),
            format_label="DICOM",
        )

        # Verify structure
        assert doc["format"] == "DICOM"
        assert doc["source_file"] == str(synthetic_dicom)
        assert "SOPInstanceUID" in doc["linkage_uids"]
        assert doc["linkage_uids"]["SOPInstanceUID"] != "<not present>"

        # Verify changes
        assert "PatientName" in doc["changes"]["modified"]
        assert doc["changes"]["modified"]["PatientName"] == "Smith^John"
        assert "PatientBirthDate" in doc["changes"]["blanked"]
        assert "InstitutionName" in doc["changes"]["removed"]
        assert "PatientIdentityRemoved" in doc["changes"]["added"]

    def test_uid_in_document_matches_deid_file(
        self, synthetic_dicom, tmp_path
    ):
        """
        The SOPInstanceUID in the reid document must match the one in the
        de-identified DICOM — this is the linkage mechanism.
        """
        original_ds = pydicom.dcmread(
            str(synthetic_dicom), stop_before_pixels=True
        )
        pre = hr.snapshot_from_pydicom(original_ds)

        deid_ds = copy.deepcopy(original_ds)
        deid_ds.PatientName = "ANONYMOUS"
        post = hr.snapshot_from_pydicom(deid_ds)

        doc = hr.build_reid_document(
            pre_snapshot=pre,
            post_snapshot=post,
            uid_keys=["SOPInstanceUID", "StudyInstanceUID", "SeriesInstanceUID"],
        )

        assert (
            doc["linkage_uids"]["SOPInstanceUID"]
            == str(deid_ds.SOPInstanceUID)
        )

    def test_no_phi_in_linkage_uids(self, synthetic_dicom):
        """
        Linkage UIDs must be UIDs only — must not contain patient name,
        date of birth, or other PHI.
        """
        original_ds = pydicom.dcmread(
            str(synthetic_dicom), stop_before_pixels=True
        )
        pre = hr.snapshot_from_pydicom(original_ds)

        deid_ds = copy.deepcopy(original_ds)
        deid_ds.PatientName = "ANONYMOUS"
        post = hr.snapshot_from_pydicom(deid_ds)

        doc = hr.build_reid_document(
            pre_snapshot=pre,
            post_snapshot=post,
            uid_keys=["SOPInstanceUID", "StudyInstanceUID", "SeriesInstanceUID"],
        )

        for uid_val in doc["linkage_uids"].values():
            assert "Smith" not in uid_val
            assert "19870420" not in uid_val

    def test_write_and_reload_preserves_data(
        self, synthetic_dicom, tmp_path
    ):
        """Write reid JSON to disk and reload it — all values must survive."""
        original_ds = pydicom.dcmread(
            str(synthetic_dicom), stop_before_pixels=True
        )
        pre = hr.snapshot_from_pydicom(original_ds)

        deid_ds = copy.deepcopy(original_ds)
        deid_ds.PatientName = "ANONYMOUS"
        deid_ds.PatientBirthDate = ""
        post = hr.snapshot_from_pydicom(deid_ds)

        out = tmp_path / "test.reid.json"
        hr.write_reid_json(
            pre_snapshot=pre,
            post_snapshot=post,
            uid_keys=["SOPInstanceUID", "StudyInstanceUID", "SeriesInstanceUID"],
            output_path=out,
            source_file=str(synthetic_dicom),
            format_label="DICOM",
        )

        with open(out, encoding="utf-8") as f:
            reloaded = json.load(f)

        assert reloaded["format"] == "DICOM"
        assert reloaded["changes"]["modified"]["PatientName"] == "Smith^John"
        assert reloaded["changes"]["blanked"]["PatientBirthDate"] == "19870420"
        assert "SOPInstanceUID" in reloaded["linkage_uids"]
