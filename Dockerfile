# ocr-watch: überwacht Ordner (Profile), macht PDFs durchsuchbar (OCRmyPDF/Tesseract),
# wandelt Bilder in PDF/A und HEIC in JPG/PNG/WebP um. Weboberfläche + optional MQTT.
# Neu gebaut, sobald das offizielle Basis-Image (python:3-slim) sich ändert.
ARG PY_TAG=3.13-slim
FROM python:${PY_TAG}

ARG PY_TAG
ARG BASE_DIGEST=unbekannt
ARG QUELLE=https://github.com/
LABEL org.opencontainers.image.title="ocr-watch" \
      org.opencontainers.image.description="Überwacht Ordner mit Profilen: PDFs durchsuchbar machen, Bilder zu PDF/A mit OCR, HEIC zu JPG/PNG/WebP – mit Weboberfläche" \
      org.opencontainers.image.source="${QUELLE}" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.base.name="docker.io/library/python:${PY_TAG}" \
      org.opencontainers.image.base.digest="${BASE_DIGEST}"

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      tini ghostscript poppler-utils unpaper pngquant \
      tesseract-ocr tesseract-ocr-osd tesseract-ocr-deu tesseract-ocr-eng \
      tesseract-ocr-fra tesseract-ocr-ita tesseract-ocr-spa tesseract-ocr-nld \
 && rm -rf /var/lib/apt/lists/*

COPY app/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app/ /app/
RUN sed -i 's/\r$//' /app/start.sh && chmod +x /app/start.sh \
 && python -m compileall -q /app/ocrwatch \
 && ocrmypdf --version && tesseract --version | head -1

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    DATA_ROOT=/data \
    CONFIG_DIR=/config \
    UI_PORT=8080 \
    DB_TYPE=sqlite \
    PUID=99 PGID=100 UMASK=0002 \
    OMP_THREAD_LIMIT=1 \
    TZ=Europe/Berlin
WORKDIR /app
VOLUME ["/config"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/gesund' % os.environ.get('UI_PORT','8080'), timeout=4)" || exit 1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/app/start.sh"]
