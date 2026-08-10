"""
transforms.py
-------------
Replacement functions for use with `func:` references in a deid recipe.

Each function signature must match what deid passes:

    func(item, value, field, dicom) -> str | None

item   – the deid DatasetItem wrapper
value  – the current tag value (str) as read from the DICOM file
field  – the tag keyword (str), e.g. "PatientID"
dicom  – the pydicom Dataset object

Returning None causes deid to blank the field.
Returning the original value leaves it unchanged (passthrough).
"""

import hashlib
import logging
import os
from typing import Any

import pydicom

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Salt management - CURRENTLY DISABLED
# ---------------------------------------------------------------------------
# The salt MUST be set via the environment variable DEID_SALT before running.
# Using a hardcoded default is a known security risk (see Edge_De-id).
# A per-site secret salt means two sites cannot cross-correlate pseudonyms.
#
# TO RE-ENABLE SALTING IN FUTURE:
#   1. Uncomment the _SALT and _require_salt() blocks below
#   2. Uncomment the salt usage inside _hash()
#   3. Set the DEID_SALT environment variable before running:
#        export DEID_SALT=$(python3 -c "import secrets; print(secrets.token_hex(32))")
#   4. In engine.py, uncomment the _require_salt() call in DeidEngine.__init__()
#   5. Store the salt securely — loss of the salt breaks longitudinal patient linkage
#   6. In Kubernetes, inject DEID_SALT from a Secret, not a ConfigMap
# ---------------------------------------------------------------------------
#_SALT: str | None = os.environ.get("DEID_SALT")


#def _require_salt() -> str:
#    """Return the configured salt or raise a clear error at runtime."""
#    if not _SALT:
#        raise RuntimeError(
#            "Environment variable DEID_SALT must be set before running dicom-deid. "
#            "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
#        )
#    return _SALT


def _hash(value: str) -> str:
    """
    Deterministic SHA-256 pseudonymisation.

    Properties:
    - Same input + same salt → same output (cross-session linkability preserved)
    - Different salt → completely different output (site isolation)
    - 24 hex chars = 96 bits — negligible collision probability for realistic cohort sizes

    NOTE: Salting is currently disabled. The hash is computed without a salt,
    meaning the same input always produces the same output regardless of site.
    This is less secure but simpler to deploy.

    TO RE-ENABLE SALTING:
    Replace the line below with:
        salt = _require_salt()
        return hashlib.sha256((salt + value).encode("utf-8")).hexdigest()[:24]
    Then uncomment _SALT and _require_salt() above.
    """
    
#    Unsalted hash — replace with salted version when ready
#    salt = _require_salt()
#    return hashlib.sha256((salt + value).encode("utf-8")).hexdigest()[:24]
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]

def _hash_with_field(value: str, field: str) -> str:
    """
    Deterministic SHA-256 pseudonymisation with field-name prefix.

    Used for AccessionNumber and StudyID so that the same raw value
    produces a different hash than PatientID, preventing cross-linkage.

    NOTE: Salting is currently disabled. See _hash() above for re-enable instructions.

    TO RE-ENABLE SALTING:
        Replace the line below with:
            salt = _require_salt()
            return hashlib.sha256((salt + field + value).encode("utf-8")).hexdigest()[:16]
    """
    # Unsalted hash with field prefix — replace with salted version when ready
    return hashlib.sha256((field + value).encode("utf-8")).hexdigest()[:16]
    
# ---------------------------------------------------------------------------
# Public transform functions (referenced by `func:` in recipe)
# ---------------------------------------------------------------------------

def hash_patient_id(
    item: Any, value: str, field: str, dicom: pydicom.Dataset
) -> str | None:
    """
    Replace PatientID with a deterministic pseudonym.
    Consistent across all scans from the same patient.
    """
    if not value:
        logger.debug("hash_patient_id: empty value for field %s — blanking", field)
        return None
    pseudonym = _hash(str(value))
    logger.debug("hash_patient_id: %s → %s", field, pseudonym)
    return pseudonym


def hash_accession_number(
    item: Any, value: str, field: str, dicom: pydicom.Dataset
) -> str | None:
    """
    Replace AccessionNumber with a deterministic pseudonym.
    Salted separately from PatientID to prevent linkage via accession alone.
    """
    if not value:
        return None
    # AccessionNumber VR is SH (max 16 chars)
    return _hash_with_field(str(value), field)
#    salt = _require_salt()
#    return hashlib.sha256((salt + field + str(value)).encode("utf-8")).hexdigest()[:16]


def passthrough(
    item: Any, value: str, field: str, dicom: pydicom.Dataset
) -> str:
    """Return the value unchanged. Use for tags you want to keep as-is."""
    return value


def blank_if_present(
    item: Any, value: str, field: str, dicom: pydicom.Dataset
) -> None:
    """Unconditionally blank a tag. Use where REMOVE would delete the tag entirely
    but you need to preserve tag presence for DICOM conformance."""
    return None
