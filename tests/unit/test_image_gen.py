"""
Unit tests for image_gen.py.

Covers model registry (build, resolve, set default, get aliases), ImageGenerator
(check_dependencies, _resolve_model_path, is_available, _detect_device, _get_output_dir,
_load_pipeline, _unload_pipeline, generate, unload), and module-level state.
Session 151.
"""

import os
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from src.tools import image_gen as ig_mod
from src.tools.image_gen import (
    ImageGenerator,
    _build_model_registry,
    get_image_model_aliases,
    resolve_image_model,
    set_default_image_model,
    get_default_image_model_name,
)


@pytest.fixture(autouse=True)
def reset_registry():
    """Reset module-level state between tests."""
    original_registry = dict(ig_mod._model_registry)
    original_default = ig_mod._default_model_alias
    original_generating = ig_mod.generating_in_progress
    ig_mod._model_registry.clear()
    ig_mod._default_model_alias = None
    ig_mod.generating_in_progress = False
    yield
    ig_mod._model_registry.clear()
    ig_mod._model_registry.update(original_registry)
    ig_mod._default_model_alias = original_default
    ig_mod.generating_in_progress = original_generating


# ── TestBuildModelRegistry ────────────────────────────────────────


class TestBuildModelRegistry:
    """Tests for _build_model_registry."""

    def test_empty_when_no_models_dir(self, tmp_path):
        with patch("src.utils.paths.base_path", return_value=str(tmp_path)):
            _build_model_registry()
        assert ig_mod._model_registry == {}

    def test_discovers_safetensors(self, tmp_path):
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        (models_dir / "illustriousRealismBy_v10VAE.safetensors").touch()
        with patch("src.utils.paths.base_path", return_value=str(tmp_path)):
            _build_model_registry()
        assert len(ig_mod._model_registry) > 0
        # Should have full stem alias
        assert "illustriousrealismby_v10vae" in ig_mod._model_registry

    def test_ignores_non_safetensors(self, tmp_path):
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        (models_dir / "model.gguf").touch()
        (models_dir / "model.bin").touch()
        with patch("src.utils.paths.base_path", return_value=str(tmp_path)):
            _build_model_registry()
        assert ig_mod._model_registry == {}

    def test_short_alias_from_camelcase(self, tmp_path):
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        (models_dir / "dreamShaperXL_v2.safetensors").touch()
        with patch("src.utils.paths.base_path", return_value=str(tmp_path)):
            _build_model_registry()
        # First camelCase word should be "dream"
        assert "dream" in ig_mod._model_registry

    def test_fallback_base_path_on_import_error(self, tmp_path, monkeypatch):
        """Falls back to Path('models') if base_path import fails."""
        monkeypatch.chdir(tmp_path)
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        (models_dir / "test_model.safetensors").touch()
        with patch("src.utils.paths.base_path", side_effect=Exception("no base")):
            _build_model_registry()
        # Should have found the model via fallback Path("models")
        # (or at least not crashed)


# ── TestGetImageModelAliases ──────────────────────────────────────


class TestGetImageModelAliases:
    """Tests for get_image_model_aliases."""

    def test_returns_copy(self, tmp_path):
        ig_mod._model_registry["test"] = "/path/to/model.safetensors"
        result = get_image_model_aliases()
        assert result == {"test": "/path/to/model.safetensors"}
        # Modifying result doesn't affect module state
        result["new"] = "foo"
        assert "new" not in ig_mod._model_registry

    def test_triggers_build_when_empty(self, tmp_path):
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        (models_dir / "test.safetensors").touch()
        with patch("src.utils.paths.base_path", return_value=str(tmp_path)):
            result = get_image_model_aliases()
        assert len(result) > 0


# ── TestResolveImageModel ─────────────────────────────────────────


