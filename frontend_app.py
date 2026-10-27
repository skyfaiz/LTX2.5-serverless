from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import aiohttp
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.requests import Request
from starlette.responses import Response

from ltx_payload_builder import (
    ASPECT_RATIOS,
    FPS,
    SECONDS_MAX,
    SECONDS_MIN,
    SECONDS_STEP,
    build_payload,
    get_advanced_fields,
    seconds_to_frames,
)
from workflow_support import (
    apply_input_filename_map,
    build_output_path,
    collect_output_entries,
    guess_media_type,
    write_input_images,
)

ROOT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT_DIR / "frontend"

# Load local .env if present (without requiring python-dotenv)
_env_file = ROOT_DIR / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

def _load_runpod_key_from_mcp_configs() -> str:
    candidates = [
        ROOT_DIR / ".devin" / "mcp_config.local.json",
        ROOT_DIR / ".devin" / "mcp_config.json",
        ROOT_DIR.parent / ".devin" / "mcp_config.local.json",
        ROOT_DIR.parent / ".devin" / "mcp_config.json",
        Path.home() / ".config" / "devin" / "mcp_config.local.json",
        Path.home() / ".config" / "devin" / "mcp_config.json",
    ]
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        server = (data.get("mcpServers") or {}).get("runpod") or {}
        env_key = ((server.get("env") or {}).get("RUNPOD_API_KEY") or "").strip()
        header = ((server.get("headers") or {}).get("Authorization") or "").strip()
        if env_key:
            return env_key
        if header.lower().startswith("bearer "):
            return header[7:].strip()
        if header:
            return header
    return ""


DEFAULT_RUNPOD_ENDPOINT_ID = os.environ.get("RUNPOD_ENDPOINT_ID", "fizt5mjsivvy3i").strip()
DEFAULT_RUNPOD_API_KEY = (
    os.environ.get("RUNPOD_API_KEY", "").strip()
    or _load_runpod_key_from_mcp_configs()
)
RUNPOD_MCP_URL = os.environ.get("RUNPOD_MCP_URL", "https://mcp.runpod.io/").strip()
LTX_POD_NAME = os.environ.get("LTX_POD_NAME", "ltx25-pod").strip()
LTX_POD_IMAGE = os.environ.get("LTX_POD_IMAGE", "notrius/ltx-2.5-serverless:cu130").strip()
LTX_VOLUME_ID = os.environ.get("LTX_VOLUME_ID", "vkqbtcijow").strip()
LTX_VOLUME_NAME = os.environ.get("LTX_VOLUME_NAME", "ltx25-models").strip()
LTX_VOLUME_SIZE_GB = int(os.environ.get("LTX_VOLUME_SIZE_GB", "165"))
LTX_DATA_CENTER_ID = os.environ.get("LTX_DATA_CENTER_ID", "EUR-IS-2").strip()
LTX_MOUNT_PATH = os.environ.get("LTX_MOUNT_PATH", "/workspace").strip()
LTX_HF_SECRET_REF = os.environ.get(
    "LTX_HF_SECRET_REF", "{{ RUNPOD_SECRET_hf_access_token }}"
).strip()
CACHED_OUTPUTS_DIR = ROOT_DIR / "outputs"
CACHED_OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

LTX_GPU_OPTIONS = [
    {
        "id": "NVIDIA L40S",
        "label": "NVIDIA L40S · 48 GB VRAM ($1.09/hr)",
        "vram_gb": 48,
        "price_hr": 1.09,
    },
    {
        "id": "NVIDIA RTX PRO 6000 Blackwell Server Edition MIG 2g.48gb",
        "label": "RTX PRO 6000 MIG · 48 GB VRAM ($1.29/hr)",
        "vram_gb": 48,
        "price_hr": 1.29,
    },
    {
        "id": "NVIDIA RTX PRO 6000 Blackwell Server Edition",
        "label": "RTX PRO 6000 Blackwell · 96 GB VRAM ($2.49/hr)",
        "vram_gb": 96,
        "price_hr": 2.49,
    },
]
COMFY_NODES = os.environ.get("COMFY_NODES", "127.0.0.1:8188").split(",")
COMFY_INPUT_DIR = os.environ.get("COMFY_INPUT_DIR", "/comfyui/input")
LOCAL_COMFY_NODE = os.environ.get("LOCAL_COMFY_NODE", "127.0.0.1:8188").strip()
FRONTEND_SUBMIT_URLS = tuple(
    url.strip() for url in os.environ.get("FRONTEND_SUBMIT_URLS", "").split(",")
    if url.strip()
)
POD_SUBMIT_JOBS: dict[str, dict[str, Any]] = {}

