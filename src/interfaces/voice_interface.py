"""
Voice Interface for Archi — Speech-to-Text (STT) and Text-to-Speech (TTS).

STT: faster-whisper (CTranslate2-based, 4x faster than OpenAI Whisper)
TTS: piper-tts (lightweight ONNX-based, real-time on CPU)
Audio I/O: sounddevice (cross-platform, bundles PortAudio — no C compiler needed)

Usage:
    voice = VoiceInterface()
    voice.start_listening()   # Continuous microphone → text
    voice.speak("Hello!")     # Text → speaker output

Env vars:
    ARCHI_VOICE_ENABLED=true       Enable voice interface (default: false)
    ARCHI_WHISPER_MODEL=large-v3-turbo  Whisper model size (default: base)
    ARCHI_WHISPER_DEVICE=cuda      Device for STT (default: auto)
    ARCHI_PIPER_VOICE=en_US-lessac-medium  Piper voice name (default: en_US-lessac-medium)
    ARCHI_VOICE_SILENCE_THRESHOLD=0.03  Silence detection threshold
    ARCHI_VOICE_SILENCE_DURATION=1.5   Seconds of silence to end utterance
"""

import io
import logging
import os
import queue
import struct
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Any, Callable, Dict, Optional


logger = logging.getLogger(__name__)


class STTEngine:
    """Speech-to-Text using faster-whisper."""

    def __init__(
        self,
        model_size: str = "base",
        device: str = "auto",
        compute_type: str = "auto",
    ) -> None:
        """
        Initialize the STT engine.

        Args:
            model_size: Whisper model size. Options:
                - "tiny" (~39MB, fastest, least accurate)
                - "base" (~74MB, good balance for real-time)
                - "small" (~244MB)
                - "medium" (~769MB)
                - "large-v3-turbo" (~809MB, best accuracy, recommended if VRAM allows)
            device: "cuda", "cpu", or "auto"
            compute_type: "float16", "int8", or "auto"
        """
        self._model = None
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._loaded = False

    def load(self) -> bool:
        """Load the Whisper model. Returns True if successful."""
        if self._loaded:
            return True
        try:
            from faster_whisper import WhisperModel

            # Auto-detect best settings
            device = self._device
            compute_type = self._compute_type
            if device == "auto":
                try:
                    import torch
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                except ImportError:
                    device = "cpu"
            if compute_type == "auto":
                compute_type = "float16" if device == "cuda" else "int8"

            logger.info(
                "Loading Whisper model: %s (device=%s, compute=%s)",
                self._model_size, device, compute_type,
            )
            self._model = WhisperModel(
                self._model_size,
                device=device,
                compute_type=compute_type,
            )
            self._loaded = True
            logger.info("Whisper STT loaded successfully")
            return True
        except ImportError:
            logger.warning(
                "faster-whisper not installed. Run: pip install faster-whisper"
            )
            return False
        except Exception as e:
            logger.error("Failed to load Whisper model: %s", e)
            return False

    def transcribe_file(self, audio_path: str, language: str = "en") -> str:
        """Transcribe an audio file to text."""
        if not self._loaded:
            if not self.load():
                return ""
        try:
            segments, info = self._model.transcribe(
                audio_path,
                language=language,
                beam_size=5,
                vad_filter=True,
            )
            text = " ".join(seg.text.strip() for seg in segments).strip()
            if text:
                logger.info("STT transcribed: %s", text[:80])
            else:
                logger.debug("STT: audio processed but no speech detected (VAD filtered)")
            return text
        except Exception as e:
            logger.error("STT transcription failed: %s", e)
            return ""

    def transcribe_bytes(self, audio_bytes: bytes, sample_rate: int = 16000, language: str = "en") -> str:
        """Transcribe raw PCM audio bytes to text."""
        # Write to temp WAV file (faster-whisper needs a file path)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
            with wave.open(tmp, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)  # 16-bit
                wf.setframerate(sample_rate)
                wf.writeframes(audio_bytes)
        try:
            return self.transcribe_file(tmp_path, language=language)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    @property
    def is_loaded(self) -> bool:
        return self._loaded


