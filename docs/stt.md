# `backend/stt.py` — Moonshine Speech-to-Text Transcriber

> **File:** `backend/stt.py` (125 lines)
> **Purpose:** Lightweight in-process speech-to-text wrapper around Hugging Face's `transformers` ASR pipeline, configured for the **Moonshine-tiny** model (`UsefulSensors/moonshine-tiny`). Handles arbitrary audio byte streams (WAV, FLAC, OGG via `soundfile`; WebM/MP3/M4A via `pydub` fallback).

---

## 1. Purpose & Overview

`stt.py` is the **voice input** layer of the assistant. The Streamlit frontend (`streamlit_app.py`) records microphone audio in the browser and POSTs it to `/api/v1/transcribe` (`backend/main.py:401`). The handler:

1. Reads the multipart upload as raw bytes.
2. Calls `get_transcriber().transcribe(audio_bytes)` — the singleton returned by `stt.py`.
3. Returns the transcribed text plus device, duration, and inference timing metadata.

`stt.py` itself does three things:

1. **Lazy-singleton initialization** of `MoonshineTranscriber` so the ~few-hundred-MB model is loaded exactly once across the lifetime of the FastAPI process.
2. **Audio decoding** from arbitrary containers to a 1-D float32 numpy array at 16 kHz mono.
3. **Inference** through `transformers.pipeline(task="automatic-speech-recognition", model=…, device=…)`.

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                  MoonshineTranscriber                                 │
│                                                                      │
│  __init__()                                                           │
│    │                                                                 │
│    ├─► Resolve device (cuda:0 if available, else cpu)                 │
│    │                                                                 │
│    └─► transformers.pipeline(                                         │
│           task="automatic-speech-recognition",                        │
│           model=settings.model.stt_model,                            │
│           device=self.device,                                        │
│        )                                                             │
│                                                                      │
│  transcribe(audio_bytes: bytes) -> Dict                              │
│    │                                                                 │
│    ├─► reject if len(audio_bytes) < 100                              │
│    │                                                                 │
│    ├─► _load_audio_to_numpy(audio_bytes)                             │
│    │     ├─► try soundfile.read(BytesIO, dtype='float32')            │
│    │     │       → stereo? .mean(axis=1) → mono                      │
│    │     │       → returns (data, sample_rate)                       │
│    │     │                                                          │
│    │     └─► on failure: pydub.AudioSegment.from_file(BytesIO)       │
│    │              .set_channels(1).set_frame_rate(16000)              │
│    │              → 16-bit PCM bytes → float32 via /2^15             │
│    │                                                                 │
│    ├─► duration = len(audio_np) / sample_rate                         │
│    │                                                                 │
│    ├─► result = pipe({"raw": audio_np, "sampling_rate": sample_rate})│
│    │                                                                 │
│    └─► return {                                                      │
│            "text": result["text"].strip(),                           │
│            "success": True,                                          │
│            "error": None,                                            │
│            "device_used": self.device,                               │
│            "duration_seconds": round(duration, 2),                   │
│            "inference_time_seconds": round(elapsed, 3),              │
│         }                                                            │
└──────────────────────────────────────────────────────────────────────┘

Caller chain:
   POST /api/v1/transcribe (multipart UploadFile)
        │
        ▼
   backend/main.py:401 transcribe_audio(file)
        │  audio_bytes = await file.read()
        │  result = get_transcriber().transcribe(audio_bytes)
        │  return TranscribeResponse(**result)
        │
        ▼
   stt.py:120  get_transcriber()         ← module-level singleton
        │
        ▼
   stt.py:66   MoonshineTranscriber.transcribe(audio_bytes)
