import os
import sys
import json
import time
import uuid
import shutil
import subprocess
import urllib.request
import urllib.parse
import urllib.error

# -------------------------------------------------------------------
# Global Configuration & Paths
# -------------------------------------------------------------------
COMFYUI_DIR = os.getenv("COMFYUI_DIR", "/opt/ComfyUI")
if not os.path.exists(COMFYUI_DIR) and os.path.exists("/workspace/ComfyUI"):
    COMFYUI_DIR = "/workspace/ComfyUI"

SERVER_ADDRESS = "127.0.0.1:8188"
INPUT_DIR = "/mnt/s3bucket/input"
OUTPUT_DIR = "/mnt/s3bucket/output"
WORKFLOW_FILE = "/app/workflow_api.json"
COMFY_LOG_FILE = "/tmp/comfyui.log"

try:
    upscale_factor = float(os.getenv("UPSCALE_FACTOR", "3.0"))
except ValueError:
    print("⚠️ Invalid UPSCALE_FACTOR provided. Defaulting to 3.0x")
    upscale_factor = 3.0

print(f"=== Target Resolution Multiplier: {upscale_factor}x ===")

# -------------------------------------------------------------------
# ComfyUI Server Setup
# -------------------------------------------------------------------
import glob

def find_python_executable():
    """Locates the Python binary containing PyTorch & ComfyUI dependencies."""
    candidates = [
        "/opt/environments/python/comfyui/bin/python",
        "/opt/environments/python/comfyui/bin/python3",
        "/opt/micromamba/envs/comfyui/bin/python",
        "/workspace/ComfyUI/venv/bin/python",
        "/opt/ComfyUI/venv/bin/python",
    ]
    for path in candidates:
        if os.path.exists(path):
            res = subprocess.run([path, "-c", "import torch"], capture_output=True)
            if res.returncode == 0:
                return path
    for pattern in ["/opt/**/bin/python*", "/workspace/**/bin/python*"]:
        for path in glob.glob(pattern, recursive=True):
            if os.path.isfile(path) and os.access(path, os.X_OK) and not path.endswith("-config"):
                res = subprocess.run([path, "-c", "import torch"], capture_output=True)
                if res.returncode == 0:
                    return path
    return sys.executable

def find_comfyui_dir():
    candidates = [os.getenv("COMFYUI_DIR", ""), "/workspace/ComfyUI", "/opt/ComfyUI", "/app/ComfyUI"]
    for path in candidates:
        if path and os.path.exists(os.path.join(path, "main.py")):
            return path
    return "/workspace/ComfyUI"

def ensure_comfyui_running():
    """Launches ComfyUI background instance and streams logs safely to a file."""
    try:
        urllib.request.urlopen(f"http://{SERVER_ADDRESS}/system_stats", timeout=2)
        print("✓ ComfyUI server is already active.")
        return None
    except Exception:
        print("Launching local ComfyUI server instance...")
        python_bin = find_python_executable()
        comfy_dir = find_comfyui_dir()
        main_py = os.path.join(comfy_dir, "main.py")
        
        # Open a file for logs to prevent buffer deadlocks
        log_handle = open(COMFY_LOG_FILE, "w")
        
        proc = subprocess.Popen(
            [
                python_bin, main_py,
                "--listen", "127.0.0.1",
                "--port", "8188",
                "--disable-auto-launch"
            ],
            stdout=log_handle,
            stderr=subprocess.STDOUT
        )

        for _ in range(60):
            if proc.poll() is not None:
                print(f"❌ ComfyUI process exited prematurely. Checking logs at {COMFY_LOG_FILE}")
                os.system(f"cat {COMFY_LOG_FILE}")
                sys.exit(1)
            try:
                urllib.request.urlopen(f"http://{SERVER_ADDRESS}/system_stats", timeout=2)
                print("✓ ComfyUI server initialized successfully!")
                return proc
            except Exception:
                time.sleep(2)
        
        print("❌ Error: ComfyUI server failed to start within timeout.")
        os.system(f"cat {COMFY_LOG_FILE}")
        sys.exit(1)

