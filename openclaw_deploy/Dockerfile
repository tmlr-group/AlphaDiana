FROM python:3.11

# Install Node.js v22.18.0 from npmmirror
RUN wget -q -O /tmp/node.tar.xz \
        https://npmmirror.com/mirrors/node/v22.18.0/node-v22.18.0-linux-x64.tar.xz \
    && tar -xf /tmp/node.tar.xz -C /opt \
    && mv /opt/node-v22.18.0-linux-x64 /opt/node \
    && rm /tmp/node.tar.xz

ENV PATH="/opt/node/bin:$PATH"

# Install openclaw globally using npmmirror registry
RUN npm config set registry https://registry.npmmirror.com \
    && npm install -g openclaw@2026.3.7 --omit=optional \
    && npm cache clean --force

# Verify installation
RUN node --version && npm --version && openclaw --version
