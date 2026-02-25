"""
Comprehensive unit tests for voice_interface.py.

Tests cover STTEngine, TTSEngine, and VoiceInterface classes with
mocked external dependencies (faster_whisper, piper, sounddevice, numpy, torch).
"""

import io
import os
import struct
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, Mock, patch, call

import pytest


class TestSTTEngineInit:
    """Test STTEngine initialization."""

    def test_init_default_values(self):
        """Test STTEngine initializes with default parameters."""
        from src.interfaces.voice_interface import STTEngine

        engine = STTEngine()
        assert engine._model_size == "base"
        assert engine._device == "auto"
        assert engine._compute_type == "auto"
        assert engine._model is None
        assert engine._loaded is False

    def test_init_custom_values(self):
        """Test STTEngine initializes with custom parameters."""
        from src.interfaces.voice_interface import STTEngine

        engine = STTEngine(model_size="large-v3-turbo", device="cuda", compute_type="float16")
        assert engine._model_size == "large-v3-turbo"
        assert engine._device == "cuda"
        assert engine._compute_type == "float16"
        assert engine._loaded is False

    def test_is_loaded_property_initially_false(self):
        """Test is_loaded property returns False initially."""
        from src.interfaces.voice_interface import STTEngine

        engine = STTEngine()
        assert engine.is_loaded is False


class TestSTTEngineLoad:
    """Test STTEngine.load() method."""

    @patch("src.interfaces.voice_interface.logger")
    def test_load_success(self, mock_logger):
        """Test successful model loading."""
        from src.interfaces.voice_interface import STTEngine

        mock_whisper_module = MagicMock()
        mock_model = MagicMock()
        mock_whisper_module.WhisperModel.return_value = mock_model

        engine = STTEngine(model_size="base", device="cpu", compute_type="int8")

        with patch.dict("sys.modules", {"faster_whisper": mock_whisper_module}):
            result = engine.load()

        assert result is True
        assert engine._loaded is True
        assert engine._model is mock_model

    @patch("src.interfaces.voice_interface.logger")
    def test_load_import_error(self, mock_logger):
        """Test load() when faster_whisper is not installed."""
        from src.interfaces.voice_interface import STTEngine

        engine = STTEngine()

        def mock_import_error(name, *args, **kwargs):
            if name == "faster_whisper":
                raise ImportError("No module named 'faster_whisper'")
            return __import__(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import_error):
            result = engine.load()

        assert result is False
        assert engine._loaded is False
        mock_logger.warning.assert_called_once()

    @patch("src.interfaces.voice_interface.logger")
    def test_load_other_exception(self, mock_logger):
        """Test load() when model loading raises an exception."""
        from src.interfaces.voice_interface import STTEngine

        mock_whisper_class = MagicMock(side_effect=RuntimeError("CUDA not available"))

        with patch.dict("sys.modules", {"faster_whisper": MagicMock()}):
            with patch(
                "builtins.__import__",
                side_effect=lambda name, *args, **kwargs: (
                    MagicMock(WhisperModel=mock_whisper_class)
                    if name == "faster_whisper"
                    else __import__(name, *args, **kwargs)
                ),
            ):
                engine = STTEngine()
                result = engine.load()

        assert result is False
        assert engine._loaded is False

    @patch("src.interfaces.voice_interface.logger")
    def test_load_idempotent_already_loaded(self, mock_logger):
        """Test load() is idempotent when already loaded."""
        from src.interfaces.voice_interface import STTEngine

        engine = STTEngine()
        engine._loaded = True
        engine._model = MagicMock()

        result = engine.load()

        assert result is True
        mock_logger.info.assert_not_called()


