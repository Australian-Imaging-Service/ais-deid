"""
engine.py
---------
Core de-identification engine.

Key corrections over Edge_De-id's engine.py:
- Recipe is passed to DicomParser at construction (not via nonexistent apply())
- Variables are defined via parser.define() before parse()
- Private tags stripped via remove_private=True
- Sequences stripped via strip_sequences=True
- Per-file exception handling so one bad file never aborts the run
- Structured logging throughout
- Result dataclass carries per-file success/failure for downstream reporting
"""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pydicom
from deid.config import DeidRecipe
from deid.dicom.parser import DicomParser

from .transforms import hash_patient_id, hash_accession_number

import importlib.util

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class FileResult:
    """Outcome of processing a single DICOM file."""
    input_path: Path
    output_path: Path
    success: bool
    error: str | None = None
    reid_path: Path | None = None


@dataclass
class RunResult:
    """Aggregate result for a directory run."""
    results: list[FileResult] = field(default_factory=list)

    @property
    def successes(self) -> list[FileResult]:
        return [r for r in self.results if r.success]

    @property
    def failures(self) -> list[FileResult]:
        return [r for r in self.results if not r.success]

    def summary(self) -> str:
        return (
            f"{len(self.successes)} succeeded, "
            f"{len(self.failures)} failed "
            f"out of {len(self.results)} total files"
        )


# ---------------------------------------------------------------------------
# Variable builder
# ---------------------------------------------------------------------------
# This dict maps recipe `var:` names to callables that derive the value from
# the DICOM dataset. Add entries here when you add new `var:` references to
# your recipe file — keeps the engine and recipe in sync.
# ---------------------------------------------------------------------------

VariableBuilder = Callable[[pydicom.Dataset], str | int]

