#!/bin/bash
# download_models.sh

CHECKPOINT_DIR="/opt/ComfyUI/models/checkpoints"
CONTROLNET_DIR="/opt/ComfyUI/models/controlnet"

mkdir -p "$CHECKPOINT_DIR" "$CONTROLNET_DIR"

# Download SDXL Checkpoint if not present
if [ ! -f "$CHECKPOINT_DIR/sd_xl_base_1.0.safetensors" ]; then
    echo "Downloading SDXL Base Checkpoint..."
    curl -L "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors" \
         -o "$CHECKPOINT_DIR/sd_xl_base_1.0.safetensors"
fi

# Download SDXL ControlNet Tile if not present
if [ ! -f "$CONTROLNET_DIR/controlnet-tile-sdxl.safetensors" ]; then
    echo "Downloading SDXL ControlNet Tile..."
    curl -L "https://huggingface.co/xinsir/controlnet-tile-sdxl-1.0/resolve/main/diffusion_pytorch_model.safetensors" \
         -o "$CONTROLNET_DIR/controlnet-tile-sdxl.safetensors"
fi

exec "$@"