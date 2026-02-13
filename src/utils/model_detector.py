"""
Model format detection.
Given a path or identifier, figure out what kind of model it is
and what backend should handle it.
"""

import logging
import os
import json
import glob
from pathlib import Path
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class ModelFormat(Enum):
    GGUF = "gguf"
    SAFETENSORS = "safetensors"
    PYTORCH_BIN = "pytorch_bin"
    HUGGINGFACE_REPO = "huggingface_repo"  # Remote repo ID like "Qwen/Qwen2.5-7B"
    UNKNOWN = "unknown"


@dataclass
class ModelInfo:
    """Everything we know about a model before loading it."""
    path: str                          # File path or HF repo ID
    format: ModelFormat
    name: str                          # Human-friendly name
    size_bytes: Optional[int] = None   # Total size on disk
    estimated_params_b: Optional[float] = None  # Estimated param count in billions
    architecture: Optional[str] = None  # e.g., "llama", "qwen2", "mistral"
    quantization: Optional[str] = None  # e.g., "Q4_K_M", "Q8_0", "FP16"
    context_length: Optional[int] = None
    is_local: bool = True

    def print_summary(self):
        print(f"Model: {self.name}")
        print(f"  Format: {self.format.value}")
        print(f"  Path: {self.path}")
        if self.size_bytes:
            gb = self.size_bytes / (1024 ** 3)
            print(f"  Size: {gb:.2f} GB")
        if self.estimated_params_b:
            print(f"  Est. Parameters: {self.estimated_params_b:.1f}B")
        if self.architecture:
            print(f"  Architecture: {self.architecture}")
        if self.quantization:
            print(f"  Quantization: {self.quantization}")
        if self.context_length:
            print(f"  Context Length: {self.context_length}")


def detect_model(path: str) -> ModelInfo:
    """
    Detect model format and extract metadata.

    Args:
        path: Can be:
            - A .gguf file path
            - A directory containing safetensors/bin files
            - A HuggingFace repo ID (e.g., "Qwen/Qwen2.5-7B-Instruct")
    """
    # Check if it's a HuggingFace repo ID (contains "/" but isn't a file path)
    if "/" in path and not os.path.exists(path):
        # Could be a HF repo ID like "Qwen/Qwen2.5-7B-Instruct"
        if not path.startswith(("/", ".", "~", "C:", "D:")):
            return ModelInfo(
                path=path,
                format=ModelFormat.HUGGINGFACE_REPO,
                name=path.split("/")[-1],
                is_local=False,
                estimated_params_b=_guess_params_from_name(path),
            )

    path = os.path.expanduser(path)

    if not os.path.exists(path):
        raise FileNotFoundError(f"Model path not found: {path}")

    # Single GGUF file
    if os.path.isfile(path) and path.lower().endswith(".gguf"):
        return _detect_gguf(path)

    # Directory — look for model files
    if os.path.isdir(path):
        return _detect_directory(path)

    # Single safetensors file
    if os.path.isfile(path) and path.lower().endswith(".safetensors"):
        return ModelInfo(
            path=path,
            format=ModelFormat.SAFETENSORS,
            name=Path(path).stem,
            size_bytes=os.path.getsize(path),
        )

    # Single .bin file
    if os.path.isfile(path) and path.lower().endswith(".bin"):
        return ModelInfo(
            path=path,
            format=ModelFormat.PYTORCH_BIN,
            name=Path(path).stem,
            size_bytes=os.path.getsize(path),
        )

    return ModelInfo(
        path=path,
        format=ModelFormat.UNKNOWN,
        name=Path(path).name,
    )


def _detect_gguf(path: str) -> ModelInfo:
    """Extract metadata from a GGUF file."""
    name = Path(path).stem
    size = os.path.getsize(path)

    # Parse quantization from filename (common convention)
    quant = _parse_quantization_from_name(name)
    params = _guess_params_from_name(name)
    arch = _guess_architecture_from_name(name)

    return ModelInfo(
        path=path,
        format=ModelFormat.GGUF,
        name=name,
        size_bytes=size,
        quantization=quant,
        estimated_params_b=params,
        architecture=arch,
    )


def _detect_directory(path: str) -> ModelInfo:
    """Detect model format from a directory (HF-style model folder)."""
    safetensors = glob.glob(os.path.join(path, "*.safetensors"))
    bins = glob.glob(os.path.join(path, "*.bin"))
    ggufs = glob.glob(os.path.join(path, "*.gguf"))

    # Prioritize: GGUF > safetensors > bin
    if ggufs:
        # If there's a single GGUF, use it directly
        if len(ggufs) == 1:
            return _detect_gguf(ggufs[0])
        # Multiple GGUFs — return the directory info
        return ModelInfo(
            path=path,
            format=ModelFormat.GGUF,
            name=Path(path).name,
            size_bytes=sum(os.path.getsize(f) for f in ggufs),
        )

    if safetensors:
        total_size = sum(os.path.getsize(f) for f in safetensors)
        info = ModelInfo(
            path=path,
            format=ModelFormat.SAFETENSORS,
            name=Path(path).name,
            size_bytes=total_size,
        )
        # Try to read config.json for more details
        _enrich_from_config(path, info)
        return info

    if bins:
        total_size = sum(os.path.getsize(f) for f in bins)
        info = ModelInfo(
            path=path,
            format=ModelFormat.PYTORCH_BIN,
            name=Path(path).name,
            size_bytes=total_size,
        )
        _enrich_from_config(path, info)
        return info

    return ModelInfo(
        path=path,
        format=ModelFormat.UNKNOWN,
        name=Path(path).name,
    )


def _enrich_from_config(directory: str, info: ModelInfo):
    """Read config.json to get architecture, context length, etc."""
    config_path = os.path.join(directory, "config.json")
    if not os.path.exists(config_path):
        return

    try:
        with open(config_path) as f:
            config = json.load(f)

        # Architecture
        arch = config.get("model_type", "").lower()
        if arch:
            info.architecture = arch

        # Context length (different models use different keys)
        for key in ["max_position_embeddings", "max_seq_len", "seq_length",
                     "n_positions", "sliding_window"]:
            if key in config:
                info.context_length = config[key]
                break

        # Estimate parameters from hidden size and layers
        hidden = config.get("hidden_size", 0)
        layers = config.get("num_hidden_layers", 0)
        vocab = config.get("vocab_size", 0)
        if hidden and layers:
            # Very rough estimate: params ≈ 12 * layers * hidden^2
            est = 12 * layers * (hidden ** 2)
            est += vocab * hidden * 2  # Embedding + output
            info.estimated_params_b = est / 1e9

    except (json.JSONDecodeError, KeyError):
        pass


def _parse_quantization_from_name(name: str) -> Optional[str]:
    """Extract quantization level from model filename."""
    name_upper = name.upper()
    quant_patterns = [
        "Q2_K", "Q3_K_S", "Q3_K_M", "Q3_K_L",
        "Q4_0", "Q4_1", "Q4_K_S", "Q4_K_M",
        "Q5_0", "Q5_1", "Q5_K_S", "Q5_K_M",
        "Q6_K", "Q8_0",
        "IQ1_S", "IQ1_M", "IQ2_XXS", "IQ2_XS", "IQ2_S", "IQ2_M",
        "IQ3_XXS", "IQ3_XS", "IQ3_S", "IQ3_M",
        "IQ4_NL", "IQ4_XS",
        "F16", "F32", "FP16", "FP32", "BF16",
    ]
    for pattern in quant_patterns:
        if pattern in name_upper or pattern.replace("_", "-") in name_upper:
            return pattern
    return None


def _guess_params_from_name(name: str) -> Optional[float]:
    """Try to guess parameter count from model name."""
    import re
    name_lower = name.lower()
    # Match patterns like "7b", "13b", "70b", "1.5b", "0.5b"
    match = re.search(r'(\d+\.?\d*)\s*b(?:illion)?(?:\b|[-_])', name_lower)
    if match:
        return float(match.group(1))
    return None


def _guess_architecture_from_name(name: str) -> Optional[str]:
    """Guess model architecture from filename."""
    name_lower = name.lower()
    architectures = {
        "llama": "llama",
        "mistral": "mistral",
        "mixtral": "mixtral",
        "qwen": "qwen2",
        "phi": "phi",
        "gemma": "gemma",
        "deepseek": "deepseek",
        "yi": "yi",
        "command": "command-r",
        "nemotron": "nemotron",
        "internlm": "internlm",
    }
    for keyword, arch in architectures.items():
        if keyword in name_lower:
            return arch
    return None


def scan_models_directory(models_dir: str) -> list[ModelInfo]:
    """Scan a directory for all available models."""
    if not os.path.isdir(models_dir):
        return []

    models = []
    for entry in os.scandir(models_dir):
        if entry.name.startswith("."):
            continue
        try:
            if entry.is_file() and entry.name.endswith((".gguf", ".safetensors", ".bin")):
                models.append(detect_model(entry.path))
            elif entry.is_dir():
                info = detect_model(entry.path)
                if info.format != ModelFormat.UNKNOWN:
                    models.append(info)
        except Exception as e:
            logger.warning("Could not detect %s: %s", entry.name, e)

    return models


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        info = detect_model(sys.argv[1])
        info.print_summary()
    else:
        print("Usage: python model_detector.py <path_or_repo_id>")