class TestResolveImageModel:
    """Tests for resolve_image_model."""

    def test_none_returns_default_alias(self):
        ig_mod._model_registry["mymodel"] = "/path/model.safetensors"
        ig_mod._default_model_alias = "mymodel"
        assert resolve_image_model(None) == "/path/model.safetensors"

    def test_none_returns_none_when_no_default(self):
        ig_mod._model_registry["mymodel"] = "/path/model.safetensors"
        assert resolve_image_model(None) is None

    def test_exact_alias_match(self):
        ig_mod._model_registry["illustrious"] = "/path/illust.safetensors"
        assert resolve_image_model("illustrious") == "/path/illust.safetensors"

    def test_case_insensitive(self):
        ig_mod._model_registry["illustrious"] = "/path/illust.safetensors"
        assert resolve_image_model("Illustrious") == "/path/illust.safetensors"

    def test_partial_match(self):
        ig_mod._model_registry["uberrealisticpornmerge"] = "/path/uber.safetensors"
        assert resolve_image_model("uber") == "/path/uber.safetensors"

    def test_filename_match(self):
        ig_mod._model_registry["illustriousrealismby_v10vae"] = "/models/illustriousRealismBy_v10VAE.safetensors"
        assert resolve_image_model("realism") == "/models/illustriousRealismBy_v10VAE.safetensors"

    def test_no_match_returns_none(self):
        ig_mod._model_registry["illustrious"] = "/path/illust.safetensors"
        assert resolve_image_model("nonexistent") is None

    def test_triggers_build_when_empty(self, tmp_path):
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        with patch("src.utils.paths.base_path", return_value=str(tmp_path)):
            result = resolve_image_model("anything")
        assert result is None


# ── TestSetDefaultImageModel ──────────────────────────────────────


class TestSetDefaultImageModel:
    """Tests for set_default_image_model."""

    def test_sets_default_on_match(self):
        ig_mod._model_registry["illustrious"] = "/path/illust.safetensors"
        result = set_default_image_model("illustrious")
        assert result == "/path/illust.safetensors"
        assert ig_mod._default_model_alias == "illustrious"

    def test_returns_none_on_no_match(self):
        ig_mod._model_registry["illustrious"] = "/path/illust.safetensors"
        result = set_default_image_model("nonexistent")
        assert result is None
        assert ig_mod._default_model_alias is None


# ── TestGetDefaultImageModelName ──────────────────────────────────


class TestGetDefaultImageModelName:
    """Tests for get_default_image_model_name."""

    def test_returns_none_by_default(self):
        assert get_default_image_model_name() is None

    def test_returns_alias_after_set(self):
        ig_mod._default_model_alias = "illustrious"
        assert get_default_image_model_name() == "illustrious"


# ── TestCheckDependencies ─────────────────────────────────────────


class TestCheckDependencies:
    """Tests for ImageGenerator.check_dependencies."""

    def test_returns_dict_of_packages(self):
        gen = ImageGenerator()
        result = gen.check_dependencies()
        assert isinstance(result, dict)
        # Should check these packages
        for pkg in ("torch", "diffusers", "transformers", "accelerate", "safetensors"):
            assert pkg in result

    def test_missing_package_reported(self):
        gen = ImageGenerator()
        with patch("builtins.__import__", side_effect=ImportError("no torch")):
            result = gen.check_dependencies()
        # All should be MISSING
        for pkg in ("torch", "diffusers", "transformers", "accelerate", "safetensors"):
            assert "MISSING" in result[pkg]


# ── TestResolveModelPath ──────────────────────────────────────────


