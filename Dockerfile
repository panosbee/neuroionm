FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-ell \
    && rm -rf /var/lib/apt/lists/*

RUN addgroup --system ionm && adduser --system --ingroup ionm ionm

COPY requirements.txt ./
COPY pytest.ini ./
RUN python -m pip install --upgrade pip && python -m pip install -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend
COPY body.jpg ./body.jpg
COPY bodymap-asset.json ./bodymap-asset.json
COPY ionm-coords.json ./ionm-coords.json
COPY ionm-reference.html ./ionm-reference.html
COPY tools ./tools
COPY tests ./tests

RUN mkdir -p /app/data/admin_knowledge && chown -R ionm:ionm /app/data

USER ionm

EXPOSE 8000

CMD ["gunicorn", "backend.app.main:app", "--worker-class", "uvicorn.workers.UvicornWorker", "--workers", "2", "--bind", "0.0.0.0:8000", "--access-logfile", "-", "--error-logfile", "-"]
