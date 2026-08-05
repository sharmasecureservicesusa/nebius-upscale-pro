import os
import sys
import glob
import json
import time
import uuid
import shutil
import itertools
import subprocess
import urllib.request
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse

# Ports for 3 concurrent ComfyUI GPU backend instances
COMFY_PORTS = [8188, 8189, 8190]
port_cycle = itertools.cycle(COMFY_PORTS)

WORKFLOW_FILE = "/app/workflow_api.json"
app = FastAPI(title="ComfyUI 3-Worker Parallel L40S Endpoint")
comfy_processes = []

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
            return path
    return sys.executable

def find_comfyui_dir():
    candidates = [os.getenv("COMFYUI_DIR", ""), "/workspace/ComfyUI", "/opt/ComfyUI", "/app/ComfyUI"]
    for path in candidates:
        if path and os.path.exists(os.path.join(path, "main.py")):
            return path
    return "/opt/ComfyUI"

def start_comfy_instance(port):
    python_bin = find_python_executable()
    comfy_dir = find_comfyui_dir()
    main_py = os.path.join(comfy_dir, "main.py")
    log_file = f"/tmp/comfyui_{port}.log"
    
    log_handle = open(log_file, "w")
    proc = subprocess.Popen(
        [
            python_bin, main_py,
            "--listen", "127.0.0.1",
            "--port", str(port),
            "--gpu-only",
            "--fp16-vae",
            "--use-pytorch-cross-attention",
            "--disable-auto-launch"
        ],
        stdout=log_handle,
        stderr=subprocess.STDOUT
    )
    
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/system_stats", timeout=2)
            print(f"✓ ComfyUI instance on port {port} initialized successfully!")
            return proc
        except Exception:
            time.sleep(2)
    sys.exit(1)

@app.on_event("startup")
def startup_event():
    global comfy_processes
    print("🚀 Starting 3 parallel ComfyUI GPU backend instances...")
    for port in COMFY_PORTS:
        proc = start_comfy_instance(port)
        comfy_processes.append(proc)

@app.on_event("shutdown")
def shutdown_event():
    for proc in comfy_processes:
        proc.terminate()

@app.get("/health")
def health():
    return {"status": "ready", "active_workers": len(COMFY_PORTS)}

def queue_prompt(prompt_workflow, port):
    client_id = str(uuid.uuid4())
    payload = json.dumps({"prompt": prompt_workflow, "client_id": client_id}).encode("utf-8")
    req = urllib.request.Request(f"http://127.0.0.1:{port}/prompt", data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as response:
        res_data = json.loads(response.read().decode("utf-8"))
        return res_data["prompt_id"]

def wait_for_completion(prompt_id, port):
    while True:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/history/{prompt_id}") as resp:
                history = json.loads(resp.read().decode("utf-8"))
                if prompt_id in history:
                    return history[prompt_id]
        except Exception:
            pass
        time.sleep(0.2)

@app.post("/v1/upscale")
async def upscale(file: UploadFile = File(...)):
    target_port = next(port_cycle)  # Distribute incoming requests across worker 8188, 8189, and 8190
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

        prompt_id = queue_prompt(workflow, target_port)
        history = wait_for_completion(prompt_id, target_port)

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