DEFAULT_VARIABLE_BUILDERS: dict[str, VariableBuilder] = {
    "anon_patient_id": lambda ds: hash_patient_id(
        None, str(ds.get("PatientID", "")), "PatientID", ds
    ),
    "anon_accession_number": lambda ds: hash_accession_number(
        None, str(ds.get("AccessionNumber", "")), "AccessionNumber", ds
    ),
    "anon_study_id": lambda ds: hash_accession_number(
        None, str(ds.get("StudyID", "")), "StudyID", ds
    ),
    # date_jitter: zero shift by default; override with a per-site value
    "date_jitter": lambda _ds: int(os.environ.get("DEID_DATE_JITTER", "0")),
    "anon_patient_name": lambda _ds: "ANONYMOUS",
}


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class DeidEngine:
    """
    Orchestrates DICOM de-identification using a deid recipe.

    Parameters
    ----------
    recipe_path:
        Path to the deid recipe file (e.g. ``recipe.dicom``).
    variable_builders:
        Mapping of recipe ``var:`` names to callables that receive the pydicom
        Dataset and return the substitution value. Merged over the defaults.
    strip_sequences:
        Strip nested sequence tags (recommended for thorough de-id).
    remove_private:
        Remove vendor-specific private tags (recommended).
    """

    def __init__(
        self,
        recipe_path: str | Path,
        variable_builders: dict[str, VariableBuilder] | None = None,
        strip_sequences: bool = True,
        remove_private: bool = True,
        capture_headers: bool = False,
        header_output_dir: Path | None = None,
    ) -> None:
        self.recipe = DeidRecipe(str(recipe_path))
        self.strip_sequences = strip_sequences
        self.remove_private = remove_private
        self.capture_headers = capture_headers
        self.header_output_dir = header_output_dir

        # Merge caller-supplied builders over defaults
        self._variable_builders: dict[str, VariableBuilder] = {
            **DEFAULT_VARIABLE_BUILDERS,
            **(variable_builders or {}),
        }

        logger.info(
            "DeidEngine initialised | recipe=%s strip_sequences=%s remove_private=%s",
            recipe_path,
            strip_sequences,
            remove_private,
        )

        # Eagerly validate that the salt is available so failures are caught
        # at startup, not silently per-file at runtime.
        # Salt validation — CURRENTLY DISABLED
        # TO RE-ENABLE: uncomment the two lines below once DEID_SALT is configured
        # in your environment. See transforms.py for full instructions.
        #from .transforms import _require_salt
        #_require_salt()

    # ------------------------------------------------------------------
    # Single-file processing
    # ------------------------------------------------------------------

    def process_file(self, infile: str | Path, outfile: str | Path) -> FileResult:
        """
        De-identify a single DICOM file.

        The output directory is created if it does not exist.
        Returns a FileResult; never raises — exceptions are caught and recorded.
        Returns a mapping JSON.
        """
        infile = Path(infile)
        outfile = Path(outfile)

        try:
            outfile.parent.mkdir(parents=True, exist_ok=True)

            # ── STEP 1: Construct parser with recipe bound at creation ──────
            parser = DicomParser(str(infile), recipe=self.recipe)

            # Capture pre-de-identification snapshot if requested
            pre_snapshot = None
            if self.capture_headers:
                from header_reid import snapshot_from_pydicom, write_reid_json
                pre_snapshot = snapshot_from_pydicom(parser.dicom)
                
            # ── STEP 2: Define var: substitutions before parse() ────────────
            # Any `var:name` reference in the recipe must be defined here,
            # otherwise deid silently skips the action.
            ds = parser.dicom
            for var_name, builder in self._variable_builders.items():
                try:
                    value = builder(ds)
                    parser.define(var_name, value)
                    logger.debug("Defined var:%s = %r", var_name, value)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Variable builder for '%s' raised %s: %s — skipping",
                        var_name, type(exc).__name__, exc,
                    )

            # ── STEP 3: Parse applies all recipe actions ─────────────────────
            parser.parse(
                strip_sequences=self.strip_sequences,
                remove_private=self.remove_private,
            )

            # ── STEP 4: Save and Make Mapping JSON ───────────────────────────
            parser.save(str(outfile))
            
            # Write re-identification mapping document if capture is enabled
            reid_path = None
            if self.capture_headers and pre_snapshot is not None:
                post_ds = pydicom.dcmread(str(outfile), stop_before_pixels=True)
                post_snapshot = snapshot_from_pydicom(post_ds)
                reid_dir = self.header_output_dir or outfile.parent
                reid_path = reid_dir / (outfile.stem + ".reid.json")
                write_reid_json(
                    pre_snapshot=pre_snapshot,
                    post_snapshot=post_snapshot,
                    uid_keys=["SOPInstanceUID", "StudyInstanceUID", "SeriesInstanceUID"],
                    output_path=reid_path,
                    source_file=str(infile),
                    format_label="DICOM",
                )
            logger.info("De-identified: %s → %s", infile.name, outfile)
            return FileResult(infile, outfile, success=True, reid_path=reid_path)

        except Exception as exc:  # noqa: BLE001
            msg = f"{type(exc).__name__}: {exc}"
            logger.error("Failed to process %s — %s", infile, msg)
            return FileResult(infile, outfile, success=False, error=msg)

    # ------------------------------------------------------------------
    # Directory processing
    # ------------------------------------------------------------------

    def process_directory(
        self,
        input_dir: str | Path,
        output_dir: str | Path,
        glob: str = "**/*.dcm",
    ) -> RunResult:
        """
        Recursively de-identify all DICOM files under ``input_dir``.

        Preserves the full subdirectory hierarchy under ``output_dir``
        (Edge_De-id gets this right; the original standalone script did not).

        Parameters
        ----------
        input_dir:  Root of the input tree.
        output_dir: Root of the output tree.
        glob:       Pattern for matching files (default ``**/*.dcm``).
        """
        input_dir = Path(input_dir)
        output_dir = Path(output_dir)

        if not input_dir.is_dir():
            raise ValueError(f"input_dir does not exist or is not a directory: {input_dir}")

        output_dir.mkdir(parents=True, exist_ok=True)

        files = sorted(input_dir.glob(glob))
        if not files:
            logger.warning("No files matched '%s' under %s", glob, input_dir)

        run = RunResult()
        for dicom_file in files:
            # Preserve patient/study/series directory structure
            relative = dicom_file.relative_to(input_dir)
            out_file = output_dir / relative
            result = self.process_file(dicom_file, out_file)
            run.results.append(result)

        logger.info("Run complete: %s", run.summary())
        return run
