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
LABEL org.opencontainers.image.source="https://github.com/wa200508/RF-Skill-Harness" \
      org.opencontainers.image.revision="${SOURCE_REVISION}" \
      org.opencontainers.image.licenses="Apache-2.0"
WORKDIR /opt/rf-skill-harness
COPY pyproject.toml LICENSE ./
# Share the distro's GNU Radio bindings while installing lab packages separately.
# --without-pip avoids another distro package: the system pip is visible through
# --system-site-packages and installs into this venv, not over apt-owned files.
RUN python3 -m venv --without-pip --system-site-packages /opt/rf-harness-venv
ENV PATH="/opt/rf-harness-venv/bin:$PATH"
RUN --mount=type=secret,id=proxy_ca,mode=0444 \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    python3 -c 'import subprocess,sys,tomllib; spec=tomllib.load(open("pyproject.toml", "rb")); subprocess.check_call([sys.executable,"-m","pip","install","--no-cache-dir","numpy<2",*spec["project"]["optional-dependencies"]["lab"]])' \
    && groupadd --gid 10001 rf && useradd --uid 10001 --gid rf --create-home rf
COPY src/ src/
# grcc uses the distro interpreter; both it and the lab venv must see this wheel.
RUN /usr/bin/python3 -m pip install --no-deps --no-cache-dir --no-build-isolation --break-system-packages .
COPY --chmod=755 scripts/ scripts/
COPY --chmod=755 tests/ tests/
USER rf
WORKDIR /work
ENTRYPOINT ["python3", "-m", "rf_skill_harness"]
CMD ["--help"]