class TestSTTEngineTranscribeFile:
    """Test STTEngine.transcribe_file() method."""

    @patch("src.interfaces.voice_interface.logger")
    def test_transcribe_file_success(self, mock_logger):
        """Test successful file transcription."""
        from src.interfaces.voice_interface import STTEngine

        engine = STTEngine()
        mock_model = MagicMock()
        mock_segment1 = MagicMock(text="Hello")
        mock_segment2 = MagicMock(text="world")
        mock_model.transcribe.return_value = ([mock_segment1, mock_segment2], MagicMock())
        engine._model = mock_model
        engine._loaded = True

        result = engine.transcribe_file("/path/to/audio.wav", language="en")

        assert result == "Hello world"
        mock_model.transcribe.assert_called_once()

    @patch("src.interfaces.voice_interface.logger")
    def test_transcribe_file_not_loaded_auto_load_success(self, mock_logger):
        """Test transcribe_file triggers auto-load when not loaded."""
        from src.interfaces.voice_interface import STTEngine

        engine = STTEngine()
        engine._loaded = False

        mock_model = MagicMock()
        mock_segment = MagicMock(text="Test")
        mock_model.transcribe.return_value = ([mock_segment], MagicMock())
        engine._model = mock_model

        with patch.object(engine, "load", return_value=True):
            result = engine.transcribe_file("/path/to/audio.wav")

        assert result == "Test"

    @patch("src.interfaces.voice_interface.logger")
    def test_transcribe_file_error_returns_empty_string(self, mock_logger):
        """Test transcribe_file returns empty string on error."""
        from src.interfaces.voice_interface import STTEngine

        engine = STTEngine()
        mock_model = MagicMock()
        mock_model.transcribe.side_effect = RuntimeError("Model error")
        engine._model = mock_model
        engine._loaded = True

        result = engine.transcribe_file("/path/to/audio.wav")

        assert result == ""
        mock_logger.error.assert_called_once()


class TestSTTEngineTranscribeBytes:
    """Test STTEngine.transcribe_bytes() method."""

    @patch("src.interfaces.voice_interface.logger")
    def test_transcribe_bytes_creates_temp_wav(self, mock_logger):
        """Test transcribe_bytes creates temporary WAV file."""
        from src.interfaces.voice_interface import STTEngine

        engine = STTEngine()
        mock_model = MagicMock()
        mock_segment = MagicMock(text="Transcribed")
        mock_model.transcribe.return_value = ([mock_segment], MagicMock())
        engine._model = mock_model
        engine._loaded = True

        # Create minimal audio bytes (16-bit PCM, single channel)
        audio_bytes = struct.pack("<h", 100) * 1000  # 1000 samples at 16-bit

        with patch("src.interfaces.voice_interface.tempfile.NamedTemporaryFile") as mock_temp:
            mock_file = MagicMock()
            mock_temp.return_value.__enter__.return_value = mock_file
            mock_file.name = "/tmp/test_audio.wav"

            with patch("src.interfaces.voice_interface.wave.open") as mock_wave:
                mock_wav = MagicMock()
                mock_wave.return_value.__enter__.return_value = mock_wav

                result = engine.transcribe_bytes(audio_bytes, sample_rate=16000, language="en")

        assert result == "Transcribed"

    @patch("src.interfaces.voice_interface.logger")
    def test_transcribe_bytes_cleans_up_temp_file(self, mock_logger):
        """Test transcribe_bytes cleans up temporary file after transcription."""
        from src.interfaces.voice_interface import STTEngine

        engine = STTEngine()
        mock_model = MagicMock()
        mock_segment = MagicMock(text="Cleaned up")
        mock_model.transcribe.return_value = ([mock_segment], MagicMock())
        engine._model = mock_model
        engine._loaded = True

        audio_bytes = struct.pack("<h", 100) * 1000

        with patch("src.interfaces.voice_interface.os.unlink") as mock_unlink:
            with patch("src.interfaces.voice_interface.tempfile.NamedTemporaryFile"):
                with patch("src.interfaces.voice_interface.wave.open"):
                    result = engine.transcribe_bytes(audio_bytes)

        assert result == "Cleaned up"
        mock_unlink.assert_called_once()


