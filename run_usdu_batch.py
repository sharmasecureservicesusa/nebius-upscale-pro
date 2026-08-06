import os
import sys
import glob
import json
import time
import uuid
import shutil
import re
import subprocess
import urllib.request
import urllib.error
from queue import Queue
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(line_buffering=True)

print("=== Starting Auto-Scaling Multi-GPU Batch Pipeline ===", flush=True)

# Ensure boto3 is available
try:
    import boto3
except ImportError:
    print("Installing boto3 for S3 API transfers...", flush=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "boto3"], check=True)
    import boto3

import torch

# Configuration & Environment Variables
S3_BUCKET = os.getenv("S3_BUCKET_NAME", "ai-upscale-bucket")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY")
S3_ENDPOINT = os.getenv("S3_ENDPOINT_URL", "https://storage.eu-north1.nebius.cloud")

LOCAL_INPUT_DIR = "/tmp/input"
LOCAL_OUTPUT_DIR = "/tmp/output"
WORKFLOW_FILE = "/app/workflow_api.json"

try:
    upscale_factor = float(os.getenv("UPSCALE_FACTOR", "2.0"))
except ValueError:
    upscale_factor = 2.0

SUPPORTED_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tiff', '.tif', '.tga')

# Hardware Auto-Detection & Dynamic Scaling Setup
GPU_COUNT = torch.cuda.device_count()
VRAM_GB = (torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)) if GPU_COUNT > 0 else 0

# Auto-calculate optimal defaults based on VRAM per GPU if ENV overrides are not set
DEFAULT_WORKERS_PER_GPU = 2 if VRAM_GB >= 35 else 1
DEFAULT_BATCH_SIZE = 2 if VRAM_GB >= 35 else 1
DEFAULT_TILE_SIZE = 1024

WORKERS_PER_GPU = int(os.getenv("WORKERS_PER_GPU", str(DEFAULT_WORKERS_PER_GPU)))
TILE_BATCH_SIZE = int(os.getenv("TILE_BATCH_SIZE", str(DEFAULT_BATCH_SIZE)))
TILE_SIZE = int(os.getenv("TILE_SIZE", str(DEFAULT_TILE_SIZE)))


