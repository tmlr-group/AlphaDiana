#!/usr/bin/env bash
# AlphaDiana installation script
#
# Creates a conda environment, installs alphadiana + dependencies.
# Assumes conda is available on PATH.

set -e

ENV_NAME="${ALPHADIANA_ENV:-alphadiana}"
PYTHON_VERSION="${ALPHADIANA_PYTHON:-3.12}"

if ! command -v conda >/dev/null 2>&1; then
    echo "Error: conda not found on PATH" >&2
    exit 1
fi

if ! conda env list | grep -q "^${ENV_NAME} "; then
    echo "Creating conda env: ${ENV_NAME} (python ${PYTHON_VERSION})"
    conda create -n "${ENV_NAME}" python="${PYTHON_VERSION}" -y
fi

source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate "${ENV_NAME}"

pip install -e .
pip install aiohttp datasets huggingface_hub openai pydantic pyyaml requests httpx ray fastapi uvicorn

echo
echo "Done. Activate with: conda activate ${ENV_NAME}"