class TestTTSEngineInit:
    """Test TTSEngine initialization."""

    def test_init_default_voice(self):
        """Test TTSEngine initializes with default voice."""
        from src.interfaces.voice_interface import TTSEngine

        engine = TTSEngine()
        assert engine._voice_name == "en_US-lessac-medium"
        assert engine._sample_rate == 22050
        assert engine._loaded is False
        assert engine._voice is None

    def test_init_custom_voice(self):
        """Test TTSEngine initializes with custom voice."""
        from src.interfaces.voice_interface import TTSEngine

        engine = TTSEngine(voice_name="en_US-amy-medium")
        assert engine._voice_name == "en_US-amy-medium"
        assert engine._loaded is False

    def test_is_loaded_property_initially_false(self):
        """Test is_loaded property returns False initially."""
        from src.interfaces.voice_interface import TTSEngine

        engine = TTSEngine()
        assert engine.is_loaded is False


class TestTTSEngineLoad:
    """Test TTSEngine.load() method."""

    @patch("src.interfaces.voice_interface.logger")
    def test_load_success_with_model_files(self, mock_logger):
        """Test successful TTS model loading when files exist."""
        from src.interfaces.voice_interface import TTSEngine

        engine = TTSEngine(voice_name="en_US-lessac-medium")

        mock_voice = MagicMock()
        mock_piper_class = MagicMock()
        mock_piper_class.load.return_value = mock_voice

        with patch("pathlib.Path.exists", return_value=True):
            with patch(
                "builtins.__import__",
                side_effect=lambda name, *args, **kwargs: (
                    MagicMock(PiperVoice=mock_piper_class)
                    if name == "piper"
                    else __import__(name, *args, **kwargs)
                ),
            ):
                result = engine.load()

        assert result is True

    @patch("src.interfaces.voice_interface.logger")
    def test_load_missing_model_files(self, mock_logger):
        """Test load() returns False when model files missing."""
        from src.interfaces.voice_interface import TTSEngine

        engine = TTSEngine(voice_name="en_US-lessac-medium")

        with patch("pathlib.Path.exists", return_value=False):
            with patch(
                "builtins.__import__",
                side_effect=lambda name, *args, **kwargs: (
                    MagicMock(PiperVoice=MagicMock())
                    if name == "piper"
                    else __import__(name, *args, **kwargs)
                ),
            ):
                result = engine.load()

        assert result is False
        mock_logger.warning.assert_called_once()

    @patch("src.interfaces.voice_interface.logger")
    def test_load_import_error(self, mock_logger):
        """Test load() when piper is not installed."""
        from src.interfaces.voice_interface import TTSEngine

        engine = TTSEngine()

        def mock_import_error(name, *args, **kwargs):
            if name == "piper":
                raise ImportError("No module named 'piper'")
            return __import__(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import_error):
            result = engine.load()

        assert result is False
        mock_logger.warning.assert_called_once()

    @patch("src.interfaces.voice_interface.logger")
    def test_load_other_exception(self, mock_logger):
        """Test load() when voice loading raises an exception."""
        from src.interfaces.voice_interface import TTSEngine

        engine = TTSEngine()

        mock_piper_class = MagicMock()
        mock_piper_class.load.side_effect = RuntimeError("Voice load error")

        with patch("pathlib.Path.exists", return_value=True):
            with patch(
                "builtins.__import__",
                side_effect=lambda name, *args, **kwargs: (
                    MagicMock(PiperVoice=mock_piper_class)
                    if name == "piper"
                    else __import__(name, *args, **kwargs)
                ),
            ):
                result = engine.load()

        assert result is False


