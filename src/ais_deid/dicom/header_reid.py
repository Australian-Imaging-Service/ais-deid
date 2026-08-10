"""
header_reid.py
--------------
Format-agnostic re-identification mapping module.

Takes a pre-de-identification metadata snapshot (JSON) and a
post-de-identification metadata snapshot (JSON), computes what changed,
and writes a standardised re-identification JSON document containing:

    - The linkage UIDs (preserved identifiers that connect the de-identified
      file back to this mapping document)
    - The original values of every tag that was modified, blanked, removed,
      or added during de-identification
    - Audit metadata (timestamp, format label, source file path)

This module is intentionally format-agnostic. It knows nothing about DICOM,
TWIX, or any other medical image format. Each format-specific de-identification
pipeline is responsible for:

    1. Producing a pre-snapshot JSON before de-identification runs
    2. Producing a post-snapshot JSON after de-identification completes
    3. Calling build_reid_document() or write_reid_json() with both snapshots
       and a list of which keys are the linkage UIDs for that format

Usage example (DICOM pipeline)
-------------------------------
    from header_reid import write_reid_json

    pre  = snapshot_from_dicom(original_dataset)   # dict
    post = snapshot_from_dicom(deid_dataset)        # dict

    write_reid_json(
        pre_snapshot=pre,
        post_snapshot=post,
        uid_keys=["SOPInstanceUID", "StudyInstanceUID", "SeriesInstanceUID"],
        output_path=Path("reid/image_001.reid.json"),
        source_file="input/patient_A/study_001/image_001.dcm",
        format_label="DICOM",
    )

Usage example (TWIX pipeline)
------------------------------
    pre  = snapshot_from_twix(original_header)
    post = snapshot_from_twix(deid_header)

    write_reid_json(
        pre_snapshot=pre,
        post_snapshot=post,
        uid_keys=["PatientID", "StudyInstanceUID"],
        output_path=Path("reid/scan_001.reid.json"),
        source_file="input/scan_001.dat",
        format_label="TWIX",
    )

Snapshot schema
---------------
A snapshot is a plain Python dict mapping tag names (str) to their values.
Values must be JSON-serialisable. Nested structures (DICOM sequences) should
be serialised as nested dicts or lists. The same serialisation logic must be
used for both pre and post snapshots so the diff is meaningful.

    {
        "PatientName":    "Smith^John",
        "PatientID":      "PAT-001",
        "StudyDate":      "20240315",
        "ReferencedStudySequence": [
            {"ReferencedSOPClassUID": "...", "ReferencedSOPInstanceUID": "..."}
        ],
        "(0009,0010)":    "VendorPrivateData"   # private tags by numeric key
    }

Output schema
-------------
    {
        "created_at":    "2024-03-15T09:30:12Z",
        "format":        "DICOM",
        "source_file":   "input/patient_A/study_001/image_001.dcm",
        "linkage_uids":  {
            "SOPInstanceUID":    "1.2.3.4.5.6.7.8.9",
            "StudyInstanceUID":  "1.2.3.4.5.6.7.8.10",
            "SeriesInstanceUID": "1.2.3.4.5.6.7.8.11"
        },
        "changes": {
            "modified": {
                "PatientName": "Smith^John",
                "PatientID":   "PAT-001"
            },
            "removed": {
                "InstitutionName": "Newcastle General Hospital"
            },
            "blanked": {
                "PatientBirthDate": "19870420",
                "StudyDescription": "Brain MRI"
            },
            "added": {
                "PatientIdentityRemoved": "YES"
            }
        }
    }

The "changes" dict records the *original* (pre) values for modified/removed/blanked
tags, and the *new* (post) values for added tags. Tags that are identical in both
snapshots are not recorded.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentinel used to distinguish "key absent" from "key present but empty"
# ---------------------------------------------------------------------------
_ABSENT = object()


# ---------------------------------------------------------------------------
# Core diff logic
# ---------------------------------------------------------------------------

def _serialise(value: Any) -> Any:
    """
    Ensure a value is JSON-serialisable.

    Converts common non-serialisable types to strings. Format-specific
    snapshot builders should do this themselves, but this provides a
    safety net.
    """
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, (list, tuple)):
        return [_serialise(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _serialise(v) for k, v in value.items()}
    return str(value)


def _values_equal(a: Any, b: Any) -> bool:
    """
    Compare two snapshot values for equality after normalising to strings.

    Normalisation prevents false positives from type differences introduced
    by serialisation (e.g. int 0 vs string "0") when both snapshots were
    produced by different code paths.
    """
    return str(a).strip() == str(b).strip()


def diff_snapshots(
    pre: dict[str, Any],
    post: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """
    Compare two metadata snapshots and classify every change.

    Parameters
    ----------
    pre:
        Snapshot taken *before* de-identification. Keys are tag names,
        values are the original field values.
    post:
        Snapshot taken *after* de-identification. Keys are tag names,
        values are the de-identified field values.

    Returns
    -------
    dict with four sub-dicts:
        "modified"  — tags present in both but with different values.
                      Value recorded is the *original* (pre) value.
        "removed"   — tags present in pre but absent in post.
                      Value recorded is the *original* (pre) value.
        "blanked"   — tags present in both but post value is empty string.
                      Value recorded is the *original* (pre) value.
        "added"     — tags absent in pre but present in post.
                      Value recorded is the *new* (post) value.
    """
    modified: dict[str, Any] = {}
    removed:  dict[str, Any] = {}
    blanked:  dict[str, Any] = {}
    added:    dict[str, Any] = {}

    all_keys = set(pre.keys()) | set(post.keys())

    for key in sorted(all_keys):
        pre_val  = pre.get(key,  _ABSENT)
        post_val = post.get(key, _ABSENT)

        # Tag absent from both (shouldn't happen, but guard anyway)
        if pre_val is _ABSENT and post_val is _ABSENT:
            continue

        # Tag added by de-identification (e.g. PatientIdentityRemoved = YES)
        if pre_val is _ABSENT:
            added[key] = _serialise(post_val)
            continue

        # Tag removed entirely by de-identification
        if post_val is _ABSENT:
            removed[key] = _serialise(pre_val)
            continue

        # Tag blanked (value replaced with empty string)
        if str(post_val).strip() == "" and str(pre_val).strip() != "":
            blanked[key] = _serialise(pre_val)
            continue

        # Tag modified (value changed but not blanked)
        if not _values_equal(pre_val, post_val):
            modified[key] = _serialise(pre_val)
            continue

        # Tag unchanged — not recorded
        logger.debug("Unchanged tag: %s", key)

    return {
        "modified": modified,
        "removed":  removed,
        "blanked":  blanked,
        "added":    added,
    }


# ---------------------------------------------------------------------------
# UID extraction
# ---------------------------------------------------------------------------

def extract_linkage_uids(
    post_snapshot: dict[str, Any],
    uid_keys: list[str],
) -> dict[str, str]:
    """
    Extract linkage UIDs from the *post* (de-identified) snapshot.

    UIDs are read from the post snapshot because they are preserved through
    de-identification — they are the bridge between the de-identified file
    and this mapping document.

    Parameters
    ----------
    post_snapshot:
        The de-identified metadata snapshot.
    uid_keys:
        List of tag names that serve as linkage identifiers for this format.
        For DICOM: ["SOPInstanceUID", "StudyInstanceUID", "SeriesInstanceUID"]
        For TWIX:  ["PatientID", "StudyInstanceUID"] (or format-equivalent)

    Returns
    -------
    dict mapping each uid_key to its value in the post snapshot.
    Missing keys are recorded as "<not present>" with a warning.
    """
    uids: dict[str, str] = {}
    for key in uid_keys:
        if key in post_snapshot:
            uids[key] = str(post_snapshot[key])
        else:
            logger.warning(
                "UID key '%s' not found in post-snapshot — "
                "this UID cannot be used for linkage.",
                key,
            )
            uids[key] = "<not present>"
    return uids


# ---------------------------------------------------------------------------
# Document builder
# ---------------------------------------------------------------------------

def build_reid_document(
    pre_snapshot: dict[str, Any],
    post_snapshot: dict[str, Any],
    uid_keys: list[str],
    source_file: str | Path | None = None,
    format_label: str = "UNKNOWN",
) -> dict[str, Any]:
    """
    Build a re-identification mapping document as a Python dict.

    This is the core function. Call this if you want the dict for further
    processing (e.g. storing in a database) rather than writing to a file.

    Parameters
    ----------
    pre_snapshot:
        Metadata snapshot taken before de-identification.
    post_snapshot:
        Metadata snapshot taken after de-identification.
    uid_keys:
        Tag names to extract from the post snapshot as linkage UIDs.
    source_file:
        Path to the original (pre-de-identification) file. Recorded for
        audit purposes only; not used for any computation.
    format_label:
        Human-readable format identifier (e.g. "DICOM", "TWIX"). Recorded
        in the output for audit and tooling purposes.

    Returns
    -------
    A dict conforming to the output schema described in this module's
    docstring. Ready to serialise with json.dumps().
    """
    if not pre_snapshot:
        raise ValueError("pre_snapshot must not be empty.")
    if not post_snapshot:
        raise ValueError("post_snapshot must not be empty.")
    if not uid_keys:
        raise ValueError(
            "uid_keys must contain at least one key so the mapping document "
            "can be linked to its corresponding de-identified file."
        )

    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    changes   = diff_snapshots(pre_snapshot, post_snapshot)
    uids      = extract_linkage_uids(post_snapshot, uid_keys)

    total_changes = sum(len(v) for v in changes.values())
    logger.info(
        "Reid document built: %d changed tags "
        "(%d modified, %d removed, %d blanked, %d added) | format=%s",
        total_changes,
        len(changes["modified"]),
        len(changes["removed"]),
        len(changes["blanked"]),
        len(changes["added"]),
        format_label,
    )

    return {
        "created_at":  timestamp,
        "format":      format_label,
        "source_file": str(source_file) if source_file else None,
        "linkage_uids": uids,
        "changes": changes,
    }


# ---------------------------------------------------------------------------
# File writer
# ---------------------------------------------------------------------------

def write_reid_json(
    pre_snapshot: dict[str, Any],
    post_snapshot: dict[str, Any],
    uid_keys: list[str],
    output_path: str | Path,
    source_file: str | Path | None = None,
    format_label: str = "UNKNOWN",
    indent: int = 2,
) -> Path:
    """
    Build a re-identification mapping document and write it to a JSON file.

    The output directory is created automatically if it does not exist.

    Parameters
    ----------
    pre_snapshot:
        Metadata snapshot taken before de-identification.
    post_snapshot:
        Metadata snapshot taken after de-identification.
    uid_keys:
        Tag names to extract from the post snapshot as linkage UIDs.
    output_path:
        Destination path for the JSON file (e.g. "reid/image_001.reid.json").
    source_file:
        Path to the original file. Recorded for audit purposes.
    format_label:
        Human-readable format label (e.g. "DICOM", "TWIX").
    indent:
        JSON indentation level (default 2 for human-readable output).

    Returns
    -------
    The resolved output path.

    Raises
    ------
    ValueError:
        If pre_snapshot, post_snapshot, or uid_keys are invalid.
    OSError:
        If the output file cannot be written.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    document = build_reid_document(
        pre_snapshot=pre_snapshot,
        post_snapshot=post_snapshot,
        uid_keys=uid_keys,
        source_file=source_file,
        format_label=format_label,
    )

    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(document, fh, indent=indent, ensure_ascii=False)

    logger.info("Re-identification document written: %s", output_path)
    return output_path


