FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py reticulum_adapter.py ./
COPY templates ./templates
COPY static ./static

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--access-logfile", "-", "app:app"]