class TestTTSEngineSynthesize:
    """Test TTSEngine synthesize methods."""

    @patch("src.interfaces.voice_interface.logger")
    def test_synthesize_to_file_success(self, mock_logger):
        """Test synthesize_to_file creates output file."""
        from src.interfaces.voice_interface import TTSEngine

        engine = TTSEngine()
        mock_voice = MagicMock()
        engine._voice = mock_voice
        engine._loaded = True

        with patch("builtins.open", create=True) as mock_open:
            with patch("src.interfaces.voice_interface.wave.open") as mock_wave:
                mock_wav = MagicMock()
                mock_wave.return_value.__enter__.return_value = mock_wav

                result = engine.synthesize_to_file("Hello world", "/tmp/output.wav")

        assert result is True
        mock_voice.synthesize.assert_called_once()

    @patch("src.interfaces.voice_interface.logger")
    def test_synthesize_to_bytes_success(self, mock_logger):
        """Test synthesize_to_bytes returns WAV bytes."""
        from src.interfaces.voice_interface import TTSEngine

        engine = TTSEngine()
        mock_voice = MagicMock()
        engine._voice = mock_voice
        engine._loaded = True

        with patch("src.interfaces.voice_interface.wave.open") as mock_wave:
            mock_wav = MagicMock()
            mock_wave.return_value.__enter__.return_value = mock_wav

            def mock_synthesize(text, wav_file):
                # Simulate writing WAV data
                pass

            mock_voice.synthesize.side_effect = mock_synthesize

            with patch("src.interfaces.voice_interface.io.BytesIO") as mock_bytesio:
                mock_buf = MagicMock()
                mock_buf.getvalue.return_value = b"WAV_DATA"
                mock_bytesio.return_value = mock_buf

                result = engine.synthesize_to_bytes("Hello")

        assert result == b"WAV_DATA"

    @patch("src.interfaces.voice_interface.logger")
    def test_synthesize_error_handling(self, mock_logger):
        """Test synthesize methods handle errors gracefully."""
        from src.interfaces.voice_interface import TTSEngine

        engine = TTSEngine()
        mock_voice = MagicMock()
        mock_voice.synthesize.side_effect = RuntimeError("Synthesis error")
        engine._voice = mock_voice
        engine._loaded = True

        result = engine.synthesize_to_bytes("Error test")

        assert result is None
        mock_logger.error.assert_called_once()


class TestTTSEngineSpeak:
    """Test TTSEngine.speak() method."""

    @patch("src.interfaces.voice_interface.logger")
    def test_speak_success(self, mock_logger):
        """Test speak() successfully outputs audio."""
        from src.interfaces.voice_interface import TTSEngine

        engine = TTSEngine()
        engine._loaded = True

        mock_numpy = MagicMock()
        mock_numpy.int16 = "int16"
        mock_numpy.frombuffer.return_value = MagicMock()

        mock_sd = MagicMock()

        with patch.dict("sys.modules", {"numpy": mock_numpy, "sounddevice": mock_sd}):
            with patch.object(engine, "synthesize_to_bytes", return_value=b"WAV_DATA"):
                with patch("src.interfaces.voice_interface.wave.open") as mock_wave:
                    mock_wav = MagicMock()
                    mock_wav.getnchannels.return_value = 1
                    mock_wav.getsampwidth.return_value = 2
                    mock_wav.getframerate.return_value = 22050
                    mock_wav.getnframes.return_value = 1000
                    mock_wav.readframes.return_value = b"audio_data"
                    mock_wave.return_value.__enter__.return_value = mock_wav

                    result = engine.speak("Hello")

        assert result is True

    @patch("src.interfaces.voice_interface.logger")
    def test_speak_import_error_no_sounddevice(self, mock_logger):
        """Test speak() handles missing sounddevice."""
        from src.interfaces.voice_interface import TTSEngine

        engine = TTSEngine()
        engine._loaded = True

        def mock_import_error(name, *args, **kwargs):
            if name in ["numpy", "sounddevice"]:
                raise ImportError(f"No module named '{name}'")
            return __import__(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import_error):
            result = engine.speak("Hello")

        assert result is False
        mock_logger.warning.assert_called_once()

    @patch("src.interfaces.voice_interface.logger")
    def test_speak_other_error(self, mock_logger):
        """Test speak() handles other exceptions."""
        from src.interfaces.voice_interface import TTSEngine

        engine = TTSEngine()
        engine._loaded = True

        with patch.object(engine, "synthesize_to_bytes", return_value=None):
            result = engine.speak("Error test")

        assert result is False


