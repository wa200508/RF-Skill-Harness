# syntax=docker/dockerfile:1
FROM ubuntu:24.04 AS base
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN --mount=type=secret,id=proxy_ca,mode=0444 \
    set -eu; \
    if [ -f /run/secrets/proxy_ca ]; then \
      sed -i 's|http://|https://|g' /etc/apt/sources.list.d/ubuntu.sources; \
      apt-get -o Acquire::https::CaInfo=/run/secrets/proxy_ca update; \
      apt-get -o Acquire::https::CaInfo=/run/secrets/proxy_ca install -y --no-install-recommends ca-certificates git python3 python3-pip libpython3.12t64 libboost-system1.83.0 libspdlog1.12; \
    else apt-get update && apt-get install -y --no-install-recommends ca-certificates git python3 python3-pip libpython3.12t64 libboost-system1.83.0 libspdlog1.12; fi; \
    rm -rf /var/lib/apt/lists/*
FROM base AS native-source
RUN --mount=type=secret,id=proxy_ca,mode=0444 \
    set -eu; \
    if [ -f /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca; fi; \
    apt-get -o Acquire::https::CaInfo="${SSL_CERT_FILE:-/etc/ssl/certs/ca-certificates.crt}" update \
    && apt-get -o Acquire::https::CaInfo="${SSL_CERT_FILE:-/etc/ssl/certs/ca-certificates.crt}" install -y --no-install-recommends g++ cmake git python3-dev libboost-dev libboost-system-dev nlohmann-json3-dev libspdlog-dev \
    && rm -rf /var/lib/apt/lists/*
RUN --mount=type=secret,id=proxy_ca,mode=0444 \
    if [ -f /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca; fi; \
    apt-get -o Acquire::https::CaInfo="${SSL_CERT_FILE:-/etc/ssl/certs/ca-certificates.crt}" update \
    && apt-get -o Acquire::https::CaInfo="${SSL_CERT_FILE:-/etc/ssl/certs/ca-certificates.crt}" install -y --no-install-recommends make \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY native/ native/
RUN --mount=type=secret,id=proxy_ca,mode=0444 \
    if [ -f /run/secrets/proxy_ca ]; then export GIT_SSL_CAINFO=/run/secrets/proxy_ca; fi; \
    python3 native/fetch_dependencies.py
FROM native-source AS builder
ARG BUILD_NATIVE_FIXTURE=OFF
RUN cmake -S native -B build -DCMAKE_BUILD_TYPE=Release -DBUILD_NATIVE_FIXTURE=${BUILD_NATIVE_FIXTURE} \
    && cmake --build build --parallel 1
FROM builder AS native-test
COPY requirements.txt /tmp/native-requirements.txt
RUN --mount=type=secret,id=proxy_ca,mode=0444 \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca GIT_SSL_CAINFO=/run/secrets/proxy_ca; fi; \
    python3 -m pip install --target /test-deps -r /tmp/native-requirements.txt
ENV PYTHONPATH=/test-deps
COPY design/ /opt/skill/design/
COPY runtime/ /opt/skill/runtime/
COPY config/ /opt/skill/config/
COPY skill.json /opt/skill/skill.json
ENTRYPOINT ["python3", "/build/native/qualify_native.py", "--bundle", "/opt/skill", "--build-dir", "/build/build"]
FROM base AS skill-runtime
WORKDIR /opt/skill
COPY --from=builder /build/build/rf-skill /usr/local/bin/rf-skill
COPY --from=builder /build/native/third_party/ third_party/
COPY skill.json requirements.txt ./
COPY design/ design/
COPY runtime/ runtime/
COPY config/ config/
COPY LICENSE NOTICE ./
RUN --mount=type=secret,id=proxy_ca,mode=0444 \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca GIT_SSL_CAINFO=/run/secrets/proxy_ca; fi; \
    python3 -m pip install --no-cache-dir --target /opt/skill/python-deps -r requirements.txt \
    && chmod -R a+rX /opt/skill \
    && groupadd --gid 10001 skill && useradd --uid 10001 --gid skill --create-home skill
ENV PYTHONPATH=/opt/skill/python-deps
USER skill
ENTRYPOINT ["rf-skill", "--config"]
CMD ["/opt/skill/config/runtime.json"]
FROM skill-runtime AS test
USER root
RUN --mount=type=secret,id=proxy_ca,mode=0444 \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca GIT_SSL_CAINFO=/run/secrets/proxy_ca; fi; \
    python3 -m pip install --no-cache-dir --target /opt/skill/python-deps pytest
COPY tests/ tests/
COPY export-manifest.json ./
RUN python3 -m pytest tests -q -p no:cacheprovider
FROM skill-runtime AS runtime
