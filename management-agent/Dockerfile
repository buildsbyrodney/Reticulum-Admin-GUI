FROM python:3.12-slim
ARG RNS_VERSION=1.5.7
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt "rns==${RNS_VERSION}"
COPY management.py configuration.py server.py ./
CMD ["gunicorn", "--bind", "0.0.0.0:8081", "--workers", "1", "server:app"]
