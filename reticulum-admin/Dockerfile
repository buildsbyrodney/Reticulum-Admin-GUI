FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py reticulum_adapter.py ./
COPY templates ./templates
COPY static ./static

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
# One worker is deliberate for a resource-constrained Raspberry Pi 3.  Its 110s
# request budget is outside the 90s agent worker and 100s internal HTTP bounds.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--timeout", "110", "--graceful-timeout", "10", "--access-logfile", "-", "app:app"]
