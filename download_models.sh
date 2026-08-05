#!/bin/bash
set -e

CHECKPOINT_DIR="/opt/ComfyUI/models/checkpoints"
CONTROLNET_DIR="/opt/ComfyUI/models/controlnet"
UPSCALE_DIR="/opt/ComfyUI/models/upscale_models"
LORA_DIR="/opt/ComfyUI/models/loras"

mkdir -p "$CHECKPOINT_DIR" "$CONTROLNET_DIR" "$UPSCALE_DIR" "$LORA_DIR"

# 1. Download SDXL Base Checkpoint
if [ ! -f "$CHECKPOINT_DIR/sd_xl_base_1.0.safetensors" ]; then
    echo "Downloading SDXL Base Checkpoint..."
    curl -fL -C - "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors" \
         -o "$CHECKPOINT_DIR/sd_xl_base_1.0.safetensors"
fi

# 2. Download SDXL Lightning 8-Step LoRA
if [ ! -f "$LORA_DIR/sdxl_lightning_8step_lora.safetensors" ]; then
    echo "Downloading SDXL Lightning 8-Step LoRA..."
    curl -fL -C - "https://huggingface.co/ByteDance/SDXL-Lightning/resolve/main/sdxl_lightning_8step_lora.safetensors" \
         -o "$LORA_DIR/sdxl_lightning_8step_lora.safetensors"
fi

# 3. Download 4x-UltraSharp Model
if [ ! -f "$UPSCALE_DIR/4x-UltraSharp.pth" ]; then
    echo "Downloading 4x-UltraSharp Model..."
    curl -fL -C - "https://huggingface.co/lokidvb/4x-UltraSharp/resolve/main/4x-UltraSharp.pth" \
         -o "$UPSCALE_DIR/4x-UltraSharp.pth"
fi

# 4. Download SDXL ControlNet Tile
if [ ! -f "$CONTROLNET_DIR/controlnet-tile-sdxl.safetensors" ]; then
    echo "Downloading SDXL ControlNet Tile..."
    curl -fL -C - "https://huggingface.co/xinsir/controlnet-tile-sdxl-1.0/resolve/main/diffusion_pytorch_model.safetensors" \
         -o "$CONTROLNET_DIR/controlnet-tile-sdxl.safetensors"
fi

echo "✓ All required models verified successfully!"

exec "$@"