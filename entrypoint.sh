#!/bin/bash
set -e

echo "=== Mounting Nebius Object Storage ==="
mkdir -p /mnt/s3bucket

if [ -n "$S3_ACCESS_KEY" ] && [ -n "$S3_SECRET_KEY" ]; then
    echo "${S3_ACCESS_KEY}:${S3_SECRET_KEY}" > /tmp/passwd-s3fs
    chmod 600 /tmp/passwd-s3fs

    s3fs "${S3_BUCKET_NAME:-ai-upscale-bucket}" /mnt/s3bucket \
        -o passwd_file=/tmp/passwd-s3fs \
        -o url=https://storage.eu-north1.nebius.cloud \
        -o use_path_request_style \
        -o allow_other
    echo "✓ Storage mounted successfully."
fi

echo "=== Creating Required Directory Paths ==="
mkdir -p /opt/ComfyUI/models/checkpoints \
         /opt/ComfyUI/models/upscale_models \
         /opt/ComfyUI/models/controlnet \
         /opt/ComfyUI/models/loras

echo "=== Checking Required Model Files ==="

# 1. SDXL Base Checkpoint
CHECKPOINT_PATH="/opt/ComfyUI/models/checkpoints/sd_xl_base_1.0.safetensors"
if [ ! -f "$CHECKPOINT_PATH" ]; then
    echo "Downloading SDXL Base model..."
    wget -c -L -O "$CHECKPOINT_PATH" "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors"
fi

# 2. SDXL Lightning 8-Step LoRA
LORA_PATH="/opt/ComfyUI/models/loras/sdxl_lightning_8step_lora.safetensors"
if [ ! -f "$LORA_PATH" ]; then
    echo "Downloading SDXL Lightning 8-step LoRA..."
    wget -c -L -O "$LORA_PATH" "https://huggingface.co/ByteDance/SDXL-Lightning/resolve/main/sdxl_lightning_8step_lora.safetensors"
fi

# 3. 4x-UltraSharp Model
UPSCALE_PATH="/opt/ComfyUI/models/upscale_models/4x-UltraSharp.pth"
if [ ! -f "$UPSCALE_PATH" ]; then
    echo "Downloading 4x-UltraSharp model..."
    wget -c -L -O "$UPSCALE_PATH" "https://huggingface.co/lokidvb/4x-UltraSharp/resolve/main/4x-UltraSharp.pth"
fi

# 4. ControlNet Tile SDXL
CONTROLNET_PATH="/opt/ComfyUI/models/controlnet/controlnet-tile-sdxl.safetensors"
if [ ! -f "$CONTROLNET_PATH" ]; then
    echo "Downloading ControlNet Tile SDXL model..."
    wget -c -L -O "$CONTROLNET_PATH" "https://huggingface.co/xinsir/controlnet-tile-sdxl-1.0/resolve/main/diffusion_pytorch_model.safetensors"
fi

echo "✓ All required models verified."

if [ "$MODE" = "endpoint" ]; then
    echo "=== Starting High-Speed Endpoint Service (Port 8000) ==="
    exec uvicorn server:app --host 0.0.0.0 --port 8000
else
    echo "=== Starting High-Speed Upscale Batch Job ==="
    exec python3 /app/run_usdu_batch.py
fi