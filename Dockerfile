# syntax=docker/dockerfile:1
FROM ubuntu:24.04 AS runtime
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# Use distro GNU Radio and its matching Python. A build-only CA mount supports
# managed HTTPS proxies without retaining a session certificate in the image.
# Minimal Ubuntu needs its CA store bootstrapped from signed vendor repositories.
RUN --mount=type=secret,id=proxy_ca,mode=0444 \
    set -eu; \
    if [ -f /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca; \
      sed -i 's|http://archive.ubuntu.com/ubuntu|https://archive.ubuntu.com/ubuntu|g; s|http://security.ubuntu.com/ubuntu|https://security.ubuntu.com/ubuntu|g' /etc/apt/sources.list.d/ubuntu.sources; \
      apt-get -o Acquire::https::CaInfo="$SSL_CERT_FILE" update; \
      apt-get -o Acquire::https::CaInfo="$SSL_CERT_FILE" install -y --no-install-recommends \
        ca-certificates gnuradio python3-numpy python3-pip python3-pytest python3-yaml; \
    else \
      apt-get update; apt-get install -y --no-install-recommends ca-certificates; \
      sed -i 's|http://archive.ubuntu.com/ubuntu|https://archive.ubuntu.com/ubuntu|g; s|http://security.ubuntu.com/ubuntu|https://security.ubuntu.com/ubuntu|g' /etc/apt/sources.list.d/ubuntu.sources; \
      apt-get update; apt-get install -y --no-install-recommends \
      ca-certificates gnuradio python3-numpy python3-pip python3-pytest python3-yaml; fi; \
    rm -rf /var/lib/apt/lists/*
ARG SOURCE_REVISION=development
ENV HOME=/tmp XDG_CACHE_HOME=/tmp/.cache
LABEL org.opencontainers.image.source="https://github.com/wa200508/RF-Skill-Harness" \
      org.opencontainers.image.revision="${SOURCE_REVISION}" \
      org.opencontainers.image.licenses="Apache-2.0"
WORKDIR /opt/rf-skill-harness
COPY pyproject.toml LICENSE ./
COPY src/ src/
RUN python3 -m pip install --no-deps --no-build-isolation --break-system-packages . \
    && groupadd --gid 10001 rf && useradd --uid 10001 --gid rf --create-home rf
COPY --chmod=755 scripts/ scripts/
COPY --chmod=755 tests/ tests/
USER rf
WORKDIR /work
ENTRYPOINT ["python3", "-m", "rf_skill_harness"]
CMD ["--help"]
