# AlphaDiana Setup Details

Detailed manual deployment, ROCK port detection, Redis/Ray/admin/proxy startup sequence, OpenClaw deployment and FAQ are still based on `README.md` in the repository root directory.

Quick entry:


```bash
bash dev/quickstart.sh
```


If you need to perform it manually, please pay attention to the following items:

- Execute `source dev/rock_env.sh` in the repository root directory
- Execute `unset TMPDIR` before running `python dev/find_rock_ports.py --write-env dev/.rock_ports.env`
- `newgrp docker` must be reset after `conda activate`, `source dev/rock_env.sh`, `source dev/.rock_ports.env`
- Execute `ray stop` before starting Ray
- `RAY_TMPDIR` defaults to `/tmp/ray`

You can continue to migrate the manual steps in `README.md` here later.