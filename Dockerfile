FROM ghcr.io/ai-dock/comfyui:latest-cuda

LABEL org.opencontainers.image.source="https://github.com/adminsharmasecureservicescausa/nebiusupscale"

WORKDIR /app

# Configure pipx to place executable binaries directly into system PATH
ENV PIPX_HOME=/opt/pipx
ENV PIPX_BIN_DIR=/usr/local/bin

# Install system dependencies and pipx
RUN apt-get update && apt-get install -y \
    s3fs \
    dos2unix \
    wget \
    git \
    python3 \
    python3-pip \
    pipx \
    && rm -rf /var/lib/apt/lists/*

# Install Uvicorn via pipx and inject required web packages into its isolated environment
RUN pipx install uvicorn && \
    pipx inject uvicorn fastapi python-multipart

# Install Ultimate SD Upscale custom node safely
RUN git config --global --add safe.directory '*' && \
    mkdir -p /opt/ComfyUI/custom_nodes && \
    rm -rf /opt/ComfyUI/custom_nodes/ComfyUI_UltimateSDUpscale && \
    git clone --depth 1 https://github.com/ssitu/ComfyUI_UltimateSDUpscale /opt/ComfyUI/custom_nodes/ComfyUI_UltimateSDUpscale

# Create destination directories for runtime model downloads
RUN mkdir -p /opt/ComfyUI/models/checkpoints \
             /opt/ComfyUI/models/upscale_models \
             /opt/ComfyUI/models/controlnet \
             /opt/ComfyUI/models/loras

# Copy repository scripts into container
COPY . /app

# Convert line endings and set execution permissions
RUN dos2unix /app/entrypoint.sh /app/download_models.sh && \
    chmod +x /app/entrypoint.sh /app/download_models.sh

ENTRYPOINT ["/app/entrypoint.sh"]