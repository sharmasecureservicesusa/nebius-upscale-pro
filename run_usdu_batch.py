import os
import sys
import glob
import json
import time
import uuid
import shutil
import subprocess
import urllib.request

# Force unbuffered real-time logging for Nebius AI Cloud Jobs
sys.stdout.reconfigure(line_buffering=True)

print("=== Starting Upscaling Batch Pipeline ===", flush=True)

# Ensure boto3 is available for native S3 transfers
try:
    import boto3
except ImportError:
    print("Installing boto3 for S3 API transfers...", flush=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "boto3"], check=True)
    import boto3

# Configuration & Environment Variables
S3_BUCKET = os.getenv("S3_BUCKET_NAME", "ai-upscale-bucket")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY")
S3_ENDPOINT = os.getenv("S3_ENDPOINT_URL", "https://storage.eu-north1.nebius.cloud")

LOCAL_INPUT_DIR = "/tmp/input"
LOCAL_OUTPUT_DIR = "/tmp/output"
SERVER_ADDRESS = "127.0.0.1:8188"
WORKFLOW_FILE = "/app/workflow_api.json"
COMFY_LOG_FILE = "/tmp/comfyui.log"

try:
    upscale_factor = float(os.getenv("UPSCALE_FACTOR", "2.0"))
except ValueError:
    upscale_factor = 2.0

SUPPORTED_EXTENSIONS = (
    '.png', '.jpg', '.jpeg', '.webp', 
    '.bmp', '.tiff', '.tif', '.tga'
)

def find_python_executable():
    candidates = [
        "/opt/environments/python/comfyui/bin/python",
        "/opt/micromamba/envs/comfyui/bin/python",
        "/workspace/ComfyUI/venv/bin/python",
        "/opt/ComfyUI/venv/bin/python",
    ]
    for path in candidates:
        if os.path.exists(path):
            res = subprocess.run([path, "-c", "import torch"], capture_output=True)
            if res.returncode == 0:
                return path
    return sys.executable

def find_comfyui_dir():
    candidates = [os.getenv("COMFYUI_DIR", ""), "/workspace/ComfyUI", "/opt/ComfyUI", "/app/ComfyUI"]
    for path in candidates:
        if path and os.path.exists(os.path.join(path, "main.py")):
            return path
    return "/opt/ComfyUI"

def ensure_comfyui_running():
    try:
        urllib.request.urlopen(f"http://{SERVER_ADDRESS}/system_stats", timeout=2)
        print("✓ ComfyUI server is active.", flush=True)
        return None
    except Exception:
        print("Launching local ComfyUI instance (--gpu-only, --fp16-vae)...", flush=True)
        python_bin = find_python_executable()
        comfy_dir = find_comfyui_dir()
        main_py = os.path.join(comfy_dir, "main.py")
        
        log_handle = open(COMFY_LOG_FILE, "w")
        proc = subprocess.Popen(
            [
                python_bin, main_py,
                "--listen", "127.0.0.1",
                "--port", "8188",
                "--gpu-only",
                "--fp16-vae",
                "--use-pytorch-cross-attention",
                "--disable-auto-launch"
            ],
            stdout=log_handle,
            stderr=subprocess.STDOUT
        )

        for _ in range(60):
            if proc.poll() is not None:
                print("❌ ComfyUI process exited prematurely.", flush=True)
                os.system(f"cat {COMFY_LOG_FILE}")
                sys.exit(1)
            try:
                urllib.request.urlopen(f"http://{SERVER_ADDRESS}/system_stats", timeout=2)
                print("✓ ComfyUI server initialized successfully!", flush=True)
                return proc
            except Exception:
                time.sleep(2)
        sys.exit(1)

def queue_prompt(prompt_workflow):
    client_id = str(uuid.uuid4())
    payload = json.dumps({"prompt": prompt_workflow, "client_id": client_id}).encode("utf-8")
    req = urllib.request.Request(f"http://{SERVER_ADDRESS}/prompt", data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode("utf-8"))["prompt_id"]

def wait_for_completion(prompt_id):
    while True:
        try:
            with urllib.request.urlopen(f"http://{SERVER_ADDRESS}/history/{prompt_id}") as resp:
                history = json.loads(resp.read().decode("utf-8"))
                if prompt_id in history:
                    return history[prompt_id]
        except Exception:
            pass
        time.sleep(0.3)

