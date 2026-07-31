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
else
    echo "⚠️ Warning: S3 credentials missing. Proceeding without mounting Object Storage..."
fi

echo "=== Checking Required Model Files ==="

# 1. SDXL Base Checkpoint
CHECKPOINT_PATH="/opt/ComfyUI/models/checkpoints/sd_xl_base_1.0.safetensors"
if [ ! -f "$CHECKPOINT_PATH" ]; then
    echo "Downloading SDXL Base model..."
    wget -c -O "$CHECKPOINT_PATH" "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors"
fi

# 2. RealESRGAN x4plus
UPSCALE_PATH="/opt/ComfyUI/models/upscale_models/RealESRGAN_x4plus.pth"
if [ ! -f "$UPSCALE_PATH" ]; then
    echo "Downloading RealESRGAN x4plus model..."
    wget -c -O "$UPSCALE_PATH" "https://huggingface.co/ai-forever/Real-ESRGAN/resolve/main/RealESRGAN_x4plus.pth"
fi

# 3. ControlNet Tile SDXL
CONTROLNET_PATH="/opt/ComfyUI/models/controlnet/controlnet-tile-sdxl.safetensors"
if [ ! -f "$CONTROLNET_PATH" ]; then
    echo "Downloading ControlNet Tile SDXL model..."
    wget -c -O "$CONTROLNET_PATH" "https://huggingface.co/xinsir/controlnet-tile-sdxl-1.0/resolve/main/diffusion_pytorch_model.safetensors"
fi

echo "✓ All required models verified."

echo "=== Starting Upscale Batch Job ==="
exec python3 /app/run_usdu_batch.py