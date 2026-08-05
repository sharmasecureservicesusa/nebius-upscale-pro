import os
import sys
import glob
import json
import time
import uuid
import shutil
import subprocess
import urllib.request
import urllib.parse
import urllib.error
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse

SERVER_ADDRESS = "127.0.0.1:8188"
WORKFLOW_FILE = "/app/workflow_api.json"
COMFY_LOG_FILE = "/tmp/comfyui.log"

app = FastAPI(title="ComfyUI Fast SDXL Upscaler Endpoint")
comfy_process = None

def find_python_executable():
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
    return "/opt/ComfyUI"

def ensure_comfyui_running():
    try:
        urllib.request.urlopen(f"http://{SERVER_ADDRESS}/system_stats", timeout=2)
        print("✓ ComfyUI server is active.")
        return None
    except Exception:
        print("Launching local ComfyUI instance (--gpu-only, --fp16-vae, SDPA)...")
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
                print(f"❌ ComfyUI process exited prematurely.")
                os.system(f"cat {COMFY_LOG_FILE}")
                sys.exit(1)
            try:
                urllib.request.urlopen(f"http://{SERVER_ADDRESS}/system_stats", timeout=2)
                print("✓ ComfyUI server initialized successfully!")
                return proc
            except Exception:
                time.sleep(2)
        sys.exit(1)

def queue_prompt(prompt_workflow):
    client_id = str(uuid.uuid4())
    payload = json.dumps({"prompt": prompt_workflow, "client_id": client_id}).encode("utf-8")
    req = urllib.request.Request(f"http://{SERVER_ADDRESS}/prompt", data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as response:
        res_data = json.loads(response.read().decode("utf-8"))
        return res_data["prompt_id"]

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

@app.on_event("startup")
def startup_event():
    global comfy_process
    print("🚀 Starting ComfyUI backend service...")
    comfy_process = ensure_comfyui_running()

@app.on_event("shutdown")
def shutdown_event():
    global comfy_process
    if comfy_process:
        print("Terminating ComfyUI server process...")
        comfy_process.terminate()

@app.get("/health")
def health():
    return {"status": "ready"}

@app.post("/v1/upscale")
async def upscale(file: UploadFile = File(...)):
    comfy_dir = find_comfyui_dir()
    comfy_input_dir = os.path.join(comfy_dir, "input")
    os.makedirs(comfy_input_dir, exist_ok=True)

    filename = f"{uuid.uuid4()}_{file.filename}"
    temp_input_path = os.path.join(comfy_input_dir, filename)

    with open(temp_input_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        with open(WORKFLOW_FILE, "r") as f:
            workflow = json.load(f)

        for node_id, node in workflow.items():
            if node.get("class_type") == "LoadImage":
                workflow[node_id]["inputs"]["image"] = filename

        prompt_id = queue_prompt(workflow)
        history = wait_for_completion(prompt_id)

        outputs = history.get("outputs", {})
        generated_path = None

        for node_id, output_data in outputs.items():
            if "images" in output_data and len(output_data["images"]) > 0:
                img_info = output_data["images"][0]
                generated_path = os.path.join(comfy_dir, "output", img_info.get("subfolder", ""), img_info["filename"])
                break

        if generated_path and os.path.exists(generated_path):
            return FileResponse(generated_path, media_type="image/png", filename=f"upscaled_{file.filename}")
        else:
            raise HTTPException(status_code=500, detail="Upscaling failed to generate output image.")

    finally:
        if os.path.exists(temp_input_path):
            os.remove(temp_input_path)