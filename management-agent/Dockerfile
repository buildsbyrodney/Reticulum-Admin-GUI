FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY management.py configuration.py features.py server.py ./
# An apply and its safety rollback can each use the 70-second restart-health
# budget. This outer deadline leaves room for both and remains inside the web
# request budget. One worker is deliberate for Pi 3.
CMD ["gunicorn", "--bind", "0.0.0.0:8081", "--workers", "1", "--timeout", "170", "--graceful-timeout", "10", "server:app"]