def find_python_executable():
    candidates = [
        "/opt/environments/python/comfyui/bin/python",
        "/opt/micromamba/envs/comfyui/bin/python",
        "/workspace/ComfyUI/venv/bin/python",
        "/opt/ComfyUI/venv/bin/python",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return sys.executable


def find_comfyui_dir():
    candidates = [os.getenv("COMFYUI_DIR", ""), "/workspace/ComfyUI", "/opt/ComfyUI", "/app/ComfyUI"]
    for path in candidates:
        if path and os.path.exists(os.path.join(path, "main.py")):
            return path
    return "/opt/ComfyUI"


def start_comfyui_server(worker_id, gpu_id, port):
    """Starts a ComfyUI instance assigned to a specific GPU and Port."""
    server_address = f"127.0.0.1:{port}"
    print(f"Launching Worker {worker_id} on GPU {gpu_id} (Port {port})...", flush=True)
    
    python_bin = find_python_executable()
    comfy_dir = find_comfyui_dir()
    main_py = os.path.join(comfy_dir, "main.py")
    log_file = f"/tmp/comfyui_worker_{worker_id}.log"
    
    env = os.environ.copy()
    if GPU_COUNT > 0:
        env["CUDA_VISIBLE_DEVICES"] = str(gpu_id)

    log_handle = open(log_file, "w")
    proc = subprocess.Popen(
        [
            python_bin, main_py,
            "--listen", "127.0.0.1",
            "--port", str(port),
            "--gpu-only" if GPU_COUNT > 0 else "--cpu",
            "--fp16-vae",
            "--use-pytorch-cross-attention",
            "--disable-auto-launch"
        ],
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        env=env
    )

    for _ in range(60):
        if proc.poll() is not None:
            print(f"[ERROR] Worker {worker_id} on GPU {gpu_id} exited prematurely.", flush=True)
            os.system(f"cat {log_file}")
            sys.exit(1)
        try:
            urllib.request.urlopen(f"http://{server_address}/system_stats", timeout=2)
            print(f"[INFO] Worker {worker_id} active on GPU {gpu_id} (Port {port}).", flush=True)
            return proc, server_address
        except Exception:
            time.sleep(2)
    sys.exit(1)


def queue_prompt(server_address, prompt_workflow):
    client_id = str(uuid.uuid4())
    payload = json.dumps({"prompt": prompt_workflow, "client_id": client_id}).encode("utf-8")
    req = urllib.request.Request(
        f"http://{server_address}/prompt", 
        data=payload, 
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode("utf-8"))["prompt_id"]


def wait_for_completion(server_address, prompt_id):
    while True:
        try:
            with urllib.request.urlopen(f"http://{server_address}/history/{prompt_id}") as resp:
                history = json.loads(resp.read().decode("utf-8"))
                if prompt_id in history:
                    return history[prompt_id]
        except Exception:
            pass
        time.sleep(0.3)


def worker_thread(worker_id, gpu_id, server_address, job_queue, base_workflow, load_image_node, comfy_dir, s3_client):
    """Worker task processing images from the shared queue."""
    comfy_input_dir = os.path.join(comfy_dir, "input")
    os.makedirs(comfy_input_dir, exist_ok=True)

    while not job_queue.empty():
        try:
            idx, img_name, total_files = job_queue.get_nowait()
        except Exception:
            break

        print(f"[Worker {worker_id} | GPU {gpu_id}] Processing [{idx}/{total_files}]: {img_name}", flush=True)
        local_src_path = os.path.join(LOCAL_INPUT_DIR, img_name)
        safe_img_name = f"w{worker_id}_" + re.sub(r'[^a-zA-Z0-9_.-]', '_', img_name)
        comfy_temp_input = os.path.join(comfy_input_dir, safe_img_name)

        try:
            shutil.copy(local_src_path, comfy_temp_input)

            current_workflow = json.loads(json.dumps(base_workflow))
            if load_image_node:
                current_workflow[load_image_node]["inputs"]["image"] = safe_img_name

            prompt_id = queue_prompt(server_address, current_workflow)
            history = wait_for_completion(server_address, prompt_id)
            outputs = history.get("outputs", {})
            generated_full_path = None

            for node_id, output_data in outputs.items():
                if "images" in output_data and len(output_data["images"]) > 0:
                    img_info = output_data["images"][0]
                    generated_full_path = os.path.join(comfy_dir, "output", img_info.get("subfolder", ""), img_info["filename"])
                    break

            if generated_full_path and os.path.exists(generated_full_path):
                s3_target_key = f"output/upscaled_{re.sub(r'[^a-zA-Z0-9_.-]', '_', img_name)}"
                if s3_client:
                    s3_client.upload_file(generated_full_path, S3_BUCKET, s3_target_key)
                    s3_client.delete_object(Bucket=S3_BUCKET, Key=f"input/{img_name}")

                print(f"[Worker {worker_id} | GPU {gpu_id}] Successfully upscaled: {img_name}", flush=True)

                # Cleanup temp local files immediately
                if os.path.exists(comfy_temp_input):
                    os.remove(comfy_temp_input)
                if os.path.exists(generated_full_path):
                    os.remove(generated_full_path)
                if os.path.exists(local_src_path):
                    os.remove(local_src_path)
            else:
                print(f"[Worker {worker_id} | GPU {gpu_id}] WARNING: Output file missing for {img_name}.", flush=True)

        except Exception as e:
            print(f"[Worker {worker_id} | GPU {gpu_id}] ERROR: Error processing {img_name}: {str(e)}", flush=True)
        finally:
            job_queue.task_done()


def main():
    os.makedirs(LOCAL_INPUT_DIR, exist_ok=True)
    os.makedirs(LOCAL_OUTPUT_DIR, exist_ok=True)

    s3_client = None
    if S3_ACCESS_KEY and S3_SECRET_KEY:
        print(f"Connecting to Nebius S3 bucket: {S3_BUCKET}...", flush=True)
        s3_client = boto3.client(
            "s3",
            aws_access_key_id=S3_ACCESS_KEY,
            aws_secret_access_key=S3_SECRET_KEY,
            endpoint_url=S3_ENDPOINT
        )

        response = s3_client.list_objects_v2(Bucket=S3_BUCKET, Prefix="input/")
        objects = response.get("Contents", [])

        download_count = 0
        for obj in objects:
            key = obj["Key"]
            filename = os.path.basename(key)
            if filename and filename.lower().endswith(SUPPORTED_EXTENSIONS):
                local_path = os.path.join(LOCAL_INPUT_DIR, filename)
                s3_client.download_file(S3_BUCKET, key, local_path)
                download_count += 1

        print(f"Downloaded {download_count} image(s) from S3.", flush=True)

    image_files = [f for f in os.listdir(LOCAL_INPUT_DIR) if f.lower().endswith(SUPPORTED_EXTENSIONS)]
    if not image_files:
        print(f"[ERROR] No valid images found in s3://{S3_BUCKET}/input/. Exiting.", flush=True)
        sys.exit(0)

    # Calculate total active workers based on detected GPUs
    if GPU_COUNT == 0:
        print("[WARNING] No GPU detected. Defaulting to 1 CPU worker instance.", flush=True)
        effective_gpus = 1
        total_workers = 1
    else:
        effective_gpus = GPU_COUNT
        total_workers = GPU_COUNT * WORKERS_PER_GPU

    print(f"Detected {GPU_COUNT} GPU(s) (~{VRAM_GB:.1f} GB VRAM per GPU).", flush=True)
    print(f"Auto-scaling configuration: {total_workers} parallel workers ({WORKERS_PER_GPU} per GPU, tile_size={TILE_SIZE}, tile_batch={TILE_BATCH_SIZE})", flush=True)

    server_procs = []
    worker_configs = []
    base_port = 8188

    for worker_id in range(total_workers):
        gpu_id = worker_id // WORKERS_PER_GPU if GPU_COUNT > 0 else 0
        port = base_port + worker_id
        proc, server_addr = start_comfyui_server(worker_id, gpu_id, port)
        server_procs.append(proc)
        worker_configs.append((worker_id, gpu_id, server_addr))

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
        usdu_inputs = base_workflow[usdu_node]["inputs"]
        usdu_inputs["upscale_by"] = upscale_factor
        usdu_inputs["tile_width"] = TILE_SIZE
        usdu_inputs["tile_height"] = TILE_SIZE
        usdu_inputs["batch_size"] = TILE_BATCH_SIZE
        usdu_inputs["force_uniform_tiles"] = True
        usdu_inputs.setdefault("seam_fix_mode", "None")
        usdu_inputs.setdefault("seam_fix_width", 64)
        usdu_inputs.setdefault("seam_fix_denoise", 0.35)
        usdu_inputs.setdefault("seam_fix_padding", 32)
        usdu_inputs.setdefault("seam_fix_mask_blur", 8)
        usdu_inputs.setdefault("tiled_decode", False)

    comfy_dir = find_comfyui_dir()

    # Populate thread-safe queue
    job_queue = Queue()
    for idx, img_name in enumerate(image_files, 1):
        job_queue.put((idx, img_name, len(image_files)))

    # Launch worker pool matching total calculated workers
    with ThreadPoolExecutor(max_workers=total_workers) as executor:
        futures = []
        for worker_id, gpu_id, server_addr in worker_configs:
            f = executor.submit(
                worker_thread, worker_id, gpu_id, server_addr, job_queue, 
                base_workflow, load_image_node, comfy_dir, s3_client
            )
            futures.append(f)
        
        for f in futures:
            f.result()

    for proc in server_procs:
        proc.terminate()

    print("\n=== All batch jobs completed successfully ===", flush=True)


if __name__ == "__main__":
    main()