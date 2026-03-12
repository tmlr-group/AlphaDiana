# AlphaDiana Setup Details

详细的手动部署、ROCK 端口探测、Redis/Ray/admin/proxy 启动顺序、OpenClaw 部署与 FAQ 目前仍以仓库根目录的 `README.md` 为准。

快速入口：

```bash
bash dev/quickstart.sh
```

如果需要手动执行，请优先注意这几项：

- 在仓库根目录执行 `source dev/rock_env.sh`
- 运行 `python dev/find_rock_ports.py --write-env dev/.rock_ports.env` 前先执行 `unset TMPDIR`
- `newgrp docker` 后必须重新 `conda activate`、`source dev/rock_env.sh`、`source dev/.rock_ports.env`
- 启动 Ray 前先执行 `ray stop`
- `RAY_TMPDIR` 默认使用 `/tmp/ray`

后续可以再把 `README.md` 里的手动步骤继续迁移到这里。
