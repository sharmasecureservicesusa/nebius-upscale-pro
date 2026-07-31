import os
import sys
import json
import time
import glob
import subprocess
import uuid
import shutil
import subprocess
import urllib.request
import urllib.parse

# -------------------------------------------------------------------
# Global Configuration & Paths
# -------------------------------------------------------------------
# Automatically locate ComfyUI base path inside the container
COMFYUI_DIR = os.getenv("COMFYUI_DIR", "/opt/ComfyUI")
if not os.path.exists(COMFYUI_DIR) and os.path.exists("/workspace/ComfyUI"):
    COMFYUI_DIR = "/workspace/ComfyUI"

SERVER_ADDRESS = "127.0.0.1:8188"
INPUT_DIR = "/mnt/s3bucket/input"
OUTPUT_DIR = "/mnt/s3bucket/output"
WORKFLOW_FILE = "/app/workflow_api.json"

# -------------------------------------------------------------------
# 1. Parse Dynamic Resolution Multiplier
# -------------------------------------------------------------------
try:
    upscale_factor = float(os.getenv("UPSCALE_FACTOR", "3.0"))
except ValueError:
    print("⚠️ Invalid UPSCALE_FACTOR provided. Defaulting to 3.0x")
    upscale_factor = 3.0

print(f"=== Target Resolution Multiplier: {upscale_factor}x ===")

# -------------------------------------------------------------------
# 2. ComfyUI Server & API Helpers
# -------------------------------------------------------------------
def find_python_executable():
    """Locates the Python binary that contains PyTorch and ComfyUI dependencies."""
    candidates = [
        "/opt/environments/python/comfyui/bin/python",
        "/opt/environments/python/comfyui/bin/python3",
        "/opt/micromamba/envs/comfyui/bin/python",
        "/opt/micromamba/envs/comfyui/bin/python3",
        "/workspace/environments/python/comfyui/bin/python",
        "/workspace/ComfyUI/venv/bin/python",
        "/opt/ComfyUI/venv/bin/python",
    ]
    
    # 1. Test known container paths for PyTorch
    for path in candidates:
        if os.path.exists(path):
            res = subprocess.run([path, "-c", "import torch"], capture_output=True)
            if res.returncode == 0:
                print(f"✓ Found PyTorch in environment: {path}")
                return path

    # 2. Dynamic search if paths shift across image tags
    print("Searching container directories for Python binary with PyTorch...")
    for pattern in ["/opt/**/bin/python*", "/workspace/**/bin/python*"]:
        for path in glob.glob(pattern, recursive=True):
            if os.path.isfile(path) and os.access(path, os.X_OK) and not path.endswith("-config"):
                res = subprocess.run([path, "-c", "import torch"], capture_output=True)
                if res.returncode == 0:
                    print(f"✓ Dynamically discovered PyTorch binary: {path}")
                    return path

    print("⚠️ Warning: Could not locate PyTorch environment. Falling back to sys.executable.")
    return sys.executable

def find_comfyui_dir():
    """Locates the main ComfyUI installation directory."""
    candidates = [
        os.getenv("COMFYUI_DIR", ""),
        "/workspace/ComfyUI",
        "/opt/ComfyUI",
        "/app/ComfyUI"
    ]
    for path in candidates:
        if path and os.path.exists(os.path.join(path, "main.py")):
            return path
    return "/workspace/ComfyUI"