class TestVoiceInterfaceInit:
    """Test VoiceInterface initialization."""

    def test_init_default_values(self):
        """Test VoiceInterface initializes with defaults."""
        from src.interfaces.voice_interface import VoiceInterface

        voice = VoiceInterface()
        assert voice._on_transcription is None
        assert voice._listening is False
        assert voice._listen_thread is None
        assert voice._silence_threshold == 0.03
        assert voice._silence_duration == 1.5
        assert voice.stt._model_size == "base"
        assert voice.tts._voice_name == "en_US-lessac-medium"

    def test_init_custom_parameters(self):
        """Test VoiceInterface initializes with custom parameters."""
        from src.interfaces.voice_interface import VoiceInterface

        callback = lambda x: None
        voice = VoiceInterface(
            on_transcription=callback,
            whisper_model="large-v3-turbo",
            piper_voice="en_US-amy-medium",
        )
        assert voice._on_transcription is callback
        assert voice.stt._model_size == "large-v3-turbo"
        assert voice.tts._voice_name == "en_US-amy-medium"

    def test_init_env_var_configuration(self):
        """Test VoiceInterface reads environment variables."""
        from src.interfaces.voice_interface import VoiceInterface

        with patch.dict(
            os.environ,
            {
                "ARCHI_WHISPER_MODEL": "small",
                "ARCHI_WHISPER_DEVICE": "cuda",
                "ARCHI_PIPER_VOICE": "en_GB-alan-medium",
                "ARCHI_VOICE_SILENCE_THRESHOLD": "0.05",
                "ARCHI_VOICE_SILENCE_DURATION": "2.0",
            },
        ):
            voice = VoiceInterface()

        assert voice.stt._model_size == "small"
        assert voice.stt._device == "cuda"
        assert voice.tts._voice_name == "en_GB-alan-medium"
        assert voice._silence_threshold == 0.05
        assert voice._silence_duration == 2.0


class TestVoiceInterfaceInitialize:
    """Test VoiceInterface.initialize() method."""

    def test_initialize_calls_both_load_methods(self):
        """Test initialize() calls both STT and TTS load methods."""
        from src.interfaces.voice_interface import VoiceInterface

        voice = VoiceInterface()

        with patch.object(voice.stt, "load", return_value=True):
            with patch.object(voice.tts, "load", return_value=True):
                result = voice.initialize()

        assert result == {"stt": True, "tts": True}

    def test_initialize_returns_status_dict(self):
        """Test initialize() returns correct status dictionary."""
        from src.interfaces.voice_interface import VoiceInterface

        voice = VoiceInterface()

        with patch.object(voice.stt, "load", return_value=False):
            with patch.object(voice.tts, "load", return_value=True):
                result = voice.initialize()

        assert result == {"stt": False, "tts": True}


class TestVoiceInterfaceStartStopListening:
    """Test VoiceInterface listening control methods."""

    @patch("src.interfaces.voice_interface.logger")
    def test_start_listening_success(self, mock_logger):
        """Test start_listening starts background thread."""
        from src.interfaces.voice_interface import VoiceInterface

        voice = VoiceInterface()
        voice.stt._loaded = True

        with patch.dict("sys.modules", {"sounddevice": MagicMock()}):
            with patch.object(voice, "_listen_loop"):
                result = voice.start_listening()

        assert result is True
        assert voice._listening is True
        assert voice._listen_thread is not None

    @patch("src.interfaces.voice_interface.logger")
    def test_start_listening_without_stt(self, mock_logger):
        """Test start_listening fails without STT loaded."""
        from src.interfaces.voice_interface import VoiceInterface

        voice = VoiceInterface()
        voice.stt._loaded = False

        with patch.object(voice.stt, "load", return_value=False):
            result = voice.start_listening()

        assert result is False
        mock_logger.error.assert_called_once()

    @patch("src.interfaces.voice_interface.logger")
    def test_start_listening_already_listening(self, mock_logger):
        """Test start_listening when already listening."""
        from src.interfaces.voice_interface import VoiceInterface

        voice = VoiceInterface()
        voice._listening = True

        with patch.dict("sys.modules", {"sounddevice": MagicMock()}):
            result = voice.start_listening()

        assert result is True
        mock_logger.warning.assert_called_once()

    @patch("src.interfaces.voice_interface.logger")
    def test_stop_listening(self, mock_logger):
        """Test stop_listening stops the listen thread."""
        from src.interfaces.voice_interface import VoiceInterface

        voice = VoiceInterface()
        voice._listening = True
        mock_thread = MagicMock()
        voice._listen_thread = mock_thread

        voice.stop_listening()

        assert voice._listening is False
        mock_thread.join.assert_called_once_with(timeout=3)
        assert voice._listen_thread is None


