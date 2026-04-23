#!/usr/bin/env python3
from __future__ import annotations

import os

_bind_host = os.environ.get("ROCK_BIND_HOST") or os.environ.get("ROCK_HTTP_HOST") or "127.0.0.1"

import rock.admin.main as rock_admin_main

_original_uvicorn_run = rock_admin_main.uvicorn.run


def _run_with_local_bind(app, *args, **kwargs):
    kwargs["host"] = _bind_host
    return _original_uvicorn_run(app, *args, **kwargs)


rock_admin_main.uvicorn.run = _run_with_local_bind


if __name__ == "__main__":
    rock_admin_main.main()
