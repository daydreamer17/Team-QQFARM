FROM python:3.11-slim

ARG MICROMAMBA_VERSION=2.8.1-0
ARG MICROMAMBA_SHA256_AMD64=9689782d863c05a1bf5d2d371ba527104e7a4eb4310c1637d8653b751aed9c82
ARG MICROMAMBA_SHA256_ARM64=e5ba23b5945aa49dfd11022e592a510d2686a8feee810e00140b73c9fdf0ba2a
ARG TARGETARCH

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/ocr-tools/bin:${PATH}" \
    TESSDATA_PREFIX=/opt/ocr-tools/share/tessdata

WORKDIR /app

COPY environment-ocr-tools.yml ./
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl \
    && case "${TARGETARCH}" in \
        amd64) MICROMAMBA_ARCH=64; MICROMAMBA_SHA256="${MICROMAMBA_SHA256_AMD64}" ;; \
        arm64) MICROMAMBA_ARCH=aarch64; MICROMAMBA_SHA256="${MICROMAMBA_SHA256_ARM64}" ;; \
        *) echo "Unsupported Docker architecture: ${TARGETARCH}" >&2; exit 1 ;; \
       esac \
    && curl --fail --location --show-error \
        "https://github.com/mamba-org/micromamba-releases/releases/download/${MICROMAMBA_VERSION}/micromamba-linux-${MICROMAMBA_ARCH}" \
        --output /usr/local/bin/micromamba \
    && echo "${MICROMAMBA_SHA256}  /usr/local/bin/micromamba" | sha256sum --check --strict \
    && chmod 0755 /usr/local/bin/micromamba \
    && micromamba create --yes --prefix /opt/ocr-tools --file environment-ocr-tools.yml \
    && micromamba clean --all --yes \
    && rm -f environment-ocr-tools.yml \
    && rm -rf /var/lib/apt/lists/* \
    && tesseract --version 2>&1 | grep -Fqx "tesseract 5.5.1"

COPY pyproject.toml ./
COPY src ./src
COPY data/contracts ./data/contracts
COPY data/policies ./data/policies
COPY data/examples/policy_rag ./data/examples/policy_rag
COPY evaluation/reference/policy_rag ./evaluation/reference/policy_rag
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir .

COPY alembic.ini ./
COPY migrations ./migrations
COPY data/generated/supplier_history/mcu9 ./data/generated/supplier_history/mcu9

RUN useradd --create-home --uid 10001 supplier \
    && mkdir -p /var/lib/supplier-comparison/quotes /var/lib/supplier-comparison/policy-uploads \
    && chown -R supplier:supplier /var/lib/supplier-comparison
USER supplier

EXPOSE 8000
CMD ["uvicorn", "supplier_comparison.backend.api:app", "--host", "0.0.0.0", "--port", "8000"]
