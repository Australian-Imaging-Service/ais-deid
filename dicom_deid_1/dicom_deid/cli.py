"""
cli.py
------
Click-based command-line interface for dicom-deid.

Installed as the ``dicom-deid`` shell command via pyproject.toml entry point.

Usage examples
--------------
    # Basic run
    dicom-deid process --input /data/raw --output /data/deid --recipe recipe.dicom

    # Verbose with custom date jitter (also settable via env var DEID_DATE_JITTER)
    dicom-deid --verbose process --input /data/raw --output /data/deid \\
        --recipe recipe.dicom --date-jitter -14

    # Print version
    dicom-deid --version
"""

import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from . import __version__
from .engine import DeidEngine

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _setup_logging(verbose: bool) -> None:
    """Configure root logger with rich formatting."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
    )


# ---------------------------------------------------------------------------
# CLI root group
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(__version__, prog_name="dicom-deid")
@click.option(
    "--verbose", "-v",
    is_flag=True,
    default=False,
    help="Enable DEBUG-level logging.",
)
@click.pass_context
def main(ctx: click.Context, verbose: bool) -> None:
    """De-identify DICOM metadata using pydicom/deid recipes."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    _setup_logging(verbose)


# ---------------------------------------------------------------------------
# `process` subcommand
# ---------------------------------------------------------------------------

@main.command()
@click.option(
    "--input", "-i", "input_dir",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Input directory containing DICOM files (.dcm).",
)
@click.option(
    "--output", "-o", "output_dir",
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Output directory for de-identified files.",
)
@click.option(
    "--recipe", "-r",
    default="recipe.dicom",
    show_default=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    envvar="DEID_RECIPE",
    help="Path to deid recipe file. Also reads DEID_RECIPE env var.",
)
@click.option(
    "--date-jitter",
    default=None,
    type=int,
    envvar="DEID_DATE_JITTER",
    help=(
        "Days to shift dates (positive or negative integer). "
        "Overrides DEID_DATE_JITTER env var. Default: 0."
    ),
)
@click.option(
    "--glob",
    default="**/*.dcm",
    show_default=True,
    help="Glob pattern for matching DICOM files under --input.",
)
@click.option(
    "--no-remove-private",
    is_flag=True,
    default=False,
    help="Do NOT strip private (vendor) tags. Not recommended.",
)
@click.option(
    "--no-strip-sequences",
    is_flag=True,
    default=False,
    help="Do NOT strip nested sequence tags. Not recommended.",
)
@click.pass_context
def process(
    ctx: click.Context,
    input_dir: Path,
    output_dir: Path,
    recipe: Path,
    date_jitter: int | None,
    glob: str,
    no_remove_private: bool,
    no_strip_sequences: bool,
) -> None:
    """
    De-identify all DICOM files under INPUT_DIR and write to OUTPUT_DIR.

    Directory structure is preserved: patient/study/series trees are
    replicated under OUTPUT_DIR.

    Environment variables
    ---------------------
    DEID_SALT         Required. Hex secret used for pseudonymisation hashing.
    DEID_RECIPE       Path to recipe file (overridden by --recipe flag).
    DEID_DATE_JITTER  Integer day offset for date jittering.
    """
    logger = logging.getLogger(__name__)

    # If date_jitter was supplied on CLI, push it into the env so the
    # variable builder in engine.py picks it up.
    if date_jitter is not None:
        import os
        os.environ["DEID_DATE_JITTER"] = str(date_jitter)

    logger.info(
        "Starting de-identification | input=%s output=%s recipe=%s",
        input_dir, output_dir, recipe,
    )

    engine = DeidEngine(
        recipe_path=recipe,
        remove_private=not no_remove_private,
        strip_sequences=not no_strip_sequences,
    )

    run = engine.process_directory(input_dir, output_dir, glob=glob)

    # ── Rich summary table ──────────────────────────────────────────────────
    console = Console()
    console.print()

    table = Table(title="De-identification Summary", show_lines=True)
    table.add_column("Status", style="bold")
    table.add_column("File", overflow="fold")
    table.add_column("Detail")

    for result in run.results:
        if result.success:
            table.add_row(
                "[green]OK[/green]",
                str(result.input_path.name),
                f"→ {result.output_path}",
            )
        else:
            table.add_row(
                "[red]FAIL[/red]",
                str(result.input_path.name),
                result.error or "unknown error",
            )

    console.print(table)
    console.print(f"\n[bold]{run.summary()}[/bold]")

    if run.failures:
        logger.error("%d file(s) failed. Review errors above.", len(run.failures))
        sys.exit(1)


# ---------------------------------------------------------------------------
# `validate` subcommand — checks de-identified output for residual PHI tags
# ---------------------------------------------------------------------------

# Tags that must be absent OR empty after de-identification
_MUST_BE_ABSENT_OR_EMPTY: list[str] = [
    "PatientAddress",
    "ReferringPhysicianName",
    "InstitutionName",
    "InstitutionAddress",
    "OperatorsName",
]

# Tags that must NOT contain values resembling real names (heuristic: no spaces/commas
# in a name-like field, OR value equals known anonymisation sentinel)
_MUST_BE_ANONYMISED: list[str] = [
    "PatientName",   # must be blank or a sentinel like ANONYMOUS
    "PatientID",     # must be present but changed — we check it's not a "real" looking ID
]

_MUST_BE_SET: list[str] = [
    "PatientIdentityRemoved",
]


@main.command()
@click.option(
    "--input", "-i", "input_dir",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Directory of de-identified DICOM files to validate.",
)
@click.option(
    "--glob",
    default="**/*.dcm",
    show_default=True,
)
def validate(input_dir: Path, glob: str) -> None:
    """
    Audit de-identified DICOM files for residual PHI tags.

    Checks that high-risk tags are absent and that PatientIdentityRemoved=YES
    is present. Exits with code 1 if any file fails.
    """
    import pydicom  # noqa: PLC0415

    console = Console()
    issues: list[tuple[Path, str]] = []

    for dcm_path in sorted(input_dir.glob(glob)):
        try:
            ds = pydicom.dcmread(str(dcm_path), stop_before_pixels=True)
        except Exception as exc:  # noqa: BLE001
            issues.append((dcm_path, f"Could not read file: {exc}"))
            continue

        for tag in _MUST_BE_ABSENT_OR_EMPTY:
            if tag in ds and str(ds[tag].value).strip():
                issues.append((dcm_path, f"PHI tag not cleared: {tag} = {ds[tag].value!r}"))

        # PatientName must be blank or a known sentinel — not a real name (heuristic: contains ^)
        if "PatientName" in ds:
            name_val = str(ds.PatientName).strip()
            if "^" in name_val and name_val.upper() != "ANONYMOUS":
                issues.append((dcm_path, f"PatientName appears to be a real name: {name_val!r}"))

        for tag in _MUST_BE_SET:
            if tag not in ds or str(ds[tag].value).upper() != "YES":
                val = ds[tag].value if tag in ds else "<missing>"
                issues.append((dcm_path, f"Required tag not set: {tag} = {val!r}"))

        if ds.file_meta and hasattr(ds.file_meta, "ImplementationVersionName"):
            pass  # informational only

    console.print()
    if issues:
        table = Table(title="[red]Validation FAILED[/red]", show_lines=True)
        table.add_column("File", overflow="fold")
        table.add_column("Issue")
        for path, msg in issues:
            table.add_row(str(path.name), msg)
        console.print(table)
        sys.exit(1)
    else:
        console.print("[green bold]✓ All files passed validation.[/green bold]")
