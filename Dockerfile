# ─────────────────────────────────────────────────────────────────────────────
# Scholaris — Dockerfile
# Base: python:3.11-slim  (Debian Bullseye)
# Includes: Tesseract OCR, poppler-utils (pdf2image), OpenCV runtime libs,
#           and all Python ML dependencies via pip.
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim AS base

# ── System dependencies ───────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Tesseract OCR
    tesseract-ocr \
    tesseract-ocr-eng \
    # pdf → image conversion (pdf2image)
    poppler-utils \
    # OpenCV shared libs
    libglib2.0-0 \
    libsm6 \
    libxrender1 \
    libxext6 \
    libgl1 \
    # PostgreSQL client libs (psycopg2-binary needs these at runtime)
    libpq5 \
    # Audio processing libs (PyAudio requires portaudio)
    portaudio19-dev \
    python3-dev \
    # Misc build/runtime tools
    wget curl gcc g++ git \
    && rm -rf /var/lib/apt/lists/*

# ── Python environment ────────────────────────────────────────────────────────
WORKDIR /app

# Copy requirements first so Docker layer-cache skips pip install on code changes
COPY requirements.txt .

# Upgrade pip then install all deps
# Note: torch CPU-only wheel is much smaller — swap index-url if GPU is needed
RUN pip install --upgrade pip && \
    pip install --no-cache-dir \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        -r requirements.txt

# ── Application code ──────────────────────────────────────────────────────────
COPY . .

# Create upload directory expected by the app
RUN mkdir -p /app/static/uploads

# ── Runtime config ────────────────────────────────────────────────────────────
# All secrets/config are injected via environment variables at runtime.
# Do NOT embed .env in the image.

EXPOSE 5000

# Run with Gunicorn (already in requirements.txt)
# --workers: 2 × CPU + 1 is the standard formula; 3 is safe for a t3.small
CMD ["gunicorn", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "2", \
     "--timeout", "3600", \
     "--log-level", "info", \
     "app:app"]
