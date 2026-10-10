FROM python:3.12-slim
ARG RNS_VERSION=1.5.7
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt "rns==${RNS_VERSION}"
COPY management.py configuration.py server.py ./
# 90s allows one 35s apply check plus a bounded 35s rollback check. It remains
# inside the web-to-agent request budget. One worker is deliberate for Pi 3.
CMD ["gunicorn", "--bind", "0.0.0.0:8081", "--workers", "1", "--timeout", "90", "--graceful-timeout", "10", "server:app"]