```

### Module-level layout

| Lines         | Section                                                       |
| ------------- | ------------------------------------------------------------- |
| `1–10`        | Imports + module-level singleton handle (`_transcriber_instance`) |
| `13–117`      | `class MoonshineTranscriber` — model init + inference       |
| `120–125`     | `get_transcriber()` — singleton factory                     |

---

## 3. Key Classes & Functions

### 3.1 Module-level singleton

```python
_transcriber_instance: Optional["MoonshineTranscriber"] = None
```

Lazy, process-global. The first call to `get_transcriber()` instantiates the model; subsequent calls reuse it. This matters because loading Moonshine-tiny + the `transformers` ASR pipeline takes several seconds and consumes ~200–400 MB of RAM.

### 3.2 `class MoonshineTranscriber` — `backend/stt.py:13`

#### 3.2.1 Constructor — `backend/stt.py:16`

```python
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
    self.pipe = pipeline(
        task="automatic-speech-recognition",
        model=self.model_name,
        device=self.device,
    )
    t_elapsed = time.perf_counter() - t0
    print(f"[STT] Moonshine STT initialized successfully in {t_elapsed:.2f}s (device={self.device})")
```

| Parameter | Default | Source | Notes |
| --------- | ------- | ------ | ----- |
| `model_name` | `None` → `settings.model.stt_model` | `os.getenv("STT_MODEL_NAME", "UsefulSensors/moonshine-tiny")` | Hugging Face model id. Override-able for testing. |
| `device_preference` | `None` → `settings.model.stt_device` | `os.getenv("STT_DEVICE", "cpu")` | `"cpu"`, `"cuda"`, or `"auto"`. CUDA requested but unavailable → silently falls back to CPU. |

The pipeline call returns a callable that accepts `{"raw": np.ndarray, "sampling_rate": int}` and returns `{"text": "..."}`. Internally it handles tokenization, encoder forward pass, and CTC/decoding.

> **Note:** Moonshine is a CTC-based ASR model (vs. Whisper's encoder-decoder). It's deliberately small (~27 M params for `tiny`) and designed for real-time edge inference. The `transformers` pipeline wraps it transparently.

#### 3.2.2 `_load_audio_to_numpy(audio_bytes) -> Tuple[np.ndarray, int]` — `backend/stt.py:43`

Two-tier audio decoder. Both paths return `(audio_float32, sample_rate)`.

**Tier 1 — `soundfile`** (preferred):

```python
buffer = io.BytesIO(audio_bytes)
audio_data, sample_rate = sf.read(buffer, dtype="float32")
if audio_data.ndim > 1:
    audio_data = audio_data.mean(axis=1)   # multi-channel → mono by averaging
return audio_data, sample_rate
```

Supports WAV, FLAC, OGG/Opus, and other PCM-native formats. Returns native sample rate (could be anything; 44.1 kHz, 48 kHz, etc.).

**Tier 2 — `pydub` fallback** (for browser-recorded WebM, MP3, M4A):

```python
buffer.seek(0)
from pydub import AudioSegment
audio_seg = AudioSegment.from_file(buffer)
audio_seg = audio_seg.set_channels(1).set_frame_rate(16000)
raw_samples = np.array(audio_seg.get_array_of_samples(), dtype=np.float32)
max_val = float(1 << (8 * audio_seg.sample_width - 1))
normalized = raw_samples / max_val
return normalized, 16000
```

Steps:
1. **Mono conversion** via `.set_channels(1)`.
2. **Resample to 16 kHz** via `.set_frame_rate(16000)`.
3. **Normalize** integer PCM samples to `[-1.0, 1.0]` by dividing by `2^(sample_width*8 - 1)` (e.g., `2^15 = 32768` for 16-bit audio).

> Moonshine expects 16 kHz mono float32. The pipeline can resample internally too, but doing it explicitly guarantees a known shape before inference.

#### 3.2.3 `transcribe(audio_bytes) -> Dict[str, Any]` — `backend/stt.py:66`

The public entry point.

```python
def transcribe(self, audio_bytes: bytes) -> Dict[str, Any]:
    if not audio_bytes or len(audio_bytes) < 100:
        return {"text": "", "success": False,
                "error": "Audio payload is empty or too short.",
                "device_used": self.device, "duration_seconds": 0.0}

    t_start = time.perf_counter()
    try:
        audio_np, sample_rate = self._load_audio_to_numpy(audio_bytes)

        if len(audio_np) == 0:
            return {"text": "", "success": False,
                    "error": "No valid audio samples found.",
                    "device_used": self.device, "duration_seconds": 0.0}

        duration = len(audio_np) / float(sample_rate)
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
```

Behavior:

| Parameter | Type | Description |
| --------- | ---- | ----------- |
| `audio_bytes` | `bytes` | Raw bytes from an uploaded file. Can be WAV / FLAC / OGG (via soundfile) or WebM / MP3 / M4A (via pydub). |
| **Returns** | `Dict[str, Any]` | `{text, success, error, device_used, duration_seconds, inference_time_seconds}`. |

**Pre-flight checks:**

1. **Empty / too-short guard** (`len(audio_bytes) < 100`): rejects trivial uploads (a few bytes of header noise) before any decoding work.
2. **Zero-samples guard**: if the decoder succeeded but produced an empty array (e.g., silent recording), returns a structured error rather than crashing the pipeline.

**Timing instrumentation:** `time.perf_counter()` is captured before decoding and reported as `inference_time_seconds` in the response. The print statement (`backend/stt.py:98`) shows up in server logs for every successful transcription, useful for benchmarking.

**Return contract:**

```json
{
    "text": "hello can you find scholarships in germany",
    "success": true,
    "error": null,
    "device_used": "cpu",
    "duration_seconds": 3.42,
    "inference_time_seconds": 0.583
}
```

The FastAPI handler wraps this dict into the Pydantic `TranscribeResponse` schema (`backend/schemas.py`) for OpenAPI validation.

### 3.3 `get_transcriber() -> MoonshineTranscriber` — `backend/stt.py:120`

```python
def get_transcriber() -> MoonshineTranscriber:
    global _transcriber_instance
    if _transcriber_instance is None:
        _transcriber_instance = MoonshineTranscriber()
    return _transcriber_instance