class TTSEngine:
    """Text-to-Speech using piper-tts."""

    def __init__(self, voice_name: str = "en_US-lessac-medium") -> None:
        """
        Initialize TTS engine.

        Args:
            voice_name: Piper voice name. Common options:
                - "en_US-lessac-medium" (clear American male)
                - "en_US-amy-medium" (American female)
                - "en_GB-alan-medium" (British male)
        """
        self._voice = None
        self._voice_name = voice_name
        self._loaded = False
        self._sample_rate = 22050

    def load(self) -> bool:
        """Load the TTS voice model. Returns True if successful."""
        if self._loaded:
            return True
        try:
            from piper import PiperVoice

            # Piper voices are downloaded automatically on first use
            # from https://github.com/rhasspy/piper/blob/master/VOICES.md
            model_dir = Path(__file__).resolve().parent.parent.parent / "models" / "piper"
            model_dir.mkdir(parents=True, exist_ok=True)

            model_path = model_dir / f"{self._voice_name}.onnx"
            config_path = model_dir / f"{self._voice_name}.onnx.json"

            if model_path.exists() and config_path.exists():
                self._voice = PiperVoice.load(str(model_path), str(config_path))
                self._loaded = True
                logger.info("Piper TTS loaded: %s", self._voice_name)
                return True
            else:
                logger.warning(
                    "Piper voice not found at %s. Download it first:\n"
                    "  See: https://github.com/rhasspy/piper/blob/master/VOICES.md\n"
                    "  Place %s.onnx and %s.onnx.json in %s",
                    model_path, self._voice_name, self._voice_name, model_dir,
                )
                return False
        except ImportError:
            logger.warning("piper-tts not installed. Run: pip install piper-tts")
            return False
        except Exception as e:
            logger.error("Failed to load Piper TTS: %s", e)
            return False

    def synthesize_to_file(self, text: str, output_path: str) -> bool:
        """Synthesize text to a WAV file."""
        if not self._loaded:
            if not self.load():
                return False
        try:
            with wave.open(output_path, "wb") as wav_file:
                self._voice.synthesize(text, wav_file)
            logger.info("TTS synthesized to: %s", output_path)
            return True
        except Exception as e:
            logger.error("TTS synthesis failed: %s", e)
            return False

    def synthesize_to_bytes(self, text: str) -> Optional[bytes]:
        """Synthesize text and return WAV bytes."""
        if not self._loaded:
            if not self.load():
                return None
        try:
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(self._sample_rate)
                self._voice.synthesize(text, wav_file)
            return buf.getvalue()
        except Exception as e:
            logger.error("TTS synthesis failed: %s", e)
            return None

    def speak(self, text: str) -> bool:
        """Synthesize and play text through speakers (blocking)."""
        if not self._loaded:
            if not self.load():
                return False
        try:
            import numpy as np
            import sounddevice as sd

            audio_data = self.synthesize_to_bytes(text)
            if not audio_data:
                return False

            # Parse WAV header to get format
            buf = io.BytesIO(audio_data)
            with wave.open(buf, "rb") as wf:
                channels = wf.getnchannels()
                sample_width = wf.getsampwidth()
                rate = wf.getframerate()
                frames = wf.readframes(wf.getnframes())

            # Convert raw bytes to numpy array for sounddevice
            dtype_map = {1: np.int8, 2: np.int16, 4: np.int32}
            dtype = dtype_map.get(sample_width, np.int16)
            audio_array = np.frombuffer(frames, dtype=dtype)
            if channels > 1:
                audio_array = audio_array.reshape(-1, channels)

            sd.play(audio_array, samplerate=rate)
            sd.wait()  # Block until playback finishes
            return True
        except ImportError:
            logger.warning(
                "sounddevice not installed. Run: pip install sounddevice numpy"
            )
            return False
        except Exception as e:
            logger.error("TTS playback failed: %s", e)
            return False

    @property
    def is_loaded(self) -> bool:
        return self._loaded