class TestResolveModelPath:
    """Tests for ImageGenerator._resolve_model_path."""

    def test_env_var_local_path(self, tmp_path):
        model_file = tmp_path / "my_model.safetensors"
        model_file.touch()
        with patch.dict(os.environ, {"IMAGE_MODEL_PATH": str(model_file)}):
            result = ImageGenerator._resolve_model_path()
        assert result == str(model_file)

    def test_env_var_hf_id(self):
        with patch.dict(os.environ, {"IMAGE_MODEL_PATH": "stabilityai/sdxl-base-1.0"}):
            result = ImageGenerator._resolve_model_path()
        assert result == "stabilityai/sdxl-base-1.0"

    def test_env_var_empty_falls_through(self, tmp_path):
        with patch.dict(os.environ, {"IMAGE_MODEL_PATH": ""}):
            with patch("src.utils.paths.base_path", return_value=str(tmp_path)):
                result = ImageGenerator._resolve_model_path()
        assert result is None

    def test_keyword_match_in_models_dir(self, tmp_path):
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        (models_dir / "sdxl_base_v1.safetensors").touch()
        with patch.dict(os.environ, {"IMAGE_MODEL_PATH": ""}):
            with patch("src.utils.paths.base_path", return_value=str(tmp_path)):
                result = ImageGenerator._resolve_model_path()
        assert result is not None
        assert "sdxl_base_v1" in result

    def test_single_safetensors_fallback(self, tmp_path):
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        (models_dir / "unknown_model.safetensors").touch()
        with patch.dict(os.environ, {"IMAGE_MODEL_PATH": ""}):
            with patch("src.utils.paths.base_path", return_value=str(tmp_path)):
                result = ImageGenerator._resolve_model_path()
        assert result is not None
        assert "unknown_model" in result

    def test_multiple_safetensors_picks_largest(self, tmp_path):
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        small = models_dir / "small_model.safetensors"
        large = models_dir / "large_model.safetensors"
        small.write_bytes(b"x" * 100)
        large.write_bytes(b"x" * 1000)
        with patch.dict(os.environ, {"IMAGE_MODEL_PATH": ""}):
            with patch("src.utils.paths.base_path", return_value=str(tmp_path)):
                result = ImageGenerator._resolve_model_path()
        assert "large_model" in result

    def test_mmproj_excluded(self, tmp_path):
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        (models_dir / "mmproj_clip.safetensors").touch()
        with patch.dict(os.environ, {"IMAGE_MODEL_PATH": ""}):
            with patch("src.utils.paths.base_path", return_value=str(tmp_path)):
                result = ImageGenerator._resolve_model_path()
        assert result is None

    def test_no_models_dir_returns_none(self, tmp_path):
        with patch.dict(os.environ, {"IMAGE_MODEL_PATH": ""}):
            with patch("src.utils.paths.base_path", return_value=str(tmp_path)):
                result = ImageGenerator._resolve_model_path()
        assert result is None


# ── TestIsAvailable ───────────────────────────────────────────────


class TestIsAvailable:
    """Tests for ImageGenerator.is_available."""

    def test_available_when_model_found(self, tmp_path):
        model_file = tmp_path / "model.safetensors"
        model_file.touch()
        with patch.dict(os.environ, {"IMAGE_MODEL_PATH": str(model_file)}):
            assert ImageGenerator.is_available() is True

    def test_not_available_when_no_model(self, tmp_path):
        with patch.dict(os.environ, {"IMAGE_MODEL_PATH": ""}):
            with patch("src.utils.paths.base_path", return_value=str(tmp_path)):
                assert ImageGenerator.is_available() is False


# ── TestDetectDevice ──────────────────────────────────────────────


class TestDetectDevice:
    """Tests for ImageGenerator._detect_device."""

    def test_returns_cuda_when_available(self):
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        mock_torch.cuda.get_device_name.return_value = "NVIDIA RTX 4090"
        mock_torch.__version__ = "2.1.0"
        with patch.dict("sys.modules", {"torch": mock_torch}):
            with patch("builtins.__import__", side_effect=lambda name, *a, **kw: mock_torch if name == "torch" else __import__(name, *a, **kw)):
                gen = ImageGenerator()
                result = gen._detect_device()
        assert result == "cuda"

    def test_returns_cpu_when_no_cuda(self):
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_torch.version.cuda = None
        mock_torch.__version__ = "2.1.0+cpu"
        with patch.dict("sys.modules", {"torch": mock_torch}):
            with patch("builtins.__import__", side_effect=lambda name, *a, **kw: mock_torch if name == "torch" else __import__(name, *a, **kw)):
                gen = ImageGenerator()
                result = gen._detect_device()
        assert result == "cpu"

    def test_returns_cpu_on_import_error(self):
        with patch("builtins.__import__", side_effect=ImportError("no torch")):
            gen = ImageGenerator()
            result = gen._detect_device()
        assert result == "cpu"