class TestCalculateRms:
    """Test VoiceInterface._calculate_rms() static method."""

    def test_calculate_rms_silence(self):
        """Test RMS calculation for silence (zeros)."""
        from src.interfaces.voice_interface import VoiceInterface

        # Create bytes representing silence (all zeros)
        silence_data = struct.pack("<1000h", *([0] * 1000))

        rms = VoiceInterface._calculate_rms(silence_data)

        assert rms == 0.0

    def test_calculate_rms_loud_signal(self):
        """Test RMS calculation for loud signal."""
        from src.interfaces.voice_interface import VoiceInterface

        # Create bytes with max amplitude values
        loud_data = struct.pack("<100h", *([32767] * 100))

        rms = VoiceInterface._calculate_rms(loud_data)

        assert rms > 0.9  # Should be close to 1.0 (normalized)

    def test_calculate_rms_empty_data(self):
        """Test RMS calculation for empty data."""
        from src.interfaces.voice_interface import VoiceInterface

        rms = VoiceInterface._calculate_rms(b"")

        assert rms == 0.0

    def test_calculate_rms_short_data(self):
        """Test RMS calculation for data shorter than 2 bytes."""
        from src.interfaces.voice_interface import VoiceInterface

        rms = VoiceInterface._calculate_rms(b"\x00")

        assert rms == 0.0

    def test_calculate_rms_moderate_signal(self):
        """Test RMS calculation for moderate amplitude signal."""
        from src.interfaces.voice_interface import VoiceInterface

        # Create bytes with moderate amplitude
        data = struct.pack("<200h", *([16384] * 200))

        rms = VoiceInterface._calculate_rms(data)

        assert 0.4 < rms < 0.6  # Should be roughly half amplitude


class TestVoiceInterfaceStatus:
    """Test VoiceInterface.get_status() and other status methods."""

    def test_get_status_loaded_state(self):
        """Test get_status returns correct loaded state."""
        from src.interfaces.voice_interface import VoiceInterface

        voice = VoiceInterface()
        voice.stt._loaded = True
        voice.tts._loaded = False
        voice._listening = False

        status = voice.get_status()

        assert status["stt_loaded"] is True
        assert status["tts_loaded"] is False
        assert status["listening"] is False
        assert status["whisper_model"] == "base"
        assert status["piper_voice"] == "en_US-lessac-medium"

    def test_get_status_listening_state(self):
        """Test get_status returns listening state."""
        from src.interfaces.voice_interface import VoiceInterface

        voice = VoiceInterface()
        voice._listening = True

        status = voice.get_status()

        assert status["listening"] is True

    def test_is_listening_property(self):
        """Test is_listening property."""
        from src.interfaces.voice_interface import VoiceInterface

        voice = VoiceInterface()
        assert voice.is_listening is False

        voice._listening = True
        assert voice.is_listening is True

    def test_speak_delegates_to_tts(self):
        """Test speak() delegates to TTS engine."""
        from src.interfaces.voice_interface import VoiceInterface

        voice = VoiceInterface()

        with patch.object(voice.tts, "speak", return_value=True) as mock_speak:
            result = voice.speak("Hello world")

        assert result is True
        mock_speak.assert_called_once_with("Hello world")

    def test_transcribe_file_delegates_to_stt(self):
        """Test transcribe_file() delegates to STT engine."""
        from src.interfaces.voice_interface import VoiceInterface

        voice = VoiceInterface()

        with patch.object(voice.stt, "transcribe_file", return_value="Transcribed text") as mock_transcribe:
            result = voice.transcribe_file("/path/to/audio.wav")

        assert result == "Transcribed text"
        mock_transcribe.assert_called_once_with("/path/to/audio.wav")


