FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

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