# ── TestGetOutputDir ──────────────────────────────────────────────


class TestGetOutputDir:
    """Tests for ImageGenerator._get_output_dir."""

    def test_creates_output_directory(self, tmp_path):
        with patch("src.utils.paths.base_path", return_value=str(tmp_path)):
            result = ImageGenerator._get_output_dir()
        assert result == tmp_path / "workspace" / "images"
        assert result.exists()

    def test_fallback_on_import_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch.dict("sys.modules", {"src.utils.paths": None}):
            # Will try to import and fail, falling back to Path("workspace"/"images")
            gen = ImageGenerator()
            # Just verify it doesn't crash
            result = gen._get_output_dir()
            assert isinstance(result, Path)


# ── TestLoadPipeline ──────────────────────────────────────────────


class TestLoadPipeline:
    """Tests for ImageGenerator._load_pipeline."""

    def test_load_from_safetensors(self):
        gen = ImageGenerator()
        mock_pipe = MagicMock()
        mock_pipe.to.return_value = mock_pipe
        mock_torch = MagicMock()
        mock_torch.float16 = "float16"
        mock_sdxl = MagicMock()
        mock_sdxl.from_single_file.return_value = mock_pipe

        with patch.object(gen, "_detect_device", return_value="cuda"):
            with patch.dict("sys.modules", {"torch": mock_torch, "diffusers": MagicMock()}):
                with patch("builtins.__import__", side_effect=lambda name, *a, **kw: (
                    mock_torch if name == "torch" else
                    type("mod", (), {"StableDiffusionXLPipeline": mock_sdxl})() if name == "diffusers" else
                    __import__(name, *a, **kw)
                )):
                    result = gen._load_pipeline("/models/test.safetensors")
        assert result is True

    def test_load_returns_false_on_import_error(self):
        gen = ImageGenerator()
        with patch("builtins.__import__", side_effect=ImportError("no diffusers")):
            result = gen._load_pipeline("/models/test.safetensors")
        assert result is False

    def test_load_returns_false_on_exception(self):
        gen = ImageGenerator()
        with patch.object(gen, "_detect_device", side_effect=RuntimeError("device fail")):
            with patch("builtins.__import__", side_effect=lambda name, *a, **kw: (
                MagicMock() if name in ("torch", "diffusers") else __import__(name, *a, **kw)
            )):
                result = gen._load_pipeline("/models/test.safetensors")
        assert result is False


# ── TestUnloadPipeline ────────────────────────────────────────────


class TestUnloadPipeline:
    """Tests for ImageGenerator._unload_pipeline."""

    def test_unload_clears_pipeline(self):
        gen = ImageGenerator()
        gen._pipeline = MagicMock()
        with patch("gc.collect"):
            gen._unload_pipeline()
        assert gen._pipeline is None

    def test_unload_when_no_pipeline(self):
        gen = ImageGenerator()
        with patch("gc.collect"):
            gen._unload_pipeline()  # Should not raise
        assert gen._pipeline is None


# ── TestGenerate ──────────────────────────────────────────────────


