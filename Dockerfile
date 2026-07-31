FROM ghcr.io/ai-dock/comfyui:latest-cuda

# Link container package directly to GitHub Repository
LABEL org.opencontainers.image.source="https://github.com/adminsharmasecureservicescausa/nebiusupscale"

WORKDIR /app

# Install core Linux utilities and S3 tools
RUN apt-get update && apt-get install -y \
    s3fs \
    dos2unix \
    wget \
    git \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Fix Git ownership check and clone Ultimate SD Upscale node
RUN git config --global --add safe.directory '*' && \
    mkdir -p /opt/ComfyUI/custom_nodes && \
    rm -rf /opt/ComfyUI/custom_nodes/ComfyUI_UltimateSDUpscale && \
    git clone --depth 1 https://github.com/ssitu/ComfyUI_UltimateSDUpscale /opt/ComfyUI/custom_nodes/ComfyUI_UltimateSDUpscale

# Create model directories
RUN mkdir -p /opt/ComfyUI/models/checkpoints \
             /opt/ComfyUI/models/upscale_models \
             /opt/ComfyUI/models/controlnet

# Pre-download required models during image build
# 1. SDXL Base Checkpoint
RUN wget -q -O /opt/ComfyUI/models/checkpoints/sd_xl_base_1.0.safetensors \
    https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors

# 2. RealESRGAN x4plus Model
RUN wget -q -O /opt/ComfyUI/models/upscale_models/RealESRGAN_x4plus.pth \
    https://huggingface.co/ai-forever/Real-ESRGAN/resolve/main/RealESRGAN_x4plus.pth

# 3. ControlNet Tile SDXL Model
RUN wget -q -O /opt/ComfyUI/models/controlnet/controlnet-tile-sdxl.safetensors \
    https://huggingface.co/xinsir/controlnet-tile-sdxl-1.0/resolve/main/diffusion_pytorch_model.safetensors

# Copy repository scripts into container
COPY . /app

# Convert Windows CRLF line endings to Linux LF and set execution permissions
RUN dos2unix /app/entrypoint.sh && chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]