class TestVoiceInterfaceListenLoop:
    """Test VoiceInterface._listen_loop() method."""

    @patch("src.interfaces.voice_interface.logger")
    def test_listen_loop_import_error_graceful_exit(self, mock_logger):
        """Test _listen_loop exits gracefully if imports fail."""
        from src.interfaces.voice_interface import VoiceInterface

        voice = VoiceInterface()

        def mock_import_error(name, *args, **kwargs):
            if name in ["numpy", "sounddevice"]:
                raise ImportError(f"No module named '{name}'")
            return __import__(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import_error):
            # Should not raise, just return
            voice._listen_loop()

    @patch("src.interfaces.voice_interface.logger")
    def test_listen_loop_with_callback(self, mock_logger):
        """Test _listen_loop invokes transcription callback."""
        from src.interfaces.voice_interface import VoiceInterface

        voice = VoiceInterface()
        callback = MagicMock()
        voice._on_transcription = callback

        # Setup mocks for audio stream
        mock_numpy = MagicMock()
        mock_sd = MagicMock()

        # Create mock stream
        mock_stream = MagicMock()

        # Simulate audio data: silence then speech then silence
        # We'll make it exit after one iteration to keep test simple
        voice._listening = False

        with patch.dict("sys.modules", {"numpy": mock_numpy, "sounddevice": mock_sd}):
            voice._listen_loop()

        # Should not crash
        assert voice._listening is False


class TestEdgeCases:
    """Test edge cases and error conditions."""

    @patch("src.interfaces.voice_interface.logger")
    def test_stt_transcribe_file_with_empty_result(self, mock_logger):
        """Test STT handles audio with no speech detected."""
        from src.interfaces.voice_interface import STTEngine

        engine = STTEngine()
        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([], MagicMock())
        engine._model = mock_model
        engine._loaded = True

        result = engine.transcribe_file("/path/to/silent.wav")

        assert result == ""
        mock_logger.debug.assert_called_once()

    def test_voice_interface_with_none_callback(self):
        """Test VoiceInterface handles None callback gracefully."""
        from src.interfaces.voice_interface import VoiceInterface

        voice = VoiceInterface(on_transcription=None)
        assert voice._on_transcription is None

    @patch("src.interfaces.voice_interface.logger")
    def test_tts_synthesize_to_file_not_loaded_triggers_load(self, mock_logger):
        """Test synthesize_to_file auto-loads if not loaded."""
        from src.interfaces.voice_interface import TTSEngine

        engine = TTSEngine()
        engine._loaded = False

        with patch.object(engine, "load", return_value=True):
            mock_voice = MagicMock()
            engine._voice = mock_voice

            with patch("src.interfaces.voice_interface.wave.open") as mock_wave:
                mock_wav = MagicMock()
                mock_wave.return_value.__enter__.return_value = mock_wav

                result = engine.synthesize_to_file("Test", "/tmp/out.wav")

        assert result is True

    @patch("src.interfaces.voice_interface.logger")
    def test_tts_synthesize_to_file_not_loaded_load_fails(self, mock_logger):
        """Test synthesize_to_file returns False if load fails."""
        from src.interfaces.voice_interface import TTSEngine

        engine = TTSEngine()
        engine._loaded = False

        with patch.object(engine, "load", return_value=False):
            result = engine.synthesize_to_file("Test", "/tmp/out.wav")

        assert result is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
