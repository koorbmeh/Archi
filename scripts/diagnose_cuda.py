"""
Diagnostic script to find all CUDA installations and check compatibility.
Run: .\venv\Scripts\python.exe scripts\diagnose_cuda.py
"""
# Apply CUDA bootstrap before any code that loads llama_cpp (it uses CUDA_PATH\bin\x64)
import src.core.cuda_bootstrap  # noqa: F401

import os
from pathlib import Path

print("CUDA Diagnostic Report")
print("=" * 70)

# 1. Check environment variables
print("\n1. Environment Variables:")
print("-" * 70)
cuda_path = os.environ.get("CUDA_PATH")
print(f"CUDA_PATH: {cuda_path if cuda_path else 'Not set'}")

path_entries = os.environ.get("PATH", "").split(";")
cuda_in_path = [p for p in path_entries if "CUDA" in p.upper()]
if cuda_in_path:
    print("\nCUDA in PATH:")
    for p in cuda_in_path:
        exists = "✓" if os.path.exists(p) else "✗"
        print(f"  {exists} {p}")
else:
    print("No CUDA entries in PATH")

# 2. Check common installation locations
print("\n2. Checking Common CUDA Locations:")
print("-" * 70)

locations_to_check = [
    r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA",
    r"C:\Program Files\NVIDIA\CUDA",
    r"C:\Tools\CUDA",
    r"C:\CUDA",
]

found_versions = []

for base_path in locations_to_check:
    if os.path.exists(base_path):
        print(f"\n✓ Found: {base_path}")
        try:
            versions = [d for d in os.listdir(base_path) if d.startswith("v")]
            if versions:
                print(f"  Versions: {', '.join(versions)}")
                for v in sorted(versions):
                    version_path = os.path.join(base_path, v)
                    bin_path = os.path.join(version_path, "bin")
                    bin_x64_path = os.path.join(version_path, "bin", "x64")

                    has_bin = os.path.isdir(bin_path)
                    has_bin_x64 = os.path.isdir(bin_x64_path)

                    print(f"    {v}:")
                    print(f"      bin: {'✓' if has_bin else '✗'}")
                    print(f"      bin\\x64: {'✓' if has_bin_x64 else '✗'}")

                    if has_bin:
                        use_bin = bin_x64_path if has_bin_x64 else bin_path
                        found_versions.append(
                            {
                                "version": v,
                                "base": base_path,
                                "path": version_path,
                                "bin": use_bin,
                            }
                        )
            else:
                print("  No version folders found")
        except OSError as e:
            print(f"  Error scanning: {e}")
    else:
        print(f"✗ Not found: {base_path}")

# 3. Check llama-cpp-python installation
print("\n3. Checking llama-cpp-python:")
print("-" * 70)

try:
    import llama_cpp

    print(f"✓ llama-cpp-python installed: {llama_cpp.__version__}")

    llama_cpp_path = Path(llama_cpp.__file__).parent
    print(f"  Location: {llama_cpp_path}")

    dll_files = list(llama_cpp_path.glob("*.dll"))
    cuda_dlls = [f.name for f in dll_files if "cuda" in f.name.lower()]
    if cuda_dlls:
        print(f"  CUDA DLLs found: {', '.join(cuda_dlls)}")
    else:
        print("  No CUDA DLLs in package (may be in PATH or CPU-only build)")
except ImportError:
    print("✗ llama-cpp-python not installed")

# 4. Summary and recommendations
print("\n4. Summary:")
print("=" * 70)

if found_versions:
    print(f"\n✓ Found {len(found_versions)} CUDA installation(s):")
    for v in found_versions:
        print(f"  • {v['version']} at {v['path']}")

    best = found_versions[0]
    print("\nRecommended CUDA_PATH:")
    print(f"  {best['path']}")
    print("\nRecommended PATH addition (prepend):")
    print(f"  {best['bin']}")
else:
    print("\n✗ No valid CUDA installations found (with bin/).")
    print("\nPossible issues:")
    print("  • CUDA Toolkit not installed")
    print("  • Installed in non-standard location")
    print("  • Installation incomplete (missing bin or bin/x64)")

print("\n" + "=" * 70)