class VoiceInterface:
    """
    Unified voice interface combining STT and TTS.

    Provides continuous listening (microphone → text callback) and
    speech output (text → speaker). Integrates with Archi's message
    processing pipeline.
    """

    def __init__(
        self,
        on_transcription: Optional[Callable[[str], None]] = None,
        whisper_model: Optional[str] = None,
        piper_voice: Optional[str] = None,
    ) -> None:
        """
        Args:
            on_transcription: Callback when speech is transcribed to text
            whisper_model: Override Whisper model size
            piper_voice: Override Piper voice name
        """
        self._on_transcription = on_transcription
        self._listening = False
        self._listen_thread: Optional[threading.Thread] = None
        self._audio_queue: queue.Queue = queue.Queue()

        # Configure from env
        model = whisper_model or os.environ.get("ARCHI_WHISPER_MODEL", "base")
        device = os.environ.get("ARCHI_WHISPER_DEVICE", "auto")
        voice = piper_voice or os.environ.get("ARCHI_PIPER_VOICE", "en_US-lessac-medium")

        self.stt = STTEngine(model_size=model, device=device)
        self.tts = TTSEngine(voice_name=voice)

        # Voice activity detection settings
        self._silence_threshold = float(os.environ.get("ARCHI_VOICE_SILENCE_THRESHOLD", "0.03"))
        self._silence_duration = float(os.environ.get("ARCHI_VOICE_SILENCE_DURATION", "1.5"))

    def initialize(self) -> Dict[str, bool]:
        """Load both STT and TTS models. Returns status dict."""
        stt_ok = self.stt.load()
        tts_ok = self.tts.load()
        return {"stt": stt_ok, "tts": tts_ok}

    def start_listening(self) -> bool:
        """
        Start continuous microphone listening in a background thread.
        Transcribed text is passed to the on_transcription callback.
        """
        if self._listening:
            logger.warning("Already listening")
            return True

        if not self.stt.is_loaded:
            if not self.stt.load():
                logger.error("Cannot start listening: STT not available")
                return False

        try:
            import sounddevice  # noqa: F401
        except ImportError:
            logger.error(
                "sounddevice not installed. Run: pip install sounddevice numpy"
            )
            return False

        self._listening = True
        self._listen_thread = threading.Thread(
            target=self._listen_loop,
            daemon=True,
            name="ArchiVoiceListener",
        )
        self._listen_thread.start()
        logger.info("Voice listening started")
        return True

    def stop_listening(self) -> None:
        """Stop continuous microphone listening."""
        self._listening = False
        if self._listen_thread:
            self._listen_thread.join(timeout=3)
            self._listen_thread = None
        logger.info("Voice listening stopped")

    def _listen_loop(self) -> None:
        """Background thread: capture audio, detect speech, transcribe."""
        try:
            import numpy as np
            import sounddevice as sd
        except ImportError:
            return

        RATE = 16000
        CHUNK = 1024
        CHANNELS = 1

        try:
            logger.info("Microphone stream opened (rate=%d)", RATE)

            audio_buffer = bytearray()
            silence_start = None
            speaking = False

            with sd.InputStream(
                samplerate=RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK,
            ) as stream:
                while self._listening:
                    try:
                        # Read returns (numpy array, overflow flag)
                        frames, overflowed = stream.read(CHUNK)
                        data = frames.tobytes()
                    except Exception:
                        continue

                    # Simple energy-based VAD
                    rms = self._calculate_rms(data)

                    if rms > self._silence_threshold:
                        # Speech detected
                        speaking = True
                        silence_start = None
                        audio_buffer.extend(data)
                    elif speaking:
                        # Silence after speech
                        audio_buffer.extend(data)
                        if silence_start is None:
                            silence_start = time.time()
                        elif time.time() - silence_start > self._silence_duration:
                            # End of utterance — transcribe
                            if len(audio_buffer) > RATE * 2:  # at least 1 second
                                text = self.stt.transcribe_bytes(
                                    bytes(audio_buffer), sample_rate=RATE,
                                )
                                if text and self._on_transcription:
                                    self._on_transcription(text)
                            audio_buffer.clear()
                            speaking = False
                            silence_start = None
        except Exception as e:
            logger.error("Voice listen loop error: %s", e)

    @staticmethod
    def _calculate_rms(data: bytes) -> float:
        """Calculate RMS energy of 16-bit PCM audio."""
        count = len(data) // 2
        if count == 0:
            return 0.0
        shorts = struct.unpack(f"<{count}h", data)
        sum_sq = sum(s * s for s in shorts)
        rms = (sum_sq / count) ** 0.5
        return rms / 32768.0  # Normalize to 0..1

    def speak(self, text: str) -> bool:
        """Speak text through speakers. Returns True if successful."""
        return self.tts.speak(text)

    def transcribe_file(self, path: str) -> str:
        """Transcribe an audio file. Returns text."""
        return self.stt.transcribe_file(path)

    @property
    def is_listening(self) -> bool:
        return self._listening

    def get_status(self) -> Dict[str, Any]:
        """Return voice interface status."""
        return {
            "stt_loaded": self.stt.is_loaded,
            "tts_loaded": self.tts.is_loaded,
            "listening": self._listening,
            "whisper_model": self.stt._model_size,
            "piper_voice": self.tts._voice_name,
        }