```

Singleton factory. Called from `backend/main.py:405` (`transcriber = get_transcriber()`).

> **Not thread-safe.** If two threads hit `get_transcriber()` simultaneously when `_transcriber_instance is None`, both could create an instance. In practice FastAPI dispatches one request at a time per worker (with async/await for concurrency on the event loop), so this is fine. If we ever moved to thread-pool sync workers, this would need an `RLock`.

---

## 4. Flow / Lifecycle

End-to-end microphone transcription:

```
User clicks "Record" in Streamlit
   │  Browser MediaRecorder captures audio → Blob
   ▼
Streamlit st.audio_recorder() returns bytes
   │  POST /api/v1/transcribe (multipart/form-data, field=file)
   ▼
backend/main.py:401  transcribe_audio(file)
   │  audio_bytes = await file.read()
   │  transcriber = get_transcriber()           ← lazy singleton
   │  result = transcriber.transcribe(audio_bytes)
   │  return TranscribeResponse(**result)
   ▼
backend/stt.py:66   MoonshineTranscriber.transcribe(audio_bytes)
   │  1. empty/short guard
   │  2. _load_audio_to_numpy(audio_bytes)
   │       ├─► soundfile.read(BytesIO, dtype='float32')  ← WAV/FLAC/OGG
   │       │       ├─► stereo? .mean(axis=1)
   │       │       └─► (audio_np, sample_rate)
   │       └─► pydub.AudioSegment.from_file(BytesIO)    ← WebM/MP3/M4A
   │              ├─► .set_channels(1).set_frame_rate(16000)
   │              └─► int PCM → float32 / 2^(sw*8-1)
   │
   │  3. duration = len(audio_np) / sample_rate
   │  4. result = self.pipe({"raw": audio_np, "sampling_rate": sample_rate})
   │  5. return {text, success, ...}
   ▼
Streamlit receives JSON {text, success, ...}
   │  If success: populate chat input box with text
   │  Else: show error banner
   ▼
User submits the transcribed text to /chat/stream as usual
```

### Initialization lifecycle

```
First /transcribe request after server start:
   get_transcriber() → MoonshineTranscriber()  ← 2-5 second cold start
      ├─► Load model weights from HF Hub (or cache)
      ├─► Move tensors to device
      └─► transformers.pipeline(...) returns ASR callable

Subsequent requests:
   get_transcriber() → returns existing instance, no re-init
