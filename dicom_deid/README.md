# dicom-deid

De-identify DICOM metadata using [pydicom/deid](https://pydicom.github.io/deid/).

[![CI/CD](https://github.com/your-org/dicom-deid/actions/workflows/ci-cd.yml/badge.svg)](https://github.com/your-org/dicom-deid/actions)
[![codecov](https://codecov.io/gh/your-org/dicom-deid/badge.svg)](https://codecov.io/gh/your-org/dicom-deid)

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

---

## License

Apache 2.0