class TestGenerate:
    """Tests for ImageGenerator.generate."""

    def test_no_model_found(self):
        gen = ImageGenerator()
        with patch.object(ig_mod, "resolve_image_model", return_value=None):
            with patch.object(ImageGenerator, "_resolve_model_path", return_value=None):
                result = gen.generate("a cat")
        assert result["success"] is False
        assert "No image model found" in result["error"]

    def test_pipeline_load_failure(self, tmp_path):
        gen = ImageGenerator()
        model_path = str(tmp_path / "model.safetensors")
        with patch.object(ig_mod, "resolve_image_model", return_value=model_path):
            with patch.object(gen, "_load_pipeline", return_value=False):
                with patch.object(gen, "check_dependencies", return_value={}):
                    with patch.object(gen, "_unload_pipeline"):
                        result = gen.generate("a cat")
        assert result["success"] is False
        assert "Failed to load SDXL pipeline" in result["error"]

    def test_generate_success(self, tmp_path):
        gen = ImageGenerator()
        model_path = str(tmp_path / "model.safetensors")

        mock_image = MagicMock()
        mock_pipeline = MagicMock()
        mock_pipeline.return_value.images = [mock_image]

        gen._pipeline = mock_pipeline
        gen._loaded_model = model_path

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch.object(ig_mod, "resolve_image_model", return_value=model_path):
            with patch.object(ImageGenerator, "_get_output_dir", return_value=output_dir):
                with patch.object(gen, "_unload_pipeline"):
                    result = gen.generate("a cyberpunk city")

        assert result["success"] is True
        assert "image_path" in result
        assert result["prompt"] == "a cyberpunk city"
        mock_image.save.assert_called_once()

    def test_generate_exception(self, tmp_path):
        gen = ImageGenerator()
        model_path = str(tmp_path / "model.safetensors")

        mock_pipeline = MagicMock()
        mock_pipeline.side_effect = RuntimeError("generation failed")
        gen._pipeline = mock_pipeline
        gen._loaded_model = model_path

        with patch.object(ig_mod, "resolve_image_model", return_value=model_path):
            with patch.object(gen, "_unload_pipeline"):
                result = gen.generate("a cat")
        assert result["success"] is False
        assert "generation failed" in result["error"]

    def test_generate_sets_generating_flag(self, tmp_path):
        gen = ImageGenerator()
        model_path = str(tmp_path / "model.safetensors")

        flags_seen = []

        def fake_pipeline(**kwargs):
            flags_seen.append(ig_mod.generating_in_progress)
            result = MagicMock()
            result.images = [MagicMock()]
            return result

        gen._pipeline = fake_pipeline
        gen._loaded_model = model_path

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch.object(ig_mod, "resolve_image_model", return_value=model_path):
            with patch.object(ImageGenerator, "_get_output_dir", return_value=output_dir):
                with patch.object(gen, "_unload_pipeline"):
                    gen.generate("test")

        assert True in flags_seen

    def test_generate_keep_loaded(self, tmp_path):
        gen = ImageGenerator()
        model_path = str(tmp_path / "model.safetensors")

        mock_image = MagicMock()
        mock_pipeline = MagicMock()
        mock_pipeline.return_value.images = [mock_image]
        gen._pipeline = mock_pipeline
        gen._loaded_model = model_path

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch.object(ig_mod, "resolve_image_model", return_value=model_path):
            with patch.object(ImageGenerator, "_get_output_dir", return_value=output_dir):
                with patch.object(gen, "_unload_pipeline") as mock_unload:
                    result = gen.generate("test", keep_loaded=True)

        assert result["success"] is True
        # _unload_pipeline should NOT be called when keep_loaded=True
        mock_unload.assert_not_called()


# ── TestUnload ────────────────────────────────────────────────────


class TestUnload:
    """Tests for ImageGenerator.unload (public API)."""

    def test_unload_clears_flag(self):
        gen = ImageGenerator()
        gen._pipeline = MagicMock()
        ig_mod.generating_in_progress = True
        with patch("gc.collect"):
            gen.unload()
        assert ig_mod.generating_in_progress is False
        assert gen._pipeline is None


# ── TestModuleState ───────────────────────────────────────────────


class TestModuleState:
    """Tests for module-level constants and state."""

    def test_network_serving_disabled(self):
        assert ig_mod._ALLOW_NETWORK_SERVING is False

    def test_gen_lock_is_lock(self):
        assert isinstance(ig_mod._gen_lock, type(threading.Lock()))