```

The cold-start cost is paid once per process lifetime — even across thousands of transcriptions. To amortize it further, `main.py:61` could call `get_transcriber()` in the lifespan handler, but currently it relies on lazy init.

---

## 5. Dependencies

| Import | Used for | Why |
| ------ | -------- | --- |
| `io` | `io.BytesIO(audio_bytes)` for in-memory audio decoding | Avoids tempfile creation per request. |
| `time` | `time.perf_counter()` for inference timing | High-resolution monotonic clock. |
| `typing.{Dict, Any, Optional}` | Static typing | Public API. |
| `numpy` | Audio array representation + normalization (`np.float32`, division) | Required by `transformers.pipeline`. |
| `torch` | Device probe (`torch.cuda.is_available()`) | CPU/CUDA selection. |
| `transformers.pipeline` | ASR inference wrapper | Hugging Face's pre-built inference graph for ASR models. |
| `soundfile` | Audio decoding (WAV/FLAC/OGG) | Libsndfile bindings, robust for PCM formats. |
| `pydub.AudioSegment` | Audio decoding fallback (WebM/MP3/M4A) | Requires `ffmpeg` system binary under the hood. |
| `config.settings` | `settings.model.stt_model`, `settings.model.stt_device` | Config injection. |

**System dependency:** `pydub` requires `ffmpeg` (or `avconv`) to be installed and on PATH for the fallback path. The codebase does not enforce this; users on systems without ffmpeg will get an exception from `AudioSegment.from_file` for non-PCM formats. This is caught by the outer `try/except` in `transcribe` and returned as a structured error.

---

## 6. Models & External Services

| Component | Detail |
| --------- | ------ |
| Model | `UsefulSensors/moonshine-tiny` (default; configurable via `STT_MODEL_NAME` env var). |
| Architecture | CTC-based; designed for low-latency streaming/edge inference. |
| Pipeline wrapper | `transformers.pipeline(task="automatic-speech-recognition", model=…, device=…)`. |
| Device | `"cuda:0"` if CUDA available, else `"cpu"`. `STT_DEVICE` env var can force `"cpu"`, `"cuda"`, or `"auto"`. |
| Expected input | 1-D `float32` numpy array at any sample rate; pipeline resamples internally if needed. |
| Output | Plain dict `{"text": "..."}`. |
| Model loading | First call to `MoonshineTranscriber.__init__` triggers Hugging Face model download (if not cached locally at `~/.cache/huggingface/`). |

---

## 7. Notable Algorithms

### 7.1 PCM normalization

```python
max_val = float(1 << (8 * audio_seg.sample_width - 1))
normalized = raw_samples / max_val
```

Bit-exact normalization for arbitrary `sample_width` (8, 16, 24, 32-bit). For 16-bit audio: `max_val = 2^15 = 32768`; for 24-bit: `2^23`. This produces samples in `[-1.0, 1.0]`, the standard range for ASR models.

### 7.2 Mono mixing

```python
if audio_data.ndim > 1:
    audio_data = audio_data.mean(axis=1)
