# ── Stage 1: builder ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build tooling only in the builder stage
RUN pip install --no-cache-dir hatch

COPY pyproject.toml README.md ./
COPY dicom_deid/ ./dicom_deid/

RUN hatch build --target wheel


# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="dicom-deid"
LABEL org.opencontainers.image.description="DICOM de-identification using pydicom/deid"
LABEL org.opencontainers.image.licenses="Apache-2.0"

# Non-root user for security
RUN useradd --create-home --shell /bin/bash deid
WORKDIR /home/deid

# Copy wheel from builder and install (no build tools in final image)
COPY --from=builder /build/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl

# Default mount points match the Kubernetes YAML below
RUN mkdir -p /input /output && chown -R deid:deid /input /output

USER deid

# Runtime environment — DEID_SALT and DEID_RECIPE must be injected at runtime
# via Kubernetes Secret / Docker --env. Never bake secrets into the image.
ENV DEID_DATE_JITTER=0

ENTRYPOINT ["dicom-deid"]
CMD ["--help"]
