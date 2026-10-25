# Hosted copy of Door Run (SPEC sections 4, 5 and 11, task T9).
#
#   docker build -f deploy/game.Dockerfile -t fairplay-game .
#   docker run -e PORT=8000 -e PLANTED_SWITCH=on -p 8000:8000 fairplay-game
#
# Copies game/ only: no walker, no anthropic, no API key. PLANTED_SWITCH comes
# from the host's environment (the server defaults to off). Oracle lines go to
# stdout, i.e. the host's private logs, never to a file a route could serve.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ORACLE_PATH=/dev/stdout

WORKDIR /app

# Install only this service's pins, taken from the one reviewed requirements.txt (S13).
COPY requirements.txt /tmp/requirements.txt
RUN grep -E '^(fastapi|uvicorn)==' /tmp/requirements.txt > /tmp/req-game.txt \
    && pip install --no-cache-dir -r /tmp/req-game.txt \
    && rm /tmp/requirements.txt /tmp/req-game.txt

COPY game/ game/

RUN useradd --create-home app
USER app

# One worker: game state lives in memory. --proxy-headers lets the per-IP rate
# limit see each client's address instead of the host's proxy.
CMD ["sh", "-c", "exec uvicorn game.server:app --workers 1 --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
