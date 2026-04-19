FROM rust:1-slim-bookworm AS zc-builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        pkg-config \
        libssl-dev \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN cargo install zeroclawlabs@0.6.9 --locked

FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        coreutils \
        ca-certificates \
        libssl3 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=zc-builder /usr/local/cargo/bin/zeroclaw /usr/local/bin/zeroclaw

RUN pip install --no-cache-dir sympy numpy

RUN zeroclaw --version && python --version
