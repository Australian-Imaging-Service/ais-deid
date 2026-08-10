# AIS-deid

AIS-deid is a package that will evolve to include deidentification of multiple imaging file types.
Current version: 0.1.0
Data types covered: DICOM only.

## Note the CLI command is different from the PyPI package name:

> Package installs as `ais-deid`, but exposes its command-line tool as `dicom-deid`
> (the DICOM-specific tool within this package — more domains may be added
> under the `ais-deid` name in future).

i.e. 
| Use                                         | Name         |
|---------------------------------------------|--------------|
| PyPI package (what you pip-install)         | `ais-deid`   |
| Import package (what is imported in python) | `ais-deid`   |
| CLI command (what to write in the terminal  | `dicom-deid` |


# dicom-deid

De-identify DICOM metadata using [pydicom/deid](https://pydicom.github.io/deid/).

---

## Features

- Deterministic pseudonymisation — same patient maps to same anonymous ID across runs
- Full DICOM hierarchy preservation (patient → study → series → image)
- Private tag and sequence stripping
- Configurable date jittering
- Post-process validation command to audit output for residual PHI
- Click-based CLI with environment variable support
- Kubernetes-ready Docker image

---

## Quickstart

### 1. Install

```bash
pip install -e ".[dev]"
```

### 2. Set required environment variables

```bash
# Generate a secure salt (do this once per site; store it in a secrets manager)
export DEID_SALT=$(python -c "import secrets; print(secrets.token_hex(32))")

# Optional: shift dates by N days (default 0)
export DEID_DATE_JITTER=-14
```

> **Security:** `DEID_SALT` must never be hardcoded in source or committed to git.
> Use a secrets manager (AWS Secrets Manager, HashiCorp Vault, Kubernetes Secrets).

### 3. Run

```bash
dicom-deid process \
    --input  /data/raw_dicoms \
    --output /data/deid_dicoms \
    --recipe recipe.dicom
```

### 4. Validate output

```bash
dicom-deid validate --input /data/deid_dicoms
```

---

## CLI Reference

```
dicom-deid [--verbose] [--version] COMMAND [OPTIONS]

Commands:
  process   De-identify all DICOM files under --input.
  validate  Audit de-identified output for residual PHI tags.

process options:
  --input   -i  PATH   Input directory (required)
  --output  -o  PATH   Output directory (required)
  --recipe  -r  PATH   deid recipe file [env: DEID_RECIPE] [default: recipe.dicom]
  --date-jitter  INT   Days to shift dates [env: DEID_DATE_JITTER]
  --glob    TEXT       File glob pattern [default: **/*.dcm]
  --no-remove-private  Do not strip private tags (not recommended)
  --no-strip-sequences Do not strip sequence tags (not recommended)
```

---

## Recipe customisation

Edit `recipe.dicom` to match your site's requirements. The deid recipe language supports:

| Action    | Meaning                                      |
|-----------|----------------------------------------------|
| `ADD`     | Add a tag with a fixed value                 |
| `REPLACE` | Replace a tag value (supports `var:` / `func:`) |
| `BLANK`   | Set tag value to empty string                |
| `REMOVE`  | Delete the tag entirely                      |
| `JITTER`  | Shift a date tag by N days                   |
| `KEEP`    | Explicitly keep a tag unchanged              |

Field expanders:
- `contains:Name` — all tags whose keyword contains "Name"
- `startswith:Patient` — all tags starting with "Patient"
- `endswith:Date` — all tags ending with "Date"

Custom Python replacement functions are registered in `dicom_deid/transforms.py`
and referenced in the recipe with `func:dicom_deid.transforms.function_name`.

---

## Development

### Setup

```bash
pip install -e ".[dev]"
pre-commit install
```

### Run tests

```bash
export DEID_SALT="test_salt_local_dev_only"
pytest
```

### Run linting manually

```bash
pre-commit run --all-files
```

---

## Docker

```bash
# Build
docker build -t dicom-deid:latest .

# Run
docker run --rm \
  -e DEID_SALT="$DEID_SALT" \
  -v /data/raw:/input:ro \
  -v /data/deid:/output \
  -v $(pwd)/recipe.dicom:/config/recipe.dicom:ro \
  dicom-deid:latest \
  process --input /input --output /output --recipe /config/recipe.dicom
```

---

## Kubernetes

```bash
# Create the salt secret
kubectl create secret generic dicom-deid-secrets \
  --from-literal=deid-salt="$DEID_SALT"

# Load the recipe as a ConfigMap
kubectl create configmap dicom-deid-recipe \
  --from-file=recipe.dicom=recipe.dicom

# Submit the job
kubectl apply -f kubernetes/job.yaml

# Watch progress
kubectl logs -f job/dicom-deid
```

---

## Security notes

- `DEID_SALT` is a site secret. Anyone with the salt can reverse pseudonymisation
  for known patient IDs. Treat it like a password.
- The default recipe follows DICOM PS 3.15 Annex E (Basic Application Level
  Confidentiality Profile) but **does not constitute a legal guarantee** of
  de-identification. Validate with your institution's ethics/IRB process.
- Private tags (`remove_private=True`) and nested sequences (`strip_sequences=True`)
  are stripped by default. Only disable these if you have a specific reason.

## File overview
- pyproject.toml
Th project's packaging, dependencies, and tooling configuration. Declares the package name, version, and Python requirement (≥3.10). Lists runtime dependencies (deid, pydicom, click, rich) and dev dependencies (pytest, pytest-cov, flake8, pre-commit). Registers the dicom-deid shell command as an entry point so it's available system-wide after pip install. Also configures pytest's test paths and coverage settings.
- recipe.dicom
The de-identification rule file consumed by the deid library. Written in deid's plain-text recipe language, it defines an action for every category of PHI tag: patient identity (REPLACE/BLANK/REMOVE), dates (JITTER), times (BLANK), physician/operator/institution names (REMOVE), device identifiers (REMOVE), request/order fields (REMOVE), protocol descriptions (BLANK), secondary UIDs (REMOVE), sequences (REMOVE), and free-text comment fields (BLANK). Uses var: placeholders for values that are computed at runtime (e.g. var:anon_patient_id), which are injected by the engine. Field expanders like endswith:Date and contains:PhysicianName apply rules to whole groups of tags with a single line. This is the primary file to edit when adjusting what gets removed or changed.
- Dockerfile
A multi-stage Docker build. Stage 1 (builder) installs hatch and builds a wheel from the source. Stage 2 (runtime) is a minimal python:3.11-slim image that installs only the pre-built wheel — no build tools in the final image. Creates a non-root deid user for security, mounts /input and /output as working directories, and sets ENTRYPOINT ["dicom-deid"] so the container is used directly as a CLI tool. Secrets (DEID_SALT) are deliberately not baked in and must be injected at runtime.
- .flake8
Linting configuration. Sets max line length to 100, suppresses two common false-positive rules (E203, W503), excludes build/cache directories, and allows longer lines in test files where fixture data is verbose.
- .pre-commit-config.yaml
Defines Git pre-commit hooks that run automatically before every commit. Includes: trailing-whitespace, end-of-file-fixer, check-yaml, check-merge-conflict, and debug-statements from the pre-commit standard library; flake8 with flake8-bugbear for linting; and codespell for spell-checking. This is what prevents issues like the hardcoded salt or wrong API calls from ever reaching the main branch.
- .codespell-ignorewords
A suppression list for the codespell hook. Contains medical/DICOM terms that spell-checkers incorrectly flag as misspellings (dicom, deid, anonymise, anonymisation, etc.).

dicom_deid
- __init__.py
Minimal package initialiser. Declares __version__ = "0.1.0", which is imported by the CLI for --version output and by pyproject.toml as the authoritative version string.
- transforms.py
Pure functions that compute replacement values for DICOM tags. Manages the DEID_SALT secret: reads it from the environment at import time and exposes _require_salt() which raises a clear RuntimeError if it's missing. _hash() performs salted SHA-256 pseudonymisation (24 hex chars, 96 bits — deterministic, so the same patient always gets the same anonymous ID). hash_patient_id() hashes PatientID. hash_accession_number() hashes AccessionNumber with a field-name prefix so the same raw value produces a different hash for different tag types — preventing cross-linkage. Also provides passthrough() (returns value unchanged) and blank_if_present() (returns None to blank a tag while keeping it present). All functions follow the deid func: signature (item, value, field, dicom) -> str | None.
- engine.py
The core orchestration class. Defines two result dataclasses: FileResult (records success/failure and error message for one file) and RunResult (aggregates all results, exposes .successes, .failures, and .summary()). The DEFAULT_VARIABLE_BUILDERS dict maps each var: name in the recipe to a callable that derives the value from the DICOM dataset — this is how hashed IDs and the date jitter get passed to the recipe. DeidEngine.__init__() loads the recipe, merges any caller-supplied variable builders over the defaults, and eagerly validates that DEID_SALT is available so misconfiguration is caught immediately. process_file() implements the correct four-step deid API (construct parser with recipe, define vars, parse, save) with full try/except so one bad file never aborts a batch. process_directory() uses rglob with relative_to() to preserve the full patient/study/series directory hierarchy in the output.
- cli.py
The user-facing command-line interface, built with Click. The root main group provides --verbose (toggles DEBUG logging) and --version. Two subcommands: process runs de-identification — it accepts --input, --output, --recipe (also via DEID_RECIPE env var), --date-jitter (also via DEID_DATE_JITTER), --glob, --no-remove-private, and --no-strip-sequences; after running it prints a rich colour-coded summary table per file and exits with code 1 if any failures occurred. validate audits a de-identified output directory — checks that high-risk tags (InstitutionName, ReferringPhysicianName, OperatorsName, etc.) are absent or empty, that PatientName doesn't look like a real name, and that PatientIdentityRemoved=YES is set; prints a failure table and exits with code 1 if issues are found.

---

## License

Apache 2.0