def ensure_comfyui_running():
    """Checks if ComfyUI server is active; launches background instance with live logging if needed."""
    try:
        urllib.request.urlopen(f"http://{SERVER_ADDRESS}/system_stats", timeout=2)
        print("✓ ComfyUI server is active and listening on port 8188.")
        return None
    except Exception:
        print("Launching local ComfyUI server instance...")
        python_bin = find_python_executable()
        comfy_dir = find_comfyui_dir()
        main_py = os.path.join(comfy_dir, "main.py")
        
        print(f"-> Using Python binary: {python_bin}")
        print(f"-> Using ComfyUI main.py: {main_py}")
        
        if not os.path.exists(main_py):
            print(f"❌ Error: main.py not found at {main_py}")
            sys.exit(1)

        # Launch background server process and capture output
        proc = subprocess.Popen(
            [
                python_bin, main_py,
                "--listen", "127.0.0.1",
                "--port", "8188",
                "--disable-auto-launch"
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        # Poll until server responds, printing logs if process dies prematurely
        for _ in range(60):
            if proc.poll() is not None:
                print(f"❌ ComfyUI process exited prematurely with code: {proc.returncode}")
                print("--- ComfyUI Startup Logs ---")
                out, _ = proc.communicate()
                print(out)
                print("----------------------------")
                sys.exit(1)

            try:
                urllib.request.urlopen(f"http://{SERVER_ADDRESS}/system_stats", timeout=2)
                print("✓ ComfyUI server initialized successfully!")
                return proc
            except Exception:
                time.sleep(2)

        print("❌ Error: ComfyUI server failed to start within 120s timeout.")
        if proc.poll() is None:
            proc.terminate()
            out, _ = proc.communicate()
            print("--- ComfyUI Startup Logs ---")
            print(out)
        sys.exit(1)

def queue_prompt(prompt_workflow):
    """Sends prompt payload to ComfyUI API endpoint with detailed error catching."""
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
        error_body = e.read().decode("utf-8")
        print(f"\n❌ ComfyUI Prompt Validation Failed (HTTP 400):")
        try:
            parsed_err = json.loads(error_body)
            print(json.dumps(parsed_err, indent=2))
        except Exception:
            print(error_body)
        raise e

def wait_for_completion(prompt_id):
    """Polls history endpoint until processing finishes."""
    while True:
        try:
            with urllib.request.urlopen(f"http://{SERVER_ADDRESS}/history/{prompt_id}") as resp:
                history = json.loads(resp.read().decode("utf-8"))
                if prompt_id in history:
                    return history[prompt_id]
        except Exception:
            pass
        time.sleep(2)

# -------------------------------------------------------------------
# 3. Main Batch Runner
# -------------------------------------------------------------------
def main():
    os.makedirs(INPUT_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Fetch pending images from S3 input folder
    image_files = [
        f for f in os.listdir(INPUT_DIR)
        if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))
    ]

    if not image_files:
        print(f"No images found in {INPUT_DIR}. Nothing to process.")
        sys.exit(0)

    print(f"Found {len(image_files)} image(s) in input queue.")

    # 1. Start Server
    server_proc = ensure_comfyui_running()

    # 2. Load API Workflow Structure
    if not os.path.exists(WORKFLOW_FILE):
        print(f"❌ Error: Workflow file not found at {WORKFLOW_FILE}")
        sys.exit(1)

    with open(WORKFLOW_FILE, "r") as f:
        base_workflow = json.load(f)

    # 3. Identify Node References
    load_image_node = None
    usdu_node = None

    for node_id, node_data in base_workflow.items():
        class_type = node_data.get("class_type", "")
        if class_type == "LoadImage":
            load_image_node = node_id
        elif class_type == "UltimateSDUpscale":
            usdu_node = node_id

    # Apply global upscale factor to workflow
    if usdu_node:
        base_workflow[usdu_node]["inputs"]["upscale_by"] = upscale_factor
        print(f"✓ Configured UltimateSDUpscale (Node {usdu_node}) to {upscale_factor}x resolution.")

    comfy_input_dir = os.path.join(COMFYUI_DIR, "input")
    os.makedirs(comfy_input_dir, exist_ok=True)

    # 4. Batch Execution Loop
    for idx, img_name in enumerate(image_files, 1):
        print(f"\n[{idx}/{len(image_files)}] Processing image: {img_name}")
        src_input_path = os.path.join(INPUT_DIR, img_name)
        target_output_path = os.path.join(OUTPUT_DIR, f"upscaled_{img_name}")

        try:
            # Copy source image into ComfyUI local input directory
            comfy_temp_input = os.path.join(comfy_input_dir, img_name)
            shutil.copy(src_input_path, comfy_temp_input)

            # Build workflow copy for current image execution
            current_workflow = json.loads(json.dumps(base_workflow))
            if load_image_node:
                current_workflow[load_image_node]["inputs"]["image"] = img_name

            # Queue prompt & await completion
            prompt_id = queue_prompt(current_workflow)
            print(f"Queued Job ID: {prompt_id}. Upscaling in progress...")
            history = wait_for_completion(prompt_id)

            # Retrieve path to generated image output
            outputs = history.get("outputs", {})
            generated_full_path = None

            for node_id, output_data in outputs.items():
                if "images" in output_data and len(output_data["images"]) > 0:
                    img_info = output_data["images"][0]
                    generated_filename = img_info["filename"]
                    subfolder = img_info.get("subfolder", "")
                    comfy_out_dir = os.path.join(COMFYUI_DIR, "output", subfolder)
                    generated_full_path = os.path.join(comfy_out_dir, generated_filename)
                    break

            # Verify and move generated image to S3 bucket
            if generated_full_path and os.path.exists(generated_full_path):
                shutil.move(generated_full_path, target_output_path)
                print(f"✓ Saved result to S3 storage: {target_output_path}")

                # Clean temporary local input copy
                if os.path.exists(comfy_temp_input):
                    os.remove(comfy_temp_input)

                # Delete original source file from S3 input folder after confirmation
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