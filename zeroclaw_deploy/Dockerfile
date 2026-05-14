FROM docker.io/library/rust:1-slim-bookworm AS zc-builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        pkg-config \
        libssl-dev \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN cargo install zeroclawlabs@0.6.9 --locked

FROM docker.io/library/python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        coreutils \
        ca-certificates \
        libssl3 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=zc-builder /usr/local/cargo/bin/zeroclaw /usr/local/bin/zeroclaw

RUN pip install --no-cache-dir --timeout 120 \
        requests numpy sympy scipy pandas Pillow matplotlib

WORKDIR /workspace

RUN zeroclaw --version && python --version
