FROM docker.io/tmlrgroup/alphadiana:v1

# Align the standard Podman OpenClaw image with the ROCK OpenClaw sandbox base.
# The base image provides the OpenClaw tool layer; this Dockerfile only adds the
# Python packages expected by non-code benchmark agents and applies the same
# OpenAI SDK stream-timeout patch used by the ROCK image.

USER root

RUN ln -sf /usr/bin/python3 /usr/bin/python \
    && apt-get update \
    && apt-get install -y --no-install-recommends python3-pip \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --break-system-packages --timeout 120 \
        requests numpy sympy scipy pandas Pillow matplotlib

# The mutable ROCK-aligned base currently ships OpenClaw 2026.3.14. Keep this
# image pinned to the OpenClaw version used by the existing ROCK runtime config.
# Unpack the package directly so npm does not re-solve and prune the base
# image's large /app dependency tree, which the gateway imports at startup.
RUN set -eu; \
    unpack_pkg() { \
      pkg="$1"; \
      dest="$2"; \
      tgz="$(npm pack "$pkg" --silent \
        --registry https://registry.npmmirror.com --pack-destination /tmp | tail -n 1)"; \
      rm -rf "$dest"; \
      mkdir -p "$dest"; \
      tar -xzf "/tmp/$tgz" -C "$dest" --strip-components=1; \
    }; \
    unpack_pkg openclaw@2026.3.7 /app/node_modules/openclaw; \
    unpack_pkg @mariozechner/pi-ai@0.55.3 /app/node_modules/@mariozechner/pi-ai; \
    unpack_pkg @mariozechner/pi-agent-core@0.55.3 /app/node_modules/@mariozechner/pi-agent-core; \
    unpack_pkg @mariozechner/pi-coding-agent@0.55.3 /app/node_modules/@mariozechner/pi-coding-agent; \
    unpack_pkg @mariozechner/pi-tui@0.55.3 /app/node_modules/@mariozechner/pi-tui; \
    chown -R node:node /app/node_modules/openclaw /app/node_modules/@mariozechner \
    && ln -sf /app/node_modules/.bin/openclaw /usr/local/bin/openclaw \
    && npm cache clean --force

# Fix: Node.js native fetch can truncate long OpenAI-compatible streaming
# responses through the internal undici body timeout. Passing an explicit
# timeout to the OpenAI client lets the SDK own the stream budget.
RUN node -e " \
const fs = require('fs'); \
const paths = [ \
  '/app/node_modules/@mariozechner/pi-ai/dist/providers/openai-completions.js', \
  '/app/node_modules/openclaw/node_modules/@mariozechner/pi-ai/dist/providers/openai-completions.js', \
]; \
const OLD = '    return new OpenAI({\n        apiKey,\n        baseURL: model.baseUrl,\n        dangerouslyAllowBrowser: true,\n        defaultHeaders: headers,\n    });'; \
const NEW = '    return new OpenAI({\n        apiKey,\n        baseURL: model.baseUrl,\n        dangerouslyAllowBrowser: true,\n        defaultHeaders: headers,\n        timeout: 1800000,\n    });'; \
let patched = 0; \
for (const p of paths) { \
  if (!fs.existsSync(p)) { console.log('SKIP:', p); continue; } \
  const src = fs.readFileSync(p, 'utf8'); \
  if (src.includes('timeout: 1800000')) { console.log('ALREADY PATCHED:', p); patched++; continue; } \
  if (!src.includes(OLD)) { console.error('PATTERN NOT FOUND:', p); process.exit(1); } \
  fs.writeFileSync(p, src.replace(OLD, NEW), 'utf8'); \
  console.log('PATCHED:', p); patched++; \
} \
if (patched === 0) { console.error('ERROR: no openai-completions.js found to patch'); process.exit(1); } \
console.log('stream-timeout patch done; files patched/confirmed:', patched); \
"

USER node

WORKDIR /workspace

RUN node --version && npm --version && openclaw --version
