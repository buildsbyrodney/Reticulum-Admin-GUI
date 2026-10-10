FROM python:3.12-slim

ARG RNS_VERSION=1.5.7
RUN pip install --no-cache-dir "rns==${RNS_VERSION}"

RUN apt-get update && apt-get install -y --no-install-recommends util-linux && rm -rf /var/lib/apt/lists/*

COPY start-rnsd.sh /usr/local/bin/start-rnsd
COPY observer.py /usr/local/bin/reticulum-observer
COPY control_helper.py /usr/local/bin/reticulum-control-helper
COPY check_rnsd_health.py /usr/local/bin/check-rnsd-health
RUN chmod +x /usr/local/bin/start-rnsd /usr/local/bin/reticulum-observer /usr/local/bin/reticulum-control-helper /usr/local/bin/check-rnsd-health

VOLUME ["/root/.reticulum"]
ENTRYPOINT ["/usr/local/bin/start-rnsd"]
