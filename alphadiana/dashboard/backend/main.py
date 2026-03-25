"""FastAPI application for the AlphaDiana evaluation dashboard."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from alphadiana.dashboard.backend.api import init_job_manager, init_loader, init_sandbox_manager, router
from alphadiana.dashboard.backend.data_loader import DataLoader
from alphadiana.dashboard.backend.job_manager import JobManager
from alphadiana.dashboard.backend.sandbox_manager import SandboxManager

app = FastAPI(title="AlphaDiana Dashboard", version="1.0.0")

# CORS for local development — include whatever frontend port is actually in use.
_frontend_port = os.environ.get("ALPHADIANA_FRONTEND_PORT", "5173")
_cors_origins = [
    f"http://localhost:{_frontend_port}",
    f"http://127.0.0.1:{_frontend_port}",
]
# Always include the default port so hard-coded bookmarks still work.
if _frontend_port != "5173":
    _cors_origins += ["http://localhost:5173", "http://127.0.0.1:5173"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Set HF mirror only when explicitly opted in (e.g. China mainland deployments).
# Enable by setting ALPHADIANA_USE_HF_MIRROR=1 (or "true"/"yes").
if os.environ.get("ALPHADIANA_USE_HF_MIRROR", "").lower() in ("1", "true", "yes"):
    if not os.environ.get("HF_ENDPOINT"):
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

# Resolve paths relative to the project root
PROJECT_ROOT = Path(__file__).resolve().parents[3]  # alphadiana/dashboard/backend -> project root
RESULTS_DIR = os.environ.get("ALPHADIANA_RESULTS_DIR", str(PROJECT_ROOT / "results"))
CONFIGS_DIR = os.environ.get("ALPHADIANA_CONFIGS_DIR", str(PROJECT_ROOT / "configs"))

# Initialize data loader, job manager, and register routes
init_loader(DataLoader(results_dir=RESULTS_DIR, configs_dir=CONFIGS_DIR))
init_job_manager(JobManager(results_dir=RESULTS_DIR, configs_dir=CONFIGS_DIR))
init_sandbox_manager(SandboxManager())
app.include_router(router)

# Serve frontend static files in production (after `npm run build`)
# Mount assets under /assets so it doesn't conflict with /api routes
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")

    @app.get("/{path:path}")
    async def serve_spa(request: Request, path: str):
        """Serve static files or fall back to index.html for SPA routing."""
        file_path = (FRONTEND_DIST / path).resolve()
        # Guard against path traversal (e.g. ../../etc/passwd)
        if path and file_path.is_relative_to(FRONTEND_DIST.resolve()) and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIST / "index.html")
