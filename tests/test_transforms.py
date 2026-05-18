"""
test_transforms.py
------------------
Unit tests for dicom_deid.transforms.

Tests are isolated — they do not touch the filesystem or real DICOM files.
"""

import hashlib
import os

import pytest

from dicom_deid.transforms import (
    _hash,
    blank_if_present,
    hash_accession_number,
    hash_patient_id,
    passthrough,
)


# ---------------------------------------------------------------------------
# _hash internals
# ---------------------------------------------------------------------------

class TestHashFunction:
    def test_deterministic(self):
        """Same input + same salt → same output every time."""
        assert _hash("PID001") == _hash("PID001")

    def test_different_inputs_differ(self):
        assert _hash("PID001") != _hash("PID002")

    def test_output_length(self):
        """Output is 24 hex characters (96 bits)."""
        assert len(_hash("anything")) == 24

    def test_salt_changes_output(self, monkeypatch):
        """Different DEID_SALT produces different hash for the same input."""
        import dicom_deid.transforms as t
        result_a = t._hash("PID001")
        monkeypatch.setattr(t, "_SALT", "totally_different_salt_xyz")
        result_b = t._hash("PID001")
        assert result_a != result_b

    def test_requires_salt(self, monkeypatch):
        """RuntimeError raised when _SALT is None."""
        import dicom_deid.transforms as t
        monkeypatch.setattr(t, "_SALT", None)
        with pytest.raises(RuntimeError, match="DEID_SALT"):
            t._hash("anything")


# ---------------------------------------------------------------------------
# hash_patient_id
# ---------------------------------------------------------------------------

class TestHashPatientId:
    def test_returns_string(self):
        result = hash_patient_id(None, "PID001", "PatientID", None)
        assert isinstance(result, str)
        assert len(result) == 24

    def test_empty_value_returns_none(self):
        result = hash_patient_id(None, "", "PatientID", None)
        assert result is None

    def test_consistent_across_calls(self):
        a = hash_patient_id(None, "PID001", "PatientID", None)
        b = hash_patient_id(None, "PID001", "PatientID", None)
        assert a == b

    def test_different_ids_produce_different_hashes(self):
        a = hash_patient_id(None, "PID001", "PatientID", None)
        b = hash_patient_id(None, "PID002", "PatientID", None)
        assert a != b


# ---------------------------------------------------------------------------
# hash_accession_number
# ---------------------------------------------------------------------------

class TestHashAccessionNumber:
    def test_differs_from_patient_id_hash(self):
        """Same raw value → different hashes for PatientID vs AccessionNumber."""
        pid_hash = hash_patient_id(None, "SAME_VALUE", "PatientID", None)
        acc_hash = hash_accession_number(None, "SAME_VALUE", "AccessionNumber", None)
        assert pid_hash != acc_hash

    def test_empty_returns_none(self):
        assert hash_accession_number(None, "", "AccessionNumber", None) is None


# ---------------------------------------------------------------------------
# passthrough and blank_if_present
# ---------------------------------------------------------------------------

class TestHelperTransforms:
    def test_passthrough_returns_value(self):
        result = passthrough(None, "keep_me", "SomeField", None)
        assert result == "keep_me"

    def test_blank_if_present_returns_none(self):
        result = blank_if_present(None, "some_value", "SomeField", None)
        assert result is None
