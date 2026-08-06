#!/bin/bash
set -e

export PYTHONUNBUFFERED=1
export PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True,garbage_collection_threshold:0.8"
export CUDA_MODULE_LOADING="LAZY"
export OMP_NUM_THREADS=16
export MKL_NUM_THREADS=16

git clone --depth 1 https://github.com/ssitu/ComfyUI_UltimateSDUpscale /opt/ComfyUI/custom_nodes/ComfyUI_UltimateSDUpscale

echo "=== Running Model Verification ==="
/app/download_models.sh

if [ "$MODE" = "endpoint" ]; then
    echo "=== Starting Endpoint Service ==="
    exec uvicorn server:app --host 0.0.0.0 --port 8000
else
    echo "=== Launching Batch Upscale Pipeline ==="
    exec python3 /app/run_usdu_batch.py
fi