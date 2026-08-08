"""Media understanding tools for lama_ole — image/video/audio comprehension
via Ollama vision models, Whisper, and OCR."""

import base64
import os
import shutil
import subprocess
import tempfile

from tool_base import get_ollama_host, get_vision_models, tool
from tools_security.validate_path import validate_path as _validate_path


__tool_env__ = {
    "LAMA_OLE_VISION_HOST": "Ollama host for vision/audio models (defaults to --host value)",
}

try:
    from ollama import Client as OllamaClient
    HAS_OLLAMA = True
except ImportError:
    HAS_OLLAMA = False

try:
    from PIL import Image
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

_FFMPEG = shutil.which("ffmpeg")
_FFPROBE = shutil.which("ffprobe")
_TESSERACT = shutil.which("tesseract")

_VISION_CANDIDATES = ["gemma3", "llava", "bakllava", "moondream", "llava-llama3"]
_WHISPER_CANDIDATES = ["whisper", "faster-whisper"]


def _ollama_host():
    return os.environ.get("LAMA_OLE_VISION_HOST") or get_ollama_host()


def _ollama_client():
    host = _ollama_host()
    if not host.startswith(("http://", "https://")) and ":" in host:
        host = f"http://{host}"
    return OllamaClient(host=host)


def _find_model(client, candidates, default_name):
    try:
        installed = [m.name for m in client.list().models]
        full_names = []
        for m in installed:
            parts = m.split(":")
            full_names.append(parts[0])
        for cand in candidates:
            for full in full_names:
                if full == cand:
                    return cand
            for full in installed:
                if full.startswith(cand):
                    return full
        for cand in candidates:
            for full in installed:
                if cand in full:
                    return full
    except Exception:
        pass
    return default_name


def _no_ollama():
    return "Error: ollama library not installed. Run: pip install ollama"


def _image_to_base64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _ollama_vision(prompt, path, model=""):
    if not HAS_OLLAMA:
        return _no_ollama()
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    try:
        client = _ollama_client()
        user_models = get_vision_models()
        if user_models:
            resolved_model = model or _find_model(client, user_models, user_models[0])
        else:
            resolved_model = model or _find_model(client, _VISION_CANDIDATES, "llava")
        b64 = _image_to_base64(path)
        resp = client.chat(
            model=resolved_model,
            messages=[{
                "role": "user",
                "content": prompt,
                "images": [b64],
            }],
        )
        return resp.message.content
    except Exception as e:
        return f"Error calling vision model: {e}"


def _ollama_transcribe(path, model=""):
    if not HAS_OLLAMA:
        return _no_ollama()
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    try:
        client = _ollama_client()
        resolved_model = model or _find_model(client, _WHISPER_CANDIDATES, "whisper")
        resp = client.chat(
            model=resolved_model,
            messages=[{
                "role": "user",
                "content": "Transcribe this audio.",
            }],
        )
        return resp.message.content
    except Exception as e:
        return f"Error transcribing audio: {e}"


# ---------------------------------------------------------------------------
# Image understanding
# ---------------------------------------------------------------------------


@tool(description="Describe the contents of an image using a vision model")
def image_describe(path: str, model: str = "") -> str:
    return _ollama_vision("Describe this image in detail.", path, model)


@tool(description="Ask a specific question about an image using a vision model")
def image_ask(path: str, question: str, model: str = "") -> str:
    return _ollama_vision(question, path, model)


