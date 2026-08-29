import io
import time
from typing import Dict, Any, Optional
import numpy as np
import torch
from transformers import pipeline
import soundfile as sf
from config import settings

_transcriber_instance: Optional["MoonshineTranscriber"] = None


class MoonshineTranscriber:
    """Moonshine Speech-to-Text transcriber powered by UsefulSensors/moonshine-tiny and Hugging Face transformers."""

    def __init__(
        self,
        model_name: Optional[str] = None,
        device_preference: Optional[str] = None,
    ):
        self.model_name = model_name or settings.model.stt_model
        pref = (device_preference or settings.model.stt_device).lower().strip()

        if pref == "cuda":
            self.device = "cuda:0" if torch.cuda.is_available() else "cpu"
        elif pref == "cpu":
            self.device = "cpu"
        else:  # "auto"
            self.device = "cuda:0" if torch.cuda.is_available() else "cpu"

        print(f"[STT] Initializing Moonshine STT model: {self.model_name} on device: {self.device}...")
        t0 = time.perf_counter()
        
        # Initialize Hugging Face ASR pipeline
        self.pipe = pipeline(
            task="automatic-speech-recognition",
            model=self.model_name,
            device=self.device,
        )
        t_elapsed = time.perf_counter() - t0
        print(f"[STT] Moonshine STT initialized successfully in {t_elapsed:.2f}s (device={self.device})")

    def _load_audio_to_numpy(self, audio_bytes: bytes) -> tuple[np.ndarray, int]:
        """Convert arbitrary audio byte stream to a 1D float32 numpy array and sample rate."""
        buffer = io.BytesIO(audio_bytes)
        try:
            # First attempt direct read with soundfile (supports WAV, FLAC, OGG, etc.)
            audio_data, sample_rate = sf.read(buffer, dtype="float32")
            if audio_data.ndim > 1:
                # Convert stereo/multi-channel to mono by averaging channels
                audio_data = audio_data.mean(axis=1)
            return audio_data, sample_rate
        except Exception:
            # Fallback to pydub for container formats like WebM/MP3/M4A
            buffer.seek(0)
            from pydub import AudioSegment
            audio_seg = AudioSegment.from_file(buffer)
            # Standardize to 16kHz mono float32
            audio_seg = audio_seg.set_channels(1).set_frame_rate(16000)
            raw_samples = np.array(audio_seg.get_array_of_samples(), dtype=np.float32)
            # Normalize 16-bit integer PCM to [-1.0, 1.0] float range
            max_val = float(1 << (8 * audio_seg.sample_width - 1))
            normalized = raw_samples / max_val
            return normalized, 16000

    def transcribe(self, audio_bytes: bytes) -> Dict[str, Any]:
        """Transcribe raw audio bytes to text."""
        if not audio_bytes or len(audio_bytes) < 100:
            return {
                "text": "",
                "success": False,
                "error": "Audio payload is empty or too short.",
                "device_used": self.device,
                "duration_seconds": 0.0,
            }

        t_start = time.perf_counter()
        try:
            audio_np, sample_rate = self._load_audio_to_numpy(audio_bytes)

            if len(audio_np) == 0:
                return {
                    "text": "",
                    "success": False,
                    "error": "No valid audio samples found.",
                    "device_used": self.device,
                    "duration_seconds": 0.0,
                }

            duration = len(audio_np) / float(sample_rate)

            # Run inference through Moonshine pipeline
            # Pass dictionary with raw float32 array and sampling rate
            result = self.pipe({"raw": audio_np, "sampling_rate": sample_rate})
            raw_text = result.get("text", "").strip()

            t_elapsed = time.perf_counter() - t_start
            print(f"[STT] Transcribed {duration:.2f}s audio in {t_elapsed:.3f}s: \"{raw_text}\"")

            return {
                "text": raw_text,
                "success": True,
                "error": None,
                "device_used": self.device,
                "duration_seconds": round(duration, 2),
                "inference_time_seconds": round(t_elapsed, 3),
            }
        except Exception as exc:
            t_elapsed = time.perf_counter() - t_start
            print(f"[STT] Transcription failed after {t_elapsed:.3f}s: {exc}")
            return {
                "text": "",
                "success": False,
                "error": str(exc),
                "device_used": self.device,
                "duration_seconds": 0.0,
            }


def get_transcriber() -> MoonshineTranscriber:
    """Singleton getter for the Moonshine transcriber instance."""
    global _transcriber_instance
    if _transcriber_instance is None:
        _transcriber_instance = MoonshineTranscriber()
    return _transcriber_instance
