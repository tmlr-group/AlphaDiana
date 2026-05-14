FROM python:3.11

# Ensure required tools are available for Node.js installation
RUN apt-get update \
    && apt-get install -y --no-install-recommends wget ca-certificates xz-utils \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js v22.18.0 from npmmirror with SHA256 verification
ARG NODE_SHA256="c1bfeecf1d7404fa74728f9db72e697decbd8119ccc6f5a294d795756dfcfca7"
RUN wget -q -O /tmp/node.tar.xz \
        https://npmmirror.com/mirrors/node/v22.18.0/node-v22.18.0-linux-x64.tar.xz \
    && echo "${NODE_SHA256}  /tmp/node.tar.xz" | sha256sum -c - \
    && tar -xf /tmp/node.tar.xz -C /opt \
    && mv /opt/node-v22.18.0-linux-x64 /opt/node \
    && rm /tmp/node.tar.xz

ENV PATH="/opt/node/bin:$PATH"

# Install openclaw globally using npmmirror registry
RUN npm config set registry https://registry.npmmirror.com \
    && npm install -g openclaw@2026.3.7 --omit=optional \
    && npm cache clean --force

RUN python -m pip install --no-cache-dir --timeout 120 \
        requests numpy sympy scipy pandas Pillow matplotlib

WORKDIR /workspace

# Verify installation
RUN node --version && npm --version && openclaw --version