# ---------------------------------------------------------------------------
# DICOM snapshot helper
# ---------------------------------------------------------------------------

def snapshot_from_pydicom(dataset: Any, include_private: bool = True) -> dict[str, Any]:
    """
    Produce a snapshot dict from a pydicom Dataset.

    Call this on the dataset *before* de-identification (pre-snapshot) and
    *after* de-identification (post-snapshot), then pass both to
    write_reid_json().

    Parameters
    ----------
    dataset:
        A pydicom.Dataset object.
    include_private:
        Whether to include private (vendor) tags in the snapshot.
        Default True so the diff captures private tag removals.

    Returns
    -------
    A flat dict mapping tag keywords (or "(GGGG,EEEE)" for private/unknown
    tags) to their string-serialised values. Sequences are serialised
    recursively as lists of dicts.

    Notes
    -----
    PixelData and other large binary tags are excluded automatically to
    keep the snapshot lightweight.
    """
    _EXCLUDE_KEYWORDS = frozenset({
        "PixelData",
        "FloatPixelData",
        "DoubleFloatPixelData",
        "EncapsulatedDocument",
        "SpectroscopyData",
        "CurveData",
        "OverlayData",
    })

    snapshot: dict[str, Any] = {}

    for elem in dataset:
        # Skip pixel / large binary data
        keyword = elem.keyword or ""
        if keyword in _EXCLUDE_KEYWORDS:
            continue

        # Skip private tags if not requested
        if elem.tag.group % 2 != 0 and not include_private:
            continue

        # Determine the key: keyword if known, else "(GGGG,EEEE)"
        tag_key = keyword if keyword else f"({elem.tag.group:04X},{elem.tag.element:04X})"

        # Serialise sequences recursively
        if elem.VR == "SQ":
            try:
                snapshot[tag_key] = [
                    snapshot_from_pydicom(item, include_private=include_private)
                    for item in elem.value
                ]
            except Exception:  # noqa: BLE001
                snapshot[tag_key] = str(elem.value)
            continue

        # All other tags: serialise value to string
        try:
            val = elem.value
            if isinstance(val, (bytes, bytearray)):
                snapshot[tag_key] = f"<binary {len(val)} bytes>"
            else:
                snapshot[tag_key] = str(val)
        except Exception:  # noqa: BLE001
            snapshot[tag_key] = "<unreadable>"

    return snapshot


