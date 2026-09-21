FROM python:3.11-slim

WORKDIR /app

RUN useradd --create-home --uid 1000 app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY mind_integration/ ./mind_integration/

# Nessun segreto nell'immagine (§55.3): la configurazione arriva da variabili
# d'ambiente (env_file / secret manager a runtime), mai da un .env copiato qui.
RUN mkdir -p /app/data /app/logs && chown -R app:app /app
USER app

EXPOSE 8003

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8003/health/live', timeout=3)" || exit 1

CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8003"]