def main():
    os.makedirs(LOCAL_INPUT_DIR, exist_ok=True)
    os.makedirs(LOCAL_OUTPUT_DIR, exist_ok=True)

    # Initialize S3 Client
    s3_client = None
    if S3_ACCESS_KEY and S3_SECRET_KEY:
        print(f"Connecting to Nebius S3 bucket: {S3_BUCKET}...", flush=True)
        s3_client = boto3.client(
            "s3",
            aws_access_key_id=S3_ACCESS_KEY,
            aws_secret_access_key=S3_SECRET_KEY,
            endpoint_url=S3_ENDPOINT
        )

        # Download input images from s3://<bucket>/input/
        response = s3_client.list_objects_v2(Bucket=S3_BUCKET, Prefix="input/")
        objects = response.get("Contents", [])

        download_count = 0
        for obj in objects:
            key = obj["Key"]
            filename = os.path.basename(key)
            if filename and filename.lower().endswith(SUPPORTED_EXTENSIONS):
                local_path = os.path.join(LOCAL_INPUT_DIR, filename)
                print(f"  -> Downloading from S3: {key}", flush=True)
                s3_client.download_file(S3_BUCKET, key, local_path)
                download_count += 1

        print(f"✓ Downloaded {download_count} image(s) from S3.", flush=True)

    image_files = [f for f in os.listdir(LOCAL_INPUT_DIR) if f.lower().endswith(SUPPORTED_EXTENSIONS)]
    if not image_files:
        print(f"❌ No valid images found in s3://{S3_BUCKET}/input/. Exiting.", flush=True)
        sys.exit(0)

    server_proc = ensure_comfyui_running()

    with open(WORKFLOW_FILE, "r") as f:
        base_workflow = json.load(f)

    load_image_node = None
    usdu_node = None
    for node_id, node_data in base_workflow.items():
        if node_data.get("class_type") == "LoadImage":
            load_image_node = node_id
        elif node_data.get("class_type") == "UltimateSDUpscale":
            usdu_node = node_id

    if usdu_node:
        base_workflow[usdu_node]["inputs"]["upscale_by"] = upscale_factor

    comfy_dir = find_comfyui_dir()
    comfy_input_dir = os.path.join(comfy_dir, "input")
    os.makedirs(comfy_input_dir, exist_ok=True)

    for idx, img_name in enumerate(image_files, 1):
        print(f"\n[{idx}/{len(image_files)}] Processing image: {img_name}", flush=True)
        local_src_path = os.path.join(LOCAL_INPUT_DIR, img_name)
        comfy_temp_input = os.path.join(comfy_input_dir, img_name)

        try:
            shutil.copy(local_src_path, comfy_temp_input)

            current_workflow = json.loads(json.dumps(base_workflow))
            if load_image_node:
                current_workflow[load_image_node]["inputs"]["image"] = img_name

            prompt_id = queue_prompt(current_workflow)
            history = wait_for_completion(prompt_id)
            outputs = history.get("outputs", {})
            generated_full_path = None

            for node_id, output_data in outputs.items():
                if "images" in output_data and len(output_data["images"]) > 0:
                    img_info = output_data["images"][0]
                    generated_full_path = os.path.join(comfy_dir, "output", img_info.get("subfolder", ""), img_info["filename"])
                    break

            if generated_full_path and os.path.exists(generated_full_path):
                s3_target_key = f"output/upscaled_{img_name}"
                if s3_client:
                    print(f"  -> Uploading result to S3: s3://{S3_BUCKET}/{s3_target_key}", flush=True)
                    s3_client.upload_file(generated_full_path, S3_BUCKET, s3_target_key)
                    # Remove original file from input/ in S3
                    s3_client.delete_object(Bucket=S3_BUCKET, Key=f"input/{img_name}")

                print(f"✓ Successfully processed and uploaded: {img_name}", flush=True)

                if os.path.exists(comfy_temp_input):
                    os.remove(comfy_temp_input)
                if os.path.exists(generated_full_path):
                    os.remove(generated_full_path)
            else:
                print(f"⚠️ Output verification failed for {img_name}.", flush=True)

        except Exception as e:
            print(f"❌ Error processing {img_name}: {str(e)}", flush=True)

    if server_proc:
        server_proc.terminate()

    print("\n=== All batch jobs processed successfully ===", flush=True)

if __name__ == "__main__":
    main()