# ---------------------------------------------------------------------------
# CLI — standalone usage
# ---------------------------------------------------------------------------

def _cli() -> None:
    """
    Command-line interface for header_reid.py.

    Usage:
        python header_reid.py --pre pre.json --post post.json \\
            --uids SOPInstanceUID StudyInstanceUID SeriesInstanceUID \\
            --output output/image.reid.json \\
            --source input/image.dcm \\
            --format DICOM
    """
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Compute a re-identification mapping document from two metadata "
            "snapshots (pre- and post-de-identification)."
        )
    )
    parser.add_argument(
        "--pre", required=True,
        help="Path to the pre-de-identification snapshot JSON file.",
    )
    parser.add_argument(
        "--post", required=True,
        help="Path to the post-de-identification snapshot JSON file.",
    )
    parser.add_argument(
        "--uids", required=True, nargs="+",
        metavar="UID_KEY",
        help=(
            "One or more tag names to use as linkage UIDs. "
            "Example: SOPInstanceUID StudyInstanceUID SeriesInstanceUID"
        ),
    )
    parser.add_argument(
        "--output", required=True,
        help="Path to write the output re-identification JSON document.",
    )
    parser.add_argument(
        "--source", default=None,
        help="Path to the original source file (recorded for audit).",
    )
    parser.add_argument(
        "--format", default="UNKNOWN", dest="format_label",
        help="Format label recorded in the output (e.g. DICOM, TWIX).",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable DEBUG logging.",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    with open(args.pre, encoding="utf-8") as fh:
        pre_snapshot = json.load(fh)

    with open(args.post, encoding="utf-8") as fh:
        post_snapshot = json.load(fh)

    out = write_reid_json(
        pre_snapshot=pre_snapshot,
        post_snapshot=post_snapshot,
        uid_keys=args.uids,
        output_path=args.output,
        source_file=args.source,
        format_label=args.format_label,
    )
    print(f"Written: {out}")


if __name__ == "__main__":
    _cli()
