cd /data1/siatmri/menghan/code/AlphaDiana

  podman build --network host \
    -f moltbench/docker/Dockerfile.qjl-base \
    -t localhost/alphadiana:qjl-base .

    ``` bash
     podman build \
    --runtime /home/user/miniconda3/bin/runc \
    --network host \
    -f moltbench/docker/Dockerfile.qjl-base \
    -t localhost/alphadiana:qjl-base .
    ```

  podman build --network host \
    -f moltbench/docker/Dockerfile.qjl-openclaw \
    -t localhost/alphadiana:qjl-openclaw .

  OPENAI_API_KEY=你的_key \
  OPENAI_BASE_URL=你的_openai_compatible_base_url \
  OPENAI_MODEL_NAME=你的_model_name \
  python -m alphadiana.cli run configs/examples/moltbench_qjl_openclaw_podman_smoke.yaml