# -------------------------------------------------------------------
# ComfyUI API Handlers
# -------------------------------------------------------------------
def queue_prompt(prompt_workflow):
    """Sends prompt payload to ComfyUI API endpoint."""
    client_id = str(uuid.uuid4())
    payload = json.dumps({"prompt": prompt_workflow, "client_id": client_id}).encode("utf-8")
    req = urllib.request.Request(
        f"http://{SERVER_ADDRESS}/prompt",
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            return res_data["prompt_id"]
    except urllib.error.HTTPError as e:
        print(f"\n❌ ComfyUI Prompt Validation Failed (HTTP 400):")
        print(e.read().decode("utf-8"))
        raise e

def wait_for_completion(prompt_id):
    """Polls history and queue to ensure job completes and doesn't freeze."""
    while True:
        # 1. Check if job finished successfully
        try:
            with urllib.request.urlopen(f"http://{SERVER_ADDRESS}/history/{prompt_id}") as resp:
                history = json.loads(resp.read().decode("utf-8"))
                if prompt_id in history:
                    return history[prompt_id]
        except Exception:
            pass

        # 2. Check if job disappeared from the queue (Silent crash)
        try:
            with urllib.request.urlopen(f"http://{SERVER_ADDRESS}/queue") as resp:
                queue = json.loads(resp.read().decode("utf-8"))
                in_queue = any(q[1] == prompt_id for q in queue.get("queue_running", []) + queue.get("queue_pending", []))
                
                if not in_queue:
                    time.sleep(2) # Grace period for transit from queue to history
                    with urllib.request.urlopen(f"http://{SERVER_ADDRESS}/history/{prompt_id}") as hist_resp:
                        hist = json.loads(hist_resp.read().decode("utf-8"))
                        if prompt_id not in hist:
                            print(f"\n❌ ERROR: Image generation dropped silently inside ComfyUI.")
                            print("--- Last 50 lines of ComfyUI execution logs ---")
                            os.system(f"tail -n 50 {COMFY_LOG_FILE}")
                            print("-----------------------------------------------")
                            raise RuntimeError("ComfyUI execution failed.")
                        else:
                            return hist[prompt_id]
        except Exception as e:
            if isinstance(e, RuntimeError):
                raise e
            
        time.sleep(2)

# -------------------------------------------------------------------
# Main Batch Runner
# -------------------------------------------------------------------
def main():
    os.makedirs(INPUT_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    image_files = [f for f in os.listdir(INPUT_DIR) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))]
    if not image_files:
        print(f"No images found in {INPUT_DIR}. Nothing to process.")
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

    comfy_input_dir = os.path.join(COMFYUI_DIR, "input")
    os.makedirs(comfy_input_dir, exist_ok=True)

    for idx, img_name in enumerate(image_files, 1):
        print(f"\n[{idx}/{len(image_files)}] Processing image: {img_name}")
        src_input_path = os.path.join(INPUT_DIR, img_name)
        target_output_path = os.path.join(OUTPUT_DIR, f"upscaled_{img_name}")

        try:
            comfy_temp_input = os.path.join(comfy_input_dir, img_name)
            shutil.copy(src_input_path, comfy_temp_input)

            current_workflow = json.loads(json.dumps(base_workflow))
            if load_image_node:
                current_workflow[load_image_node]["inputs"]["image"] = img_name

            prompt_id = queue_prompt(current_workflow)
            print(f"Queued Job ID: {prompt_id}. Generating...")
            
            history = wait_for_completion(prompt_id)
            outputs = history.get("outputs", {})
            generated_full_path = None

            for node_id, output_data in outputs.items():
                if "images" in output_data and len(output_data["images"]) > 0:
                    img_info = output_data["images"][0]
                    generated_full_path = os.path.join(COMFYUI_DIR, "output", img_info.get("subfolder", ""), img_info["filename"])
                    break

            if generated_full_path and os.path.exists(generated_full_path):
                shutil.move(generated_full_path, target_output_path)
                print(f"✓ Saved result to S3 storage: {target_output_path}")

                if os.path.exists(comfy_temp_input):
                    os.remove(comfy_temp_input)
                if os.path.exists(target_output_path):
                    os.remove(src_input_path)
                    print(f"🗑️ Cleaned original image from S3 input: {src_input_path}")
            else:
                print(f"⚠️ Output verification failed for {img_name}. Retaining input file.")

        except Exception as e:
            print(f"❌ Error processing {img_name}: {str(e)}")

    # Clean up background process if managed by this script
    if server_proc:
        print("Terminating background ComfyUI instance...")
        server_proc.terminate()

    print("\n=== All batch jobs processed successfully ===")

if __name__ == "__main__":
    main()