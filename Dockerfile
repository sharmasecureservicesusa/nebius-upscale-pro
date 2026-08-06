FROM ghcr.io/ai-dock/comfyui:latest-cuda

LABEL org.opencontainers.image.source="https://github.com/adminsharmasecureservicescausa/nebiusupscale"

WORKDIR /app

# System Environment Variables
ENV PYTHONUNBUFFERED=1
ENV PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True,garbage_collection_threshold:0.8"
ENV PIPX_HOME=/opt/pipx
ENV PIPX_BIN_DIR=/usr/local/bin

# Install system dependencies
RUN apt-get update && apt-get install -y \
    dos2unix \
    wget \
    git \
    python3 \
    python3-pip \
    pipx \
    && rm -rf /var/lib/apt/lists/*

# Pre-install boto3 and image processing dependencies so startup is instant
RUN pip3 install --no-cache-dir boto3 pillow-heif

# Install uvicorn API endpoint dependencies
RUN pipx install uvicorn && \
    pipx inject uvicorn fastapi python-multipart pillow-heif boto3

# Pre-install ComfyUI_UltimateSDUpscale custom node
RUN git config --global --add safe.directory '*' && \
    mkdir -p /opt/ComfyUI/custom_nodes && \
    rm -rf /opt/ComfyUI/custom_nodes/ComfyUI_UltimateSDUpscale && \
    git clone --depth 1 https://github.com/ssitu/ComfyUI_UltimateSDUpscale /opt/ComfyUI/custom_nodes/ComfyUI_UltimateSDUpscale

# Create model directory structure
RUN mkdir -p /opt/ComfyUI/models/checkpoints \
             /opt/ComfyUI/models/upscale_models \
             /opt/ComfyUI/models/controlnet \
             /opt/ComfyUI/models/loras

# Copy repository contents into /app
COPY . /app

# Convert line endings (prevents Windows CRLF carriage return crashes) and fix permissions
RUN dos2unix /app/entrypoint.sh /app/download_models.sh /app/run_usdu_batch.py && \
    chmod +x /app/entrypoint.sh /app/download_models.sh

ENTRYPOINT ["/app/entrypoint.sh"]