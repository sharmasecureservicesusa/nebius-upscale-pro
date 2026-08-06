#!/bin/bash
set -e

export PYTHONUNBUFFERED=1
export PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True,garbage_collection_threshold:0.8"
export CUDA_MODULE_LOADING="LAZY"
export OMP_NUM_THREADS=16
export MKL_NUM_THREADS=16

# Locate the correct Python binary containing PyTorch and ComfyUI dependencies
PYTHON_BIN="python3"
for candidate in \
    "/opt/environments/python/comfyui/bin/python" \
    "/opt/micromamba/envs/comfyui/bin/python" \
    "/workspace/ComfyUI/venv/bin/python" \
    "/opt/ComfyUI/venv/bin/python"; do
    if [ -f "$candidate" ]; then
        PYTHON_BIN="$candidate"
        break
    fi
done

echo "[INFO] Using Python interpreter: $PYTHON_BIN"

echo "=== Ensuring Required Custom Nodes ==="
mkdir -p /opt/ComfyUI/custom_nodes

if [ ! -d "/opt/ComfyUI/custom_nodes/ComfyUI_UltimateSDUpscale" ]; then
    echo "Cloning ComfyUI_UltimateSDUpscale repository..."
    git clone --depth 1 https://github.com/ssitu/ComfyUI_UltimateSDUpscale /opt/ComfyUI/custom_nodes/ComfyUI_UltimateSDUpscale
else
    echo "✓ ComfyUI_UltimateSDUpscale node already present."
fi

echo "=== Running Model Verification ==="
/app/download_models.sh

if [ "$MODE" = "endpoint" ]; then
    echo "=== Starting Endpoint Service ==="
    exec "$PYTHON_BIN" -m uvicorn server:app --host 0.0.0.0 --port 8000
else
    echo "=== Launching Batch Upscale Pipeline ==="
    exec "$PYTHON_BIN" /app/run_usdu_batch.py
fi