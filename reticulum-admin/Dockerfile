FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py reticulum_adapter.py ./
COPY templates ./templates
COPY static ./static

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
# One worker is deliberate for a resource-constrained Raspberry Pi 3. Its 195s
# request budget is outside the 170s agent worker and 180s internal HTTP bounds.
# Keep one Pi-friendly worker, but allow a second request thread. A browser can
# leave an incomplete keep-alive connection open; with a single sync worker
# that socket otherwise blocks a real configuration Apply request until the
# Gunicorn timeout. The management operation remains bounded below 195 seconds.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--worker-class", "gthread", "--threads", "2", "--workers", "1", "--keep-alive", "2", "--timeout", "195", "--graceful-timeout", "10", "--access-logfile", "-", "app:app"]
