FROM ghcr.io/ai-dock/comfyui:latest-cuda

LABEL org.opencontainers.image.source="https://github.com/adminsharmasecureservicescausa/nebiusupscale"

WORKDIR /app

ENV PIPX_HOME=/opt/pipx
ENV PIPX_BIN_DIR=/usr/local/bin

RUN apt-get update && apt-get install -y \
    s3fs \
    dos2unix \
    wget \
    git \
    python3 \
    python3-pip \
    pipx \
    && rm -rf /var/lib/apt/lists/*

RUN pipx install uvicorn && \
    pipx inject uvicorn fastapi python-multipart

RUN git config --global --add safe.directory '*' && \
    mkdir -p /opt/ComfyUI/custom_nodes && \
    rm -rf /opt/ComfyUI/custom_nodes/ComfyUI_UltimateSDUpscale && \
    git clone --depth 1 https://github.com/ssitu/ComfyUI_UltimateSDUpscale /opt/ComfyUI/custom_nodes/ComfyUI_UltimateSDUpscale

RUN mkdir -p /opt/ComfyUI/models/checkpoints \
             /opt/ComfyUI/models/upscale_models \
             /opt/ComfyUI/models/controlnet \
             /opt/ComfyUI/models/loras

COPY . /app

RUN dos2unix /app/entrypoint.sh /app/download_models.sh && \
    chmod +x /app/entrypoint.sh /app/download_models.sh

ENTRYPOINT ["/app/entrypoint.sh"]