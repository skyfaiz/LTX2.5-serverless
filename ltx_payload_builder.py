from __future__ import annotations

import copy
import json
import random
import re
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent
WORKFLOW_TEMPLATE_PATH = ROOT_DIR / "video_ltx2_5_i2v_API.json"

FPS = 24
SECONDS_MIN = 1.0
SECONDS_MAX = 20.0
SECONDS_STEP = 1.0

ASPECT_RATIOS: dict[str, dict[str, int]] = {
    "16:9": {"width": 1280, "height": 720},
    "9:16": {"width": 720, "height": 1280},
    "1:1": {"width": 1024, "height": 1024},
}

PROMPT_NODE = "398:376"
IMAGE_NODE = "395"
DURATION_NODE = "398:362"
WIDTH_NODE = "398:372"
HEIGHT_NODE = "398:360"
FPS_NODE = "398:361"
PROMPT_OPTIMIZER_NODE = "398:380"
PROMPT_OPTIMIZER_TOGGLE_NODE = "398:383"
SEED_NODE_1 = "398:338"
SEED_NODE_2 = "398:339"

with WORKFLOW_TEMPLATE_PATH.open("r", encoding="utf-8") as template_file:
    WORKFLOW_TEMPLATE = json.load(template_file)

SAMPLER_OPTIONS = [
    "euler_ancestral",
    "euler",
    "heun",
    "dpm_2",
    "dpm_2_ancestral",
    "uni_pc",
    "uni_pc_bh2",
    "lcm",
    "ddim",
]

# Curated set of ComfyUI workflow inputs that can be overridden from the
# frontend. Defaults are read live from WORKFLOW_TEMPLATE so the UI is always
# prefilled with the current workflow values.
ADVANCED_FIELDS: list[dict[str, Any]] = [
    {
        "id": "sampler_1",
        "node": "398:352",
        "key": "sampler_name",
        "group": "Sampling · pass 1",
        "label": "Sampler",
        "type": "select",
        "options": SAMPLER_OPTIONS,
    },
    {
        "id": "sigmas_1",
        "node": "398:397",
        "key": "sigmas",
        "group": "Sampling · pass 1",
        "label": "Sigma schedule",
        "type": "text",
        "help": "Comma-separated sigmas ending in 0 — steps = values − 1 (default = 7 steps).",
    },
    {
        "id": "video_cfg_1",
        "node": "398:388",
        "key": "video_cfg",
        "group": "Sampling · pass 1",
        "label": "Video CFG",
        "type": "number",
        "min": 0,
        "max": 30,
        "step": 0.1,
    },
    {
        "id": "audio_cfg_1",
        "node": "398:388",
        "key": "audio_cfg",
        "group": "Sampling · pass 1",
        "label": "Audio CFG",
        "type": "number",
        "min": 0,
        "max": 30,
        "step": 0.1,
    },
    {
        "id": "strength_1",
        "node": "398:357",
        "key": "strength",
        "group": "Sampling · pass 1",
        "label": "I2V strength",
        "type": "number",
        "min": 0,
        "max": 1,
        "step": 0.05,
        "help": "How strongly the source frame anchors the generation.",
    },
    {
        "id": "sampler_2",
        "node": "398:341",
        "key": "sampler_name",
        "group": "Sampling · pass 2 (2× upscale refine)",
        "label": "Sampler",
        "type": "select",
        "options": SAMPLER_OPTIONS,
    },
    {
        "id": "sigmas_2",
        "node": "398:396",
        "key": "sigmas",
        "group": "Sampling · pass 2 (2× upscale refine)",
        "label": "Sigma schedule",
        "type": "text",
        "help": "Comma-separated sigmas ending in 0 — steps = values − 1 (default = 3 steps).",
    },
    {
        "id": "video_cfg_2",
        "node": "398:391",
        "key": "video_cfg",
        "group": "Sampling · pass 2 (2× upscale refine)",
        "label": "Video CFG",
        "type": "number",
        "min": 0,
        "max": 30,
        "step": 0.1,
    },
    {
        "id": "audio_cfg_2",
        "node": "398:391",
        "key": "audio_cfg",
        "group": "Sampling · pass 2 (2× upscale refine)",
        "label": "Audio CFG",
        "type": "number",
        "min": 0,
        "max": 30,
        "step": 0.1,
    },
    {
        "id": "strength_2",
        "node": "398:349",
        "key": "strength",
        "group": "Sampling · pass 2 (2× upscale refine)",
        "label": "I2V strength",
        "type": "number",
        "min": 0,
        "max": 1,
        "step": 0.05,
    },
    {
        "id": "seed",
        "special": "seed",
        "group": "Seed",
        "label": "Seed (−1 = random)",
        "type": "number",
        "step": 1,
        "value": -1,
        "help": "Applied to both sampling passes; -1 keeps random seeds per run.",
    },
    {
        "id": "negative_prompt",
        "node": "398:373",
        "key": "text",
        "group": "Prompt & conditioning",
        "label": "Negative prompt",
        "type": "textarea",
    },
    {
        "id": "enhancer_max_length",
        "node": "398:380",
        "key": "max_length",
        "group": "Prompt enhancer (AI optimize)",
        "label": "Max length",
        "type": "number",
        "min": 32,
        "max": 2048,
        "step": 1,
        "help": "Only applies when 'Optimize with AI' is enabled.",
    },
    {
        "id": "enhancer_temperature",
        "node": "398:380",
        "key": "sampling_mode.temperature",
        "group": "Prompt enhancer (AI optimize)",
        "label": "Temperature",
        "type": "number",
        "min": 0,
        "max": 2,
        "step": 0.05,
    },
    {
        "id": "enhancer_top_p",
        "node": "398:380",
        "key": "sampling_mode.top_p",
        "group": "Prompt enhancer (AI optimize)",
        "label": "Top-p",
        "type": "number",
        "min": 0,
        "max": 1,
        "step": 0.01,
    },
    {
        "id": "enhancer_top_k",
        "node": "398:380",
        "key": "sampling_mode.top_k",
        "group": "Prompt enhancer (AI optimize)",
        "label": "Top-k",
        "type": "number",
        "min": 1,
        "max": 1000,
        "step": 1,
    },
    {
        "id": "enhancer_min_p",
        "node": "398:380",
        "key": "sampling_mode.min_p",
        "group": "Prompt enhancer (AI optimize)",
        "label": "Min-p",
        "type": "number",
        "min": 0,
        "max": 1,
        "step": 0.01,
    },
    {
        "id": "enhancer_rep_penalty",
        "node": "398:380",
        "key": "sampling_mode.repetition_penalty",
        "group": "Prompt enhancer (AI optimize)",
        "label": "Repetition penalty",
        "type": "number",
        "min": 1,
        "max": 2,
        "step": 0.01,
    },
    {
        "id": "preprocess_longer_size",
        "node": "398:351",
        "key": "resize_type.longer_size",
        "group": "Preprocessing",
        "label": "Source resize · longer side (px)",
        "type": "number",
        "min": 256,
        "max": 4096,
        "step": 32,
    },
    {
        "id": "img_compression",
        "node": "398:350",
        "key": "img_compression",
        "group": "Preprocessing",
        "label": "Image compression (0–100)",
        "type": "number",
        "min": 0,
        "max": 100,
        "step": 1,
        "help": "Lower = cleaner conditioning frame; higher = more motion freedom.",
    },
    {
        "id": "vae_tile_size",
        "node": "398:374",
        "key": "tile_size",
        "group": "VAE decode",
        "label": "Spatial tile size",
        "type": "number",
        "min": 64,
        "max": 2048,
        "step": 64,
    },
    {
        "id": "vae_temporal_size",
        "node": "398:374",
        "key": "temporal_size",
        "group": "VAE decode",
        "label": "Temporal tile size",
        "type": "number",
        "min": 8,
        "max": 256,
        "step": 8,
    },
]


def get_advanced_fields() -> list[dict[str, Any]]:
    """Return the editable workflow fields prefilled with template values."""
    fields: list[dict[str, Any]] = []
    for spec in ADVANCED_FIELDS:
        field = {k: v for k, v in spec.items() if k not in {"node", "key", "special"}}
        if spec.get("special") == "seed":
            field["value"] = spec.get("value", -1)
        else:
            field["value"] = WORKFLOW_TEMPLATE[spec["node"]]["inputs"][spec["key"]]
        fields.append(field)
    return fields


def _coerce_override_value(spec: dict[str, Any], value: Any) -> Any:
    label = spec["label"]
    if spec["type"] == "select":
        value = str(value)
        if value not in spec.get("options", []):
            raise ValueError(f"{label}: unsupported option '{value}'.")
        return value

    if spec["type"] == "textarea":
        return str(value)

    if spec["key"] == "sigmas":
        raw = str(value).strip()
        try:
            parsed = [float(part) for part in raw.split(",") if part.strip()]
        except ValueError as exc:
            raise ValueError(f"{label} must be comma-separated numbers.") from exc
        if len(parsed) < 2:
            raise ValueError(f"{label} needs at least two sigma values.")
        return raw

    # Fractional step (0.1, 0.05, 0.01) means the workflow input is a float;
    # whole-step fields map to ComfyUI int inputs.
    step = spec.get("step")
    wants_float = isinstance(step, float) and not float(step).is_integer()
    try:
        parsed_number = float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a number.") from exc
    coerced: Any = parsed_number if wants_float else int(parsed_number)

    min_value = spec.get("min")
    max_value = spec.get("max")
    if min_value is not None and coerced < min_value:
        raise ValueError(f"{label} must be >= {min_value}.")
    if max_value is not None and coerced > max_value:
        raise ValueError(f"{label} must be <= {max_value}.")
    return coerced


def apply_workflow_overrides(
    workflow: dict[str, Any], overrides: dict[str, Any] | None
) -> None:
    if not overrides:
        return

    specs = {spec["id"]: spec for spec in ADVANCED_FIELDS}
    unknown = sorted(key for key in overrides if key not in specs)
    if unknown:
        raise ValueError(f"Unknown advanced settings: {', '.join(unknown)}")

    for field_id, value in overrides.items():
        spec = specs[field_id]
        if spec.get("special"):
            continue
        workflow[spec["node"]]["inputs"][spec["key"]] = _coerce_override_value(
            spec, value
        )


def resolve_seed(overrides: dict[str, Any] | None) -> int | None:
    if not overrides or "seed" not in overrides:
        return None
    try:
        seed_value = int(float(str(overrides["seed"]).strip()))
    except (TypeError, ValueError) as exc:
        raise ValueError("Seed must be an integer.") from exc
    return seed_value if seed_value >= 0 else None


def seconds_to_frames(seconds: float) -> int:
    if seconds < SECONDS_MIN or seconds > SECONDS_MAX:
        raise ValueError(
            f"Duration must be between {SECONDS_MIN:g} and {SECONDS_MAX:g} seconds."
        )
    if not float(seconds).is_integer():
        raise ValueError("Duration must be a whole number of seconds.")
    return int(round(seconds * FPS)) + 1


def sanitize_image_name(image_name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", image_name.strip())
    cleaned = cleaned.strip("._")
    return cleaned or "source-image.png"


def build_payload(
    *,
    prompt: str,
    seconds: float,
    aspect_ratio: str,
    image_name: str,
    image_data_url: str,
    optimize_prompt: bool,
    overrides: dict[str, Any] | None = None,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("Prompt is required.")

    if aspect_ratio not in ASPECT_RATIOS:
        raise ValueError(f"Unsupported aspect ratio: {aspect_ratio}")

    if not image_data_url.startswith("data:image/"):
        raise ValueError("Source image must be provided as a data URL.")

    dimensions = ASPECT_RATIOS[aspect_ratio]
    seconds_to_frames(seconds)
    normalized_image_name = sanitize_image_name(image_name)
    random_source = rng or random.SystemRandom()

    workflow = copy.deepcopy(WORKFLOW_TEMPLATE)
    workflow[PROMPT_NODE]["inputs"]["value"] = prompt
    workflow[IMAGE_NODE]["inputs"]["image"] = normalized_image_name
    workflow[DURATION_NODE]["inputs"]["value"] = int(seconds)
    workflow[WIDTH_NODE]["inputs"]["value"] = dimensions["width"]
    workflow[HEIGHT_NODE]["inputs"]["value"] = dimensions["height"]
    workflow[FPS_NODE]["inputs"]["value"] = FPS
    workflow[PROMPT_OPTIMIZER_NODE]["inputs"]["sampling_mode"] = (
        "on" if optimize_prompt else "off"
    )
    workflow[PROMPT_OPTIMIZER_TOGGLE_NODE]["inputs"]["value"] = optimize_prompt
    workflow[PROMPT_OPTIMIZER_NODE]["inputs"]["sampling_mode.seed"] = random_source.randrange(
        1, 10**9
    )

    fixed_seed = resolve_seed(overrides)
    workflow[SEED_NODE_1]["inputs"]["noise_seed"] = (
        fixed_seed
        if fixed_seed is not None
        else random_source.randrange(1, 10**15)
    )
    workflow[SEED_NODE_2]["inputs"]["noise_seed"] = (
        fixed_seed
        if fixed_seed is not None
        else random_source.randrange(1, 10**15)
    )

    apply_workflow_overrides(workflow, overrides)

    return {
        "input": {
            "workflow": workflow,
            "images": [
                {
                    "name": normalized_image_name,
                    "image": image_data_url,
                }
            ],
        }
    }
