# Read-only public dashboard (SPEC sections 9 and 11, task T9).
#
#   python scripts/export_snapshot.py      # first, and commit snapshot/
#   docker build -f deploy/dashboard.Dockerfile -t fairplay-dashboard .
#   docker run -e PORT=8501 -p 8501:8501 fairplay-dashboard
#
# Public mode reads snapshot/ only. walker/, evidence/ and data/ are not copied
# and anthropic is not installed, so the run, approve and export paths cannot
# load and nothing here can use an API key.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FAIRPLAY_MODE=public

WORKDIR /app

COPY requirements.txt /tmp/requirements.txt
RUN grep -E '^(numpy|pandas|scipy|statsmodels|plotly|streamlit|pyyaml)(==|>=)' \
        /tmp/requirements.txt > /tmp/req-dashboard.txt \
    && pip install --no-cache-dir -r /tmp/req-dashboard.txt \
    && rm /tmp/requirements.txt /tmp/req-dashboard.txt

COPY .streamlit/ .streamlit/
COPY prereg.yaml ./
COPY dashboard/ dashboard/
COPY stats/ stats/
COPY snapshot/ snapshot/

RUN useradd --create-home app
USER app

CMD ["sh", "-c", "exec streamlit run dashboard/app.py --server.address 0.0.0.0 --server.port ${PORT:-8501}"]
