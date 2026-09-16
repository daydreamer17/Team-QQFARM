FROM python:3.11-slim

ARG MICROMAMBA_VERSION=2.8.1-0
ARG MICROMAMBA_SHA256=9689782d863c05a1bf5d2d371ba527104e7a4eb4310c1637d8653b751aed9c82

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/ocr-tools/bin:${PATH}" \
    TESSDATA_PREFIX=/opt/ocr-tools/share/tessdata

WORKDIR /app

COPY environment-ocr-tools.yml ./
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl \
    && curl --fail --location --show-error \
        "https://github.com/mamba-org/micromamba-releases/releases/download/${MICROMAMBA_VERSION}/micromamba-linux-64" \
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
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir .

COPY alembic.ini ./
COPY migrations ./migrations

RUN useradd --create-home --uid 10001 supplier \
    && mkdir -p /var/lib/supplier-comparison/quotes \
    && chown -R supplier:supplier /var/lib/supplier-comparison
USER supplier

EXPOSE 8000
CMD ["uvicorn", "supplier_comparison.backend.api:app", "--host", "0.0.0.0", "--port", "8000"]
