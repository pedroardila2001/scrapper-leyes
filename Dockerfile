FROM python:3.12-slim

# Install system dependencies (e.g. for docling if needed, though we fallback if missing)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Upgrade pip and install hatchling
RUN pip install --no-cache-dir --upgrade pip hatchling

# Copy the project descriptor and source
COPY pyproject.toml .
COPY src/ ./src/

# Install the package in editable mode. Set INSTALL_INGEST=true to also pull the
# heavy ingestion extra (docling + torch) — used by the `pipeline` service that
# scrapes / re-parses sentencias. The API image stays lean (INSTALL_INGEST=false).
ARG INSTALL_INGEST=false
RUN pip install --no-cache-dir -e . \
    && if [ "$INSTALL_INGEST" = "true" ]; then \
         pip install --no-cache-dir -e ".[ingest]"; \
       fi

# Expose port for FastAPI
EXPOSE 8000

# Run uvicorn server
CMD ["uvicorn", "scrapper_leyes.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
