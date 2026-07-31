#!/bin/bash
set -e

echo "=== Mounting Nebius Object Storage ==="
mkdir -p /mnt/s3bucket

# Configure S3 credentials and mount bucket if keys are provided
if [ -n "$S3_ACCESS_KEY" ] && [ -n "$S3_SECRET_KEY" ]; then
    echo "${S3_ACCESS_KEY}:${S3_SECRET_KEY}" > /tmp/passwd-s3fs
    chmod 600 /tmp/passwd-s3fs

    s3fs "${S3_BUCKET_NAME:-ai-upscale-bucket}" /mnt/s3bucket \
        -o passwd_file=/tmp/passwd-s3fs \
        -o url=https://storage.eu-north1.nebius.cloud \
        -o use_path_request_style \
        -o allow_other
    
    echo "Storage mounted successfully."
else
    echo "Warning: S3 credentials missing. Proceeding without mounting Object Storage..."
fi

echo "=== Starting upscale batch job ==="

# Execute batch runner using python3 (Line 35 Fix)
exec python3 /app/run_usdu_batch.py