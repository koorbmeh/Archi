"""
Hardware detection and VRAM budget calculation.
Detects NVIDIA GPU, available VRAM, and system RAM to inform backend selection.
"""

import subprocess
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GPUInfo:
    name: str
    vram_total_mb: int
    vram_free_mb: int
    cuda_version: Optional[str] = None
    compute_capability: Optional[str] = None

    @property
    def vram_total_gb(self) -> float:
        return self.vram_total_mb / 1024

    @property
    def vram_free_gb(self) -> float:
        return self.vram_free_mb / 1024


@dataclass
class SystemInfo:
    ram_total_mb: int
    ram_available_mb: int
    gpus: list[GPUInfo] = field(default_factory=list)
    cuda_available: bool = False

    @property
    def primary_gpu(self) -> Optional[GPUInfo]:
        return self.gpus[0] if self.gpus else None

    @property
    def has_gpu(self) -> bool:
        return len(self.gpus) > 0

    def print_summary(self):
        print("=" * 50)
        print("SYSTEM HARDWARE")
        print("=" * 50)
        print(f"RAM: {self.ram_total_mb / 1024:.1f} GB total, "
              f"{self.ram_available_mb / 1024:.1f} GB available")
        print(f"CUDA Available: {self.cuda_available}")

        if self.gpus:
            for i, gpu in enumerate(self.gpus):
                print(f"\nGPU {i}: {gpu.name}")
                print(f"  VRAM: {gpu.vram_total_gb:.1f} GB total, "
                      f"{gpu.vram_free_gb:.1f} GB free")
                if gpu.cuda_version:
                    print(f"  CUDA: {gpu.cuda_version}")
                if gpu.compute_capability:
                    print(f"  Compute Capability: {gpu.compute_capability}")
        else:
            print("\nNo NVIDIA GPU detected — will use CPU inference")
        print("=" * 50)

    def recommend_quantization(self, model_params_b: float) -> str:
        """
        Given model size in billions of parameters, recommend quantization.
        Rule of thumb: Q4_K_M ≈ 0.6 GB per billion params
                       Q5_K_M ≈ 0.7 GB per billion params
                       Q8_0   ≈ 1.1 GB per billion params
                       FP16   ≈ 2.0 GB per billion params
        """
        if not self.has_gpu:
            # CPU mode — use most aggressive quantization
            return "Q4_K_M"

        vram_gb = self.primary_gpu.vram_free_gb
        # Leave ~1.5 GB headroom for KV cache and overhead
        usable_vram = vram_gb - 1.5

        fp16_size = model_params_b * 2.0
        q8_size = model_params_b * 1.1
        q5_size = model_params_b * 0.7
        q4_size = model_params_b * 0.6

        if fp16_size <= usable_vram:
            return "FP16"
        elif q8_size <= usable_vram:
            return "Q8_0"
        elif q5_size <= usable_vram:
            return "Q5_K_M"
        elif q4_size <= usable_vram:
            return "Q4_K_M"
        else:
            return "TOO_LARGE"


def detect_hardware() -> SystemInfo:
    """Detect system hardware: RAM, GPU(s), CUDA availability."""
    import psutil

    ram = psutil.virtual_memory()
    info = SystemInfo(
        ram_total_mb=int(ram.total / (1024 * 1024)),
        ram_available_mb=int(ram.available / (1024 * 1024)),
    )

    # Try nvidia-smi for GPU detection
    gpus = _detect_nvidia_gpus()
    if gpus:
        info.gpus = gpus
        info.cuda_available = True
    else:
        # Try PyTorch as fallback
        info.cuda_available = _check_torch_cuda()

    return info


def _detect_nvidia_gpus() -> list[GPUInfo]:
    """Use nvidia-smi to detect GPUs."""
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.free,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return []

        gpus = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                gpus.append(GPUInfo(
                    name=parts[0],
                    vram_total_mb=int(float(parts[1])),
                    vram_free_mb=int(float(parts[2])),
                ))

        # Get CUDA version separately
        cuda_result = subprocess.run(
            ["nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
        if cuda_result.returncode == 0:
            caps = cuda_result.stdout.strip().split("\n")
            for i, cap in enumerate(caps):
                if i < len(gpus):
                    gpus[i].compute_capability = cap.strip()

        return gpus

    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []


def _check_torch_cuda() -> bool:
    """Fallback: check if PyTorch CUDA is available."""
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False


if __name__ == "__main__":
    info = detect_hardware()
    info.print_summary()

    # Example recommendation
    for size in [3, 7, 8, 13, 14, 32, 70]:
        rec = info.recommend_quantization(size)
        print(f"  {size}B model → {rec}")
