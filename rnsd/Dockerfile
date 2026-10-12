FROM python:3.12-slim

ARG RNS_VERSION=1.5.7
ARG LXMF_VERSION=0.9.6
# On-network interface discovery is implemented by Reticulum through LXMF.
# Keep this pin aligned with the tested bot dependency rather than relying on
# a transitive installation that could disappear in a future RNS package.
RUN pip install --no-cache-dir "rns==${RNS_VERSION}" "lxmf==${LXMF_VERSION}" \
 && python -c "import RNS, LXMF; print('RNS and LXMF discovery dependencies available')"

RUN apt-get update && apt-get install -y --no-install-recommends util-linux iproute2 && rm -rf /var/lib/apt/lists/*

COPY start-rnsd.sh /usr/local/bin/start-rnsd
COPY observer.py /usr/local/bin/reticulum-observer
COPY control_helper.py /usr/local/bin/reticulum-control-helper
COPY check_rnsd_health.py /usr/local/bin/check-rnsd-health
COPY check_shared_instance.py /usr/local/bin/check-shared-instance
COPY check_discovery_dependencies.py /usr/local/bin/check-discovery-dependencies
RUN chmod +x /usr/local/bin/start-rnsd /usr/local/bin/reticulum-observer /usr/local/bin/reticulum-control-helper /usr/local/bin/check-rnsd-health /usr/local/bin/check-shared-instance /usr/local/bin/check-discovery-dependencies

VOLUME ["/root/.reticulum"]
ENTRYPOINT ["/usr/local/bin/start-rnsd"]