```

Channel-mean downmix for stereo recordings. Avoids the asymmetry of left-only or right-only selection. Works for multi-channel too (5.1 → mono by mean across all channels).

### 7.3 Device fallback chain

`cuda` requested but unavailable → silent fallback to `cpu` (`backend/stt.py:25`). This is friendlier than a hard failure for developers without a GPU who try `STT_DEVICE=cuda`.

### 7.4 Two-tier audio decoding

`soundfile` first because it's fast and pure-Python (libsndfile is compiled C). `pydub` fallback handles the long tail of container formats that browsers actually emit (`audio/webm; codecs=opus` from `MediaRecorder` is the common case).

### 7.5 Empty-payload short-circuit

`if not audio_bytes or len(audio_bytes) < 100` avoids the cost of decoding 50 bytes of header noise (which would otherwise produce a 0-duration audio array, fail at `len(audio_np) == 0`, and return a confusing error). 100 bytes is below any valid audio header (WAV alone is 44 bytes of header, plus a few samples).

---

## 8. Error Handling

| Failure | Behavior |
| ------- | -------- |
| `len(audio_bytes) < 100` | Returns `{success: false, error: "Audio payload is empty or too short."}`. No decoding attempted. |
| `soundfile.read` fails | Falls through to `pydub` tier. |
| `pydub.AudioSegment.from_file` fails (or ffmpeg missing) | Outer `try/except` in `transcribe` catches; returns `{success: false, error: "<exception message>"}`. |
| Decoded audio has zero samples | Returns `{success: false, error: "No valid audio samples found."}`. |
| `pipe(...)` raises | Outer `try/except` catches; returns `{success: false, error: "<exception message>"}`. |
| Model not found on Hugging Face | `transformers.pipeline` raises at init time; exception bubbles up to FastAPI which returns 500. |
| CUDA requested but unavailable | Silently downgrades to CPU. |
| Singleton race (two threads, first call) | Both could construct an instance; mitigated by FastAPI's async event loop. |

The transcribe method **never raises** — every error path produces a structured response. The FastAPI handler can therefore blindly do `TranscribeResponse(**result)`.

---

## 9. Notable Patterns & Design Decisions

1. **Lazy singleton.** Loading Moonshine takes ~2–5 seconds and ~300 MB RAM. The singleton ensures this cost is paid exactly once. `backend/main.py` could warm it eagerly in `lifespan`, but lazy is simpler and the first request absorbs the delay.

2. **Two-tier audio decoder.** `soundfile` for fast PCM-native formats, `pydub` (with `ffmpeg`) for everything else. This covers both `audio/wav` uploads (curl/Postman/Streamlit `st.audio_input`) and browser-emitted `audio/webm` from `MediaRecorder`.

3. **Structured error responses.** Even on errors, the API returns a Pydantic-validated dict (not a thrown exception). The frontend can render the error uniformly without try/catch boilerplate.

4. **Device fallback resilience.** `cuda` → `cpu` silently. No "CUDA not available, please set STT_DEVICE=cpu" dance for casual users.

5. **Timing instrumentation built in.** Every transcription logs `[STT] Transcribed 3.42s audio in 0.583s: "..."` and returns `inference_time_seconds` in the response. Useful for performance debugging and for showing users "waited 0.6s" feedback in the UI.

6. **No upsampling at inference boundary.** The pipeline can resample internally if needed, but `_load_audio_to_numpy` already standardizes the fallback path to 16 kHz. The soundfile path keeps the native rate; the pipeline's internal feature extractor handles whatever rate comes in.

7. **Tiny model by default.** `moonshine-tiny` is ~27 M parameters, deliberately small for sub-second inference on CPU. The system is designed for low-latency voice UX, not for accuracy on noisy far-field audio. If higher accuracy is ever needed, switching `STT_MODEL_NAME` to `UsefulSensors/moonshine-base` is a one-line change.

8. **Pure inference wrapper.** No stateful VAD, no streaming, no partial-result emission. Each call is a single-shot transcription. Simpler API, easier error handling, well-suited to the "record → transcribe → submit" interaction pattern in the Streamlit UI.

9. **Inference happens in `pipeline(...)`'s default chunking.** `transformers.pipeline("automatic-speech-recognition")` internally chunks long audio at ~30-second windows to fit the model's context window. For longer recordings, the text comes back as a concatenated string. No manual chunking in `stt.py`.

10. **Pre-flight size guard.** `len(audio_bytes) < 100` rejects obviously-malformed uploads (browsers can sometimes send empty blobs from cancelled recordings) before spending any decoding time.

---

## Cross-references

- HTTP handler: `backend/main.py:401` `transcribe_audio(file: UploadFile)` → calls `get_transcriber().transcribe(audio_bytes)`.
- Settings: `config.py:36–37` — `stt_model`, `stt_device`.
- Schema: `backend/schemas.py::TranscribeResponse` (consumed by OpenAPI docs and Pydantic validation).
- Frontend: `streamlit_app.py` (records audio in the browser and POSTs the bytes).
- Rate limiting: `/transcribe` is tier-rated at `rate_limit_transcribe_per_minute` (default 15/min/IP, `backend/rate_limit.py`).