app = FastAPI(title="LTX 2.5 Payload Builder")
app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")


@app.middleware("http")
async def disable_frontend_caching(request: Request, call_next) -> Response:
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


class PayloadRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=1000)
    seconds: float = Field(default=5.0, ge=SECONDS_MIN, le=SECONDS_MAX)
    aspect_ratio: str = Field(default="16:9")
    image_name: str = Field(..., min_length=1)
    image_data_url: str = Field(..., min_length=1)
    optimize_prompt: bool = True
    overrides: dict[str, Any] = Field(default_factory=dict)


class SubmitRequest(BaseModel):
    endpoint_url: str = Field(..., min_length=1)
    auth_token: str = ""
    payload: dict[str, Any]
    timeout_seconds: int = Field(default=900, ge=5, le=3600)

    @field_validator("endpoint_url")
    @classmethod
    def validate_endpoint_url(cls, value: str) -> str:
        return get_submit_url(value.strip())


def get_submit_url(value: str) -> str:
    for configured_url in FRONTEND_SUBMIT_URLS:
        if value == configured_url and configured_url.startswith(("http://", "https://")):
            return configured_url

    match = re.fullmatch(r"https://api\.runpod\.ai/v2/([A-Za-z0-9_-]+)/(run|runsync)", value)
    if match:
        return f"https://api.runpod.ai/v2/{match[1]}/{match[2]}"
    raise ValueError(
        "Use a RunPod /run or /runsync URL, or an exact URL configured in FRONTEND_SUBMIT_URLS."
    )


class PodSubmitRequest(BaseModel):
    payload: dict[str, Any]
    timeout_seconds: int = Field(default=900, ge=5, le=3600)


def get_run_mode() -> str:
    run_mode = os.environ.get("RUN_MODE", "").strip().lower()
    if run_mode in {"worker", "local-api", "pod"}:
        return run_mode
    if os.environ.get("SERVE_API_LOCALLY", "").strip().lower() == "true":
        return "local-api"
    return "pod"


def is_local_comfy_environment() -> bool:
    return Path(COMFY_INPUT_DIR).exists()


def get_submission_mode() -> str:
    return "pod" if get_run_mode() == "pod" else "endpoint"


def normalize_node_host(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise RuntimeError("LOCAL_COMFY_NODE is not configured.")

    if "://" in normalized:
        parsed = urlsplit(normalized)
        if not parsed.netloc:
            raise RuntimeError(f"Invalid LOCAL_COMFY_NODE value: {value}")
        normalized = parsed.netloc

    return normalized.rstrip("/")


def get_pod_submit_node() -> str:
    return normalize_node_host(LOCAL_COMFY_NODE)


def prepare_pod_images(
    images: list[dict[str, str]] | None,
) -> tuple[dict[str, str], list[dict[str, str]]]:
    if not images:
        return {}, []

    job_prefix = f"frontend-{uuid.uuid4().hex[:12]}"
    replacements: dict[str, str] = {}
    prepared_images: list[dict[str, str]] = []

    for image in images:
        image_name = image.get("name")
        image_data = image.get("image")
        if not image_name or not image_data:
            raise ValueError(
                "'images' must be a list of objects with 'name' and 'image' keys."
            )

        scoped_name = str(Path(job_prefix) / Path(image_name)).replace("\\", "/")
        replacements[image_name] = scoped_name
        prepared_images.append({"name": scoped_name, "image": image_data})

    return replacements, prepared_images


def cleanup_input_files(filepaths: list[str]) -> None:
    input_root = Path(COMFY_INPUT_DIR).resolve()

    for filepath in filepaths:
        try:
            path = Path(os.path.realpath(filepath))
            if not str(path).startswith(str(input_root) + os.sep):
                continue
            if path.exists():
                path.unlink()
        except OSError:
            continue

    for filepath in filepaths:
        parent = Path(os.path.realpath(filepath)).parent
        while str(parent).startswith(str(input_root) + os.sep) and parent.exists():
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent


async def wait_for_history(
    session: aiohttp.ClientSession,
    target_node: str,
    prompt_id: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    fail_count = 0

    while True:
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError("Local ComfyUI render timed out.")

        try:
            async with session.get(f"http://{target_node}/history/{prompt_id}") as resp:
                history_data = await resp.json()
        except Exception:
            fail_count += 1
            if fail_count > 5:
                raise RuntimeError(f"Local ComfyUI node {target_node} is unavailable.")
            await asyncio.sleep(2)
            continue

        if prompt_id in history_data:
            return history_data[prompt_id]

        await asyncio.sleep(2)


def remember_pod_submit_job(prompt_id: str, filepaths: list[str]) -> None:
    POD_SUBMIT_JOBS[prompt_id] = {
        "filepaths": list(filepaths),
        "submitted_at": time.time(),
    }


def cleanup_tracked_pod_submit_files(prompt_id: str) -> None:
    job = POD_SUBMIT_JOBS.pop(prompt_id, {})
    filepaths = job.get("filepaths", [])
    if filepaths:
        cleanup_input_files(filepaths)


def get_pod_submit_render_time(prompt_id: str) -> float | None:
    job = POD_SUBMIT_JOBS.get(prompt_id)
    if not job:
        return None

    submitted_at = job.get("submitted_at")
    if not isinstance(submitted_at, (int, float)):
        return None

    return round(time.time() - submitted_at, 2)


async def fetch_history_once(
    session: aiohttp.ClientSession,
    target_node: str,
    prompt_id: str,
) -> dict[str, Any] | None:
    async with session.get(
        f"http://{target_node}/history/{prompt_id}", allow_redirects=False
    ) as response:
        history_data = await response.json()

    if prompt_id in history_data:
        return history_data[prompt_id]

    return None


def build_pod_output_payload(history_entry: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    entries = collect_output_entries(history_entry.get("outputs", {}))
    output: dict[str, list[dict[str, str]]] = {"images": [], "videos": []}

    for entry in entries:
        media_type = guess_media_type(entry["filename"], entry["media_kind"])
        query = urlencode(
            {
                "filename": entry["filename"],
                "subfolder": entry.get("subfolder", ""),
                "media_kind": entry["media_kind"],
            }
        )
        download_query = urlencode(
            {
                "filename": entry["filename"],
                "subfolder": entry.get("subfolder", ""),
                "media_kind": entry["media_kind"],
                "download": "1",
            }
        )
        payload = {
            "filename": entry["filename"],
            "subfolder": entry.get("subfolder", ""),
            "media_type": media_type,
            "url": f"api/comfy-output?{query}",
            "download_url": f"api/comfy-output?{download_query}",
        }
        collection = "videos" if media_type.startswith("video/") else "images"
        output[collection].append(payload)

    return {key: value for key, value in output.items() if value}


class PodDeployRequest(BaseModel):
    gpu_id: str = "NVIDIA L40S"
    volume_id: str = ""
    data_center_id: str = ""


class PodLifecycleActionRequest(BaseModel):
    action: str = Field(..., pattern="^(start|stop|restart|terminate)$")
    pod_id: str = ""


async def call_runpod_mcp(
    session: aiohttp.ClientSession,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not DEFAULT_RUNPOD_API_KEY:
        raise HTTPException(
            status_code=500, detail="RUNPOD_API_KEY is not configured on the server."
        )
    headers = {
        "Authorization": f"Bearer {DEFAULT_RUNPOD_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments or {},
        },
    }
    async with session.post(RUNPOD_MCP_URL, headers=headers, json=body) as resp:
        raw_text = await resp.text()
        if resp.status >= 400:
            raise HTTPException(
                status_code=502,
                detail=f"RunPod MCP error ({resp.status}): {raw_text[:300]}",
            )

    rpc_payload: dict[str, Any] | None = None
    for line in raw_text.splitlines():
        line_str = line.strip()
        if line_str.startswith("data:"):
            candidate = line_str[5:].strip()
            if candidate:
                try:
                    rpc_payload = json.loads(candidate)
                    break
                except json.JSONDecodeError:
                    continue
    if rpc_payload is None:
        try:
            rpc_payload = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Invalid RunPod MCP response: {raw_text[:200]}",
            ) from exc

    result = (rpc_payload or {}).get("result") or {}
    is_error = bool(result.get("isError"))
    content_items = result.get("content") or []
    text_content = ""
    for item in content_items:
        if isinstance(item, dict) and item.get("type") == "text":
            text_content = item.get("text", "")
            break

    if is_error:
        raise HTTPException(status_code=502, detail=text_content or "RunPod MCP tool error.")

    if not text_content:
        return {}
    try:
        return json.loads(text_content)
    except json.JSONDecodeError:
        return {"raw": text_content}


async def find_ltx_pod(session: aiohttp.ClientSession) -> dict[str, Any] | None:
    listed = await call_runpod_mcp(session, "list-pods", {})
    pods = listed.get("pods") or []
    for pod in pods:
        name = (pod.get("name") or "").strip()
        image = (pod.get("image") or "").strip()
        if name == LTX_POD_NAME or image.startswith("notrius/ltx-2.5-serverless"):
            pod_id = pod.get("id")
            if pod_id:
                try:
                    return await call_runpod_mcp(session, "get-pod", {"id": pod_id})
                except Exception:
                    return pod
    return None


async def check_remote_pod_ready(
    session: aiohttp.ClientSession, pod_id: str
) -> bool:
    health_url = f"https://{pod_id}-7777.proxy.runpod.net/health"
    try:
        async with session.get(
            health_url, timeout=aiohttp.ClientTimeout(total=3)
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("status") == "ok"
    except Exception:
        return False
    return False


async def fetch_pod_boot_logs(
    session: aiohttp.ClientSession, pod_id: str
) -> list[str]:
    try:
        log_data = await call_runpod_mcp(
            session,
            "stream-pod-logs",
            {"podId": pod_id, "source": "both", "tail": 40, "maxWaitMs": 800},
        )
        items = log_data.get("items") or []
        cleaned: list[str] = []
        for item in items:
            line = (item.get("line") or "").strip()
            if not line:
                continue
            if re.match(r"^[0-9a-f]{12}\s+(Extracting|Downloading|Waiting|Verifying|Pull complete)", line):
                if cleaned and cleaned[-1].startswith("Docker image pull:"):
                    cleaned[-1] = f"Docker image pull: {line}"
                else:
                    cleaned.append(f"Docker image pull: {line}")
                continue
            if not cleaned or cleaned[-1] != line:
                cleaned.append(line)
        return cleaned[-15:]
    except Exception:
        return []


def format_pod_status_payload(
    pod: dict[str, Any] | None,
    ready: bool,
    logs: list[str],
) -> dict[str, Any]:
    default_vol = {
        "id": LTX_VOLUME_ID,
        "name": LTX_VOLUME_NAME,
        "size_gb": LTX_VOLUME_SIZE_GB,
        "data_center_id": LTX_DATA_CENTER_ID,
        "mount_path": LTX_MOUNT_PATH,
    }
    if not pod:
        return {
            "exists": False,
            "pod": None,
            "logs": [],
            "default_volume": default_vol,
            "gpu_options": LTX_GPU_OPTIONS,
        }

    pod_id = pod.get("id", "")
    status = pod.get("status", "UNKNOWN")
    if status == "RUNNING" and ready:
        phase = "READY"
    elif status == "RUNNING":
        phase = "BOOTSTRAPPING"
    elif status == "EXITED":
        phase = "STOPPED"
    else:
        phase = status

    net_mounts = ((pod.get("mounts") or {}).get("network")) or []
    vol_id = net_mounts[0].get("volumeId") if net_mounts else LTX_VOLUME_ID
    mount_path = net_mounts[0].get("path") if net_mounts else LTX_MOUNT_PATH
    gpu_info = pod.get("gpu") or {}

    return {
        "exists": True,
        "pod": {
            "id": pod_id,
            "name": pod.get("name", LTX_POD_NAME),
            "status": status,
            "ready": ready,
            "phase": phase,
            "cost_per_hr": pod.get("cost"),
            "gpu_name": gpu_info.get("id", "GPU"),
            "cuda_version": pod.get("cudaVersion"),
            "data_center_id": pod.get("dataCenterId", LTX_DATA_CENTER_ID),
            "volume_id": vol_id,
            "mount_path": mount_path,
            "actions": pod.get("actions") or [],
            "comfy_url": f"https://{pod_id}-8188.proxy.runpod.net",
            "app_url": f"https://{pod_id}-7777.proxy.runpod.net",
            "started_at": pod.get("startedAt"),
        },
        "logs": logs,
        "default_volume": default_vol,
        "gpu_options": LTX_GPU_OPTIONS,
    }


@app.get("/api/pod/status")
async def get_pod_status_endpoint() -> dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        pod = await find_ltx_pod(session)
        if not pod:
            return format_pod_status_payload(None, False, [])
        pod_id = pod.get("id", "")
        status = pod.get("status", "")
        ready = False
        logs: list[str] = []
        if status == "RUNNING" and pod_id:
            ready, logs = await asyncio.gather(
                check_remote_pod_ready(session, pod_id),
                fetch_pod_boot_logs(session, pod_id),
            )
        return format_pod_status_payload(pod, ready, logs)


@app.post("/api/pod/deploy")
async def deploy_pod_endpoint(request: PodDeployRequest) -> dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        existing = await find_ltx_pod(session)
        if existing:
            status = existing.get("status", "")
            pod_id = existing.get("id", "")
            if status == "EXITED" and pod_id:
                updated = await call_runpod_mcp(
                    session,
                    "pod-action",
                    {"id": pod_id, "body": {"action": "start"}},
                )
                return format_pod_status_payload(updated or existing, False, [])
            ready = (
                await check_remote_pod_ready(session, pod_id)
                if status == "RUNNING" and pod_id
                else False
            )
            logs = (
                await fetch_pod_boot_logs(session, pod_id)
                if status == "RUNNING" and pod_id
                else []
            )
            return format_pod_status_payload(existing, ready, logs)

        volume_id = request.volume_id.strip() or LTX_VOLUME_ID
        dc_id = request.data_center_id.strip() or LTX_DATA_CENTER_ID
        preferred_gpu = request.gpu_id.strip() or LTX_GPU_OPTIONS[0]["id"]
        candidate_gpus = [preferred_gpu] + [
            g["id"] for g in LTX_GPU_OPTIONS if g["id"] != preferred_gpu
        ]

        last_error = "No 48GB+ GPU stock available in data center."
        for gpu_id in candidate_gpus:
            create_body = {
                "name": LTX_POD_NAME,
                "image": LTX_POD_IMAGE,
                "cloud": "SECURE",
                "dataCenterIds": [dc_id],
                "disk": 60,
                "ports": ["7777/http", "8188/http"],
                "mounts": {
                    "network": [
                        {
                            "volumeId": volume_id,
                            "path": LTX_MOUNT_PATH,
                        }
                    ]
                },
                "gpu": {
                    "id": gpu_id,
                    "count": 1,
                    "minCudaVersion": "13.0",
                },
                "env": {
                    "RUN_MODE": "pod",
                    "PERSIST_WORKSPACE": "true",
                    "LTX25_PRELOAD_VARIANT": "distilled-int8",
                    "LTX25_PRELOAD_PROMPT_ENHANCER": "true",
                    "COMFY_LOG_LEVEL": "INFO",
                    "COMFY_MODEL_DISCOVERY_TIMEOUT_SECONDS": "300",
                    "HUGGINGFACE_ACCESS_TOKEN": LTX_HF_SECRET_REF,
                },
            }
            try:
                created = await call_runpod_mcp(
                    session, "create-pod", {"body": create_body}
                )
                if created and created.get("id"):
                    verified = await call_runpod_mcp(
                        session, "get-pod", {"id": created["id"]}
                    )
                    return format_pod_status_payload(verified or created, False, [])
            except HTTPException as exc:
                last_error = exc.detail
                continue

        raise HTTPException(status_code=503, detail=f"Pod deployment failed: {last_error}")


@app.post("/api/pod/action")
async def pod_action_endpoint(request: PodLifecycleActionRequest) -> dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        pod_id = request.pod_id.strip()
        if not pod_id:
            existing = await find_ltx_pod(session)
            if not existing or not existing.get("id"):
                raise HTTPException(status_code=404, detail="No active LTX pod found.")
            pod_id = existing["id"]

        if request.action == "terminate":
            await call_runpod_mcp(session, "delete-pod", {"id": pod_id})
            return format_pod_status_payload(None, False, [])

        updated = await call_runpod_mcp(
            session,
            "pod-action",
            {"id": pod_id, "body": {"action": request.action}},
        )
        refreshed = await call_runpod_mcp(session, "get-pod", {"id": pod_id})
        pod_obj = refreshed or updated
        ready = False
        logs: list[str] = []
        if pod_obj and pod_obj.get("status") == "RUNNING":
            ready = await check_remote_pod_ready(session, pod_id)
            logs = await fetch_pod_boot_logs(session, pod_id)
        return format_pod_status_payload(pod_obj, ready, logs)


@app.get("/", response_class=FileResponse)
async def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/comfy-output")
async def get_comfy_output(
    filename: str,
    subfolder: str = "",
    media_kind: str = "video",
    download: bool = False,
    pod_id: str = "",
) -> FileResponse:
    safe_name = Path(filename).name
    cached_path = CACHED_OUTPUTS_DIR / f"{pod_id or 'local'}_{safe_name}"
    media_type = guess_media_type(filename, media_kind)

    if is_local_comfy_environment():
        try:
            output_path = build_output_path(
                os.environ.get("COMFY_OUTPUT_DIR", "/comfyui/output"),
                {
                    "filename": filename,
                    "subfolder": subfolder,
                    "media_kind": media_kind,
                },
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if output_path.exists():
            return FileResponse(
                output_path,
                media_type=media_type,
                filename=safe_name if download else None,
            )

    if cached_path.exists():
        return FileResponse(
            cached_path,
            media_type=media_type,
            filename=safe_name if download else None,
        )

    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        target_pod_id = pod_id.strip()
        if not target_pod_id:
            pod = await find_ltx_pod(session)
            if pod and pod.get("id"):
                target_pod_id = pod["id"]
        if not target_pod_id:
            raise HTTPException(status_code=404, detail="Generated output file not found.")

        query = urlencode(
            {
                "filename": filename,
                "subfolder": subfolder,
                "media_kind": media_kind,
            }
        )
        remote_url = f"https://{target_pod_id}-7777.proxy.runpod.net/api/comfy-output?{query}"
        async with session.get(remote_url) as resp:
            if resp.status >= 400:
                raise HTTPException(
                    status_code=resp.status,
                    detail="Failed to fetch generated output from remote Pod.",
                )
            data = await resp.read()
            cached_path = CACHED_OUTPUTS_DIR / f"{target_pod_id}_{safe_name}"
            cached_path.write_bytes(data)
            return FileResponse(
                cached_path,
                media_type=media_type,
                filename=safe_name if download else None,
            )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config")
async def config() -> dict[str, object]:
    default_endpoint_url = (
        f"https://api.runpod.ai/v2/{DEFAULT_RUNPOD_ENDPOINT_ID}/run"
        if DEFAULT_RUNPOD_ENDPOINT_ID
        else ""
    )
    return {
        "fps": FPS,
        "seconds": {
            "min": SECONDS_MIN,
            "max": SECONDS_MAX,
            "step": SECONDS_STEP,
            "default": 5.0,
        },
        "aspect_ratios": ASPECT_RATIOS,
        "text_to_video_enabled": False,
        "run_mode": get_run_mode(),
        "submission_mode": get_submission_mode(),
        "default_endpoint_url": default_endpoint_url,
        "has_server_auth": bool(DEFAULT_RUNPOD_API_KEY),
        "advanced_fields": get_advanced_fields(),
    }


@app.post("/api/payload")
async def create_payload(request: PayloadRequest) -> dict[str, object]:
    try:
        payload = build_payload(
            prompt=request.prompt,
            seconds=request.seconds,
            aspect_ratio=request.aspect_ratio,
            image_name=request.image_name,
            image_data_url=request.image_data_url,
            optimize_prompt=request.optimize_prompt,
            overrides=request.overrides,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    dimensions = ASPECT_RATIOS[request.aspect_ratio]

    return {
        "payload": payload,
        "summary": {
            "frames": seconds_to_frames(request.seconds),
            "seconds": request.seconds,
            "fps": FPS,
            "width": dimensions["width"],
            "height": dimensions["height"],
            "aspect_ratio": request.aspect_ratio,
            "optimize_prompt": request.optimize_prompt,
        },
    }


@app.post("/api/submit")
async def submit_payload(request: SubmitRequest) -> dict[str, object]:
    endpoint_url = get_submit_url(request.endpoint_url)
    headers = {"Content-Type": "application/json"}
    auth_token = request.auth_token.strip() or DEFAULT_RUNPOD_API_KEY
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    timeout = aiohttp.ClientTimeout(total=request.timeout_seconds)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                endpoint_url,
                json=request.payload,
                headers=headers,
                allow_redirects=False,
            ) as response:
                raw_body = await response.text()
                content_type = response.headers.get("Content-Type", "")
                response_json: Any | None = None

                if "json" in content_type.lower():
                    try:
                        response_json = json.loads(raw_body)
                    except json.JSONDecodeError:
                        response_json = None

                return {
                    "ok": 200 <= response.status < 300,
                    "status_code": response.status,
                    "content_type": content_type or "application/octet-stream",
                    "endpoint_url": request.endpoint_url,
                    "response_json": response_json,
                    "response_text": None if response_json is not None else raw_body,
                }
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Submit request timed out.") from exc
    except aiohttp.ClientError as exc:
        raise HTTPException(status_code=502, detail=f"Submit request failed: {exc}") from exc


@app.get("/api/endpoint-status")
async def get_endpoint_status(
    endpoint_url: str,
    job_id: str,
    auth_token: str = "",
) -> dict[str, object]:
    submit_url = get_submit_url(endpoint_url.strip())
    if not re.fullmatch(r"[A-Za-z0-9_-]+", job_id):
        raise HTTPException(status_code=400, detail="Invalid job ID.")

    base_url = re.sub(r"/(run|runsync)$", "", submit_url)
    status_url = f"{base_url}/status/{job_id}"
    headers = {"Content-Type": "application/json"}
    token = auth_token.strip() or DEFAULT_RUNPOD_API_KEY
    if token:
        headers["Authorization"] = f"Bearer {token}"

    timeout = aiohttp.ClientTimeout(total=30)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(status_url, headers=headers) as response:
                raw_body = await response.text()
                content_type = response.headers.get("Content-Type", "")
                response_json: Any | None = None
                try:
                    response_json = json.loads(raw_body)
                except json.JSONDecodeError:
                    response_json = None
                return {
                    "ok": 200 <= response.status < 300,
                    "status_code": response.status,
                    "content_type": content_type or "application/json",
                    "endpoint_url": status_url,
                    "response_json": response_json,
                    "response_text": None if response_json is not None else raw_body,
                }
    except aiohttp.ClientError as exc:
        raise HTTPException(status_code=502, detail=f"Status request failed: {exc}") from exc


@app.post("/api/pod-submit")
async def submit_payload_in_pod(request: PodSubmitRequest) -> dict[str, object]:
    if not is_local_comfy_environment():
        timeout = aiohttp.ClientTimeout(total=request.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            pod = await find_ltx_pod(session)
            if not pod or pod.get("status") != "RUNNING" or not pod.get("id"):
                raise HTTPException(
                    status_code=503,
                    detail="LTX Pod is not running. Deploy or start the Pod in the Pod Control panel first.",
                )
            pod_id = pod["id"]
            if not await check_remote_pod_ready(session, pod_id):
                raise HTTPException(
                    status_code=503,
                    detail="LTX Pod is still bootstrapping models and ComfyUI. Please wait until status shows Ready.",
                )
            remote_submit_url = f"https://{pod_id}-7777.proxy.runpod.net/api/pod-submit"
            try:
                async with session.post(
                    remote_submit_url,
                    json={"payload": request.payload, "timeout_seconds": request.timeout_seconds},
                ) as resp:
                    raw_body = await resp.text()
                    if resp.status >= 400:
                        try:
                            err_json = json.loads(raw_body)
                            detail = err_json.get("detail") or raw_body
                        except json.JSONDecodeError:
                            detail = raw_body or "Remote Pod rejected workflow submission."
                        raise HTTPException(status_code=resp.status, detail=detail)
                    data = json.loads(raw_body)
                    if isinstance(data.get("response_json"), dict):
                        data["response_json"]["pod_id"] = pod_id
                    return data
            except aiohttp.ClientError as exc:
                raise HTTPException(
                    status_code=502, detail=f"Remote Pod submit failed: {exc}"
                ) from exc

    workflow_input = request.payload.get("input", {})
    workflow = workflow_input.get("workflow")
    images = workflow_input.get("images")
    if not isinstance(workflow, dict):
        raise HTTPException(
            status_code=400,
            detail="Payload must include input.workflow for pod submission.",
        )

    try:
        name_map, prepared_images = prepare_pod_images(images)
        prepared_workflow = apply_input_filename_map(workflow, name_map)
        written_input_files = write_input_images(COMFY_INPUT_DIR, prepared_images)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        target_node = get_pod_submit_node()
    except RuntimeError as exc:
        cleanup_input_files(written_input_files)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    timeout = aiohttp.ClientTimeout(total=request.timeout_seconds)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"http://{target_node}/prompt",
                json={"prompt": prepared_workflow},
            ) as response:
                raw_body = await response.text()
                try:
                    prompt_response = json.loads(raw_body)
                except json.JSONDecodeError:
                    prompt_response = {}
                prompt_id = prompt_response.get("prompt_id")
                if response.status >= 400 or not prompt_id:
                    detail = "Local ComfyUI rejected the workflow submit."
                    if isinstance(prompt_response, dict) and prompt_response:
                        detail = prompt_response.get("message") or json.dumps(
                            prompt_response
                        )
                    elif raw_body.strip():
                        detail = raw_body.strip()
                    raise HTTPException(
                        status_code=502,
                        detail=detail,
                    )
        remember_pod_submit_job(prompt_id, written_input_files)
        return {
            "ok": True,
            "status_code": 202,
            "content_type": "application/json",
            "response_json": {
                "prompt_id": prompt_id,
                "node_used": target_node,
                "status": "queued",
            },
            "response_text": None,
        }
    except TimeoutError as exc:
        cleanup_input_files(written_input_files)
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except aiohttp.ClientError as exc:
        cleanup_input_files(written_input_files)
        raise HTTPException(
            status_code=502,
            detail=f"Local ComfyUI request failed: {exc}",
        ) from exc
    except HTTPException:
        cleanup_input_files(written_input_files)
        raise
    except Exception:
        cleanup_input_files(written_input_files)
        raise


@app.get("/api/pod-submit/{prompt_id}")
async def get_pod_submit_status(
    prompt_id: str,
    node: str = "",
) -> dict[str, object]:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", prompt_id):
        raise HTTPException(status_code=400, detail="Invalid prompt ID.")

    if not is_local_comfy_environment():
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            pod = await find_ltx_pod(session)
            if not pod or pod.get("status") != "RUNNING" or not pod.get("id"):
                raise HTTPException(
                    status_code=503,
                    detail="LTX Pod is no longer running.",
                )
            pod_id = pod["id"]
            remote_status_url = (
                f"https://{pod_id}-7777.proxy.runpod.net/api/pod-submit/{prompt_id}"
            )
            try:
                async with session.get(remote_status_url) as resp:
                    raw_body = await resp.text()
                    if resp.status >= 400:
                        try:
                            err_json = json.loads(raw_body)
                            detail = err_json.get("detail") or raw_body
                        except json.JSONDecodeError:
                            detail = raw_body or "Remote Pod status check failed."
                        raise HTTPException(status_code=resp.status, detail=detail)
                    data = json.loads(raw_body)
                    rjson = data.get("response_json") or {}
                    output = rjson.get("output") or {}
                    for kind in ("videos", "images"):
                        for item in output.get(kind) or []:
                            for url_key in ("url", "download_url"):
                                val = item.get(url_key) or ""
                                if val.startswith("/"):
                                    val = val.lstrip("/")
                                if val and f"pod_id={pod_id}" not in val:
                                    sep = "&" if "?" in val else "?"
                                    val = f"{val}{sep}pod_id={pod_id}"
                                item[url_key] = val
                            fname = item.get("filename")
                            if fname:
                                safe_name = Path(fname).name
                                cached_path = CACHED_OUTPUTS_DIR / f"{pod_id}_{safe_name}"
                                if not cached_path.exists():
                                    dl_q = urlencode(
                                        {
                                            "filename": fname,
                                            "subfolder": item.get("subfolder", ""),
                                            "media_kind": "video" if kind == "videos" else "image",
                                        }
                                    )
                                    fetch_url = f"https://{pod_id}-7777.proxy.runpod.net/api/comfy-output?{dl_q}"
                                    try:
                                        async with session.get(fetch_url) as media_resp:
                                            if media_resp.status == 200:
                                                cached_path.write_bytes(await media_resp.read())
                                    except Exception:
                                        pass
                    return data
            except aiohttp.ClientError as exc:
                raise HTTPException(
                    status_code=502, detail=f"Remote Pod status request failed: {exc}"
                ) from exc
    try:
        target_node = get_pod_submit_node()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    try:
        requested_node = normalize_node_host(node) if node.strip() else target_node
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if requested_node != target_node:
        raise HTTPException(status_code=400, detail="Node must match LOCAL_COMFY_NODE.")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", prompt_id):
        raise HTTPException(status_code=400, detail="Invalid prompt ID.")

    timeout = aiohttp.ClientTimeout(total=10)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            history_entry = await fetch_history_once(session, target_node, prompt_id)
    except aiohttp.ClientError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Local ComfyUI status request failed: {exc}",
        ) from exc

    if history_entry is None:
        return {
            "ok": True,
            "status_code": 202,
            "content_type": "application/json",
            "response_json": {
                "prompt_id": prompt_id,
                "node_used": target_node,
                "status": "running",
            },
            "response_text": None,
        }

    render_time_sec = get_pod_submit_render_time(prompt_id)
    cleanup_tracked_pod_submit_files(prompt_id)
    return {
        "ok": True,
        "status_code": 200,
        "content_type": "application/json",
        "response_json": {
            "prompt_id": prompt_id,
            "node_used": target_node,
            "status": "completed",
            "metadata": {
                "render_time_sec": render_time_sec,
            },
            "output": build_pod_output_payload(history_entry),
        },
        "response_text": None,
    }