@tool(description="Extract text from an image using OCR (requires tesseract)")
def image_ocr(path: str, lang: str = "eng") -> str:
    if not _TESSERACT:
        return "Error: tesseract not installed. Run: apt install tesseract-ocr"
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    try:
        result = subprocess.run(
            [_TESSERACT, path, "stdout", "-l", lang],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return f"tesseract error: {result.stderr.strip()}"
        text = result.stdout.strip()
        return text if text else "(no text found in image)"
    except subprocess.TimeoutExpired:
        return "Error: tesseract timed out"
    except Exception as e:
        return f"Error running OCR: {e}"


# ---------------------------------------------------------------------------
# Video understanding
# ---------------------------------------------------------------------------


@tool(description="Describe a video by extracting frames at regular intervals and analyzing them with a vision model")
def video_describe(path: str, interval: float = 5, model: str = "") -> str:
    if not _FFMPEG:
        return "Error: ffmpeg is not required (required for frame extraction)"
    if not HAS_OLLAMA:
        return _no_ollama()
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    if not HAS_PILLOW:
        return "Error: Pillow is required. Run: pip install Pillow"
    with tempfile.TemporaryDirectory() as tmp:
        try:
            client = _ollama_client()
            user_models = get_vision_models()
            if user_models:
                resolved_model = model or _find_model(client, user_models, user_models[0])
            else:
                resolved_model = model or _find_model(client, _VISION_CANDIDATES, "llava")
            probe = subprocess.run(
                [_FFPROBE, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path],
                capture_output=True, text=True, timeout=30,
            )
            if probe.returncode != 0:
                return f"ffprobe error: {probe.stderr.strip()}"
            import json
            data = json.loads(probe.stdout)
            duration = 0
            fmt = data.get("format", {})
            if fmt:
                duration = float(fmt.get("duration", 0))
        except Exception as e:
            return f"Error probing video: {e}"
        try:
            result = subprocess.run(
                [_FFMPEG, "-i", path, "-vf", f"fps=1/{interval}", os.path.join(tmp, "frame_%04d.jpg")],
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode != 0:
                return f"ffmpeg error: {result.stderr.strip()}"
        except subprocess.TimeoutExpired:
            return "Error: ffmpeg timed out"
        frame_files = sorted(f for f in os.listdir(tmp) if f.startswith("frame_"))
        if not frame_files:
            return "Error: no frames could be extracted from the video"
        descriptions = []
        for fname in frame_files:
            fpath = os.path.join(tmp, fname)
            try:
                img = Image.open(fpath)
                width, height = img.size
                img.close()
            except Exception:
                width, height = 0, 0
            num = int(fname.split("_")[1].split(".")[0])
            timestamp = round(num * interval, 1)
            if timestamp > duration:
                timestamp = round(duration, 1)
            desc = _ollama_vision(
                "Describe what is visible in this video frame briefly.",
                fpath, resolved_model,
            )
            descriptions.append(f"[{timestamp}s]{desc}")
        summary = "\n\n".join(descriptions)
        return f"Video duration: {duration:.1f}s\nExtracted {len(frame_files)} frames at {interval}s intervals\n\n{summary}"


@tool(description="Detect scene changes/cuts in a video using ffmpeg scene detection")
def video_scene_changes(path: str, threshold: float = 0.3) -> str:
    if not _FFMPEG:
        return "Error: ffmpeg is not installed (required for scene detection)"
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    try:
        result = subprocess.run(
            [_FFMPEG, "-i", path, "-filter:v", f"select='gt(scene,{threshold})'",
             "-show_entries", "frame=pts_time", "-vsync", "vfr", "-f", "null", "-"],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            return f"ffmpeg error: {result.stderr.strip()}"
        import re
        times = re.findall(r"pts_time:([\d.]+)", result.stderr)
        if not times:
            return f"No scene changes detected (threshold={threshold}). Try a lower threshold."
        scenes = []
        for i, t in enumerate(times):
            scenes.append({"scene": i + 1, "timestamp_sec": round(float(t), 2)})
        import json
        return json.dumps({"threshold": threshold, "total_scenes": len(scenes), "scenes": scenes}, indent=2)
    except subprocess.TimeoutExpired:
        return "Error: ffmpeg timed out"
    except Exception as e:
        return f"Error detecting scene changes: {e}"


@tool(description="Extract audio from a video and transcribe it using Whisper")
def video_transcribe(path: str, model: str = "") -> str:
    if not _FFMPEG:
        return "Error: ffmpeg is not installed (required for audio extraction)"
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    with tempfile.TemporaryDirectory() as tmp:
        audio_out = os.path.join(tmp, "audio.wav")
        try:
            result = subprocess.run(
                [_FFMPEG, "-i", path, "-vn", "-acodec", "pcm_s16le", "-ar", "16000",
                 "-ac", "1", "-y", audio_out],
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode != 0:
                return f"ffmpeg error extracting audio: {result.stderr.strip()}"
        except subprocess.TimeoutExpired:
            return "Error: ffmpeg timed out"
        return _ollama_transcribe(audio_out, model)


@tool(description="Ask a question about a video by analyzing frames and audio transcript")
def video_ask(path: str, question: str, interval: float = 10, model: str = "") -> str:
    if not _FFMPEG:
        return "Error: ffmpeg is not installed (required for video processing)"
    if not HAS_OLLAMA:
        return _no_ollama()
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    with tempfile.TemporaryDirectory() as tmp:
        audio_out = os.path.join(tmp, "audio.wav")
        transcript = ""
        try:
            subprocess.run(
                [_FFMPEG, "-i", path, "-vn", "-acodec", "pcm_s16le", "-ar", "16000",
                 "-ac", "1", "-y", audio_out],
                capture_output=True, text=True, timeout=300, check=True,
            )
            transcript = _ollama_transcribe(audio_out, model)
        except Exception:
            transcript = "(could not extract audio)"
        try:
            client = _ollama_client()
            user_models = get_vision_models()
            if user_models:
                resolved_model = model or _find_model(client, user_models, user_models[0])
            else:
                resolved_model = model or _find_model(client, _VISION_CANDIDATES, "llava")
            subprocess.run(
                [_FFMPEG, "-i", path, "-vf", f"fps=1/{interval}", os.path.join(tmp, "frame_%04d.jpg")],
                capture_output=True, text=True, timeout=300,
            )
        except subprocess.TimeoutExpired:
            return "Error: ffmpeg timed out"
        except Exception:
            pass
        frame_files = sorted(f for f in os.listdir(tmp) if f.startswith("frame_"))
        if frame_files:
            mid_idx = len(frame_files) // 2
            mid_frame = os.path.join(tmp, frame_files[mid_idx])
            context = f"Transcript: {transcript}\n\nQuestion: {question}"
            answer = _ollama_vision(context, mid_frame, resolved_model)
            return answer
        return f"No frames could be extracted.\nTranscript: {transcript}\n\n(Answer based only on transcript)"


# ---------------------------------------------------------------------------
# Audio understanding
# ---------------------------------------------------------------------------


@tool(description="Transcribe speech from an audio file using Whisper")
def audio_transcribe(path: str, model: str = "") -> str:
    return _ollama_transcribe(path, model)


@tool(description="Transcribe an audio file and answer a question about its contents")
def audio_ask(path: str, question: str, model: str = "") -> str:
    transcript = _ollama_transcribe(path, model)
    if transcript.startswith("Error"):
        return transcript
    if not HAS_OLLAMA:
        return _no_ollama()
    try:
        client = _ollama_client()
        user_models = get_vision_models()
        if user_models:
            resolved_model = model or _find_model(client, user_models, user_models[0])
        else:
            resolved_model = model or _find_model(client, _VISION_CANDIDATES, "llava")
        resp = client.chat(
            model=resolved_model,
            messages=[{
                "role": "user",
                "content": f"Transcript of an audio recording:\n\n{transcript}\n\nQuestion: {question}",
            }],
        )
        return resp.message.content
    except Exception as e:
        return f"{transcript}\n\n(Error answering question: {e})"


# ---------------------------------------------------------------------------
# Model listing
# ---------------------------------------------------------------------------


@tool(description="List all available vision models configured via --vision_model for media understanding tools")
def list_vision_models() -> str:
    models = get_vision_models()
    if not models:
        return (
            "No vision models explicitly configured (--vision_model). "
            "The system will auto-detect from installed Ollama models "
            f"using built-in candidates: {_VISION_CANDIDATES}"
        )
    return "Available vision models:\n" + "\n".join(f"  - {m}" for m in models)
