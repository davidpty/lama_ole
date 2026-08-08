"""Audio processing tools for lama_ole — requires ffmpeg/ffprobe."""

import json
import os
import shutil
import subprocess
import tempfile

from tool_base import tool
from tools_security.validate_path import validate_path as _validate_path

try:
    import pydub
    HAS_PYDUB = True
except ImportError:
    HAS_PYDUB = False

_FFMPEG = shutil.which("ffmpeg")
_FFPROBE = shutil.which("ffprobe")


def _no_ffmpeg():
    return "Error: ffmpeg is not installed (required for audio tools)"


def _ensure_parent(path):
    parent = os.path.dirname(path)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)


def _resolve_out(path, suffix, ext=None):
    if ext is None:
        ext = os.path.splitext(path)[1]
    base, _ = os.path.splitext(path)
    return f"{base}_{suffix}{ext}"


@tool(description="Get metadata about an audio file (duration, sample rate, channels, codec, bitrate)")
def audio_info(path: str) -> str:
    if not _FFPROBE:
        return _no_ffmpeg()
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    try:
        result = subprocess.run(
            [_FFPROBE, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return f"ffprobe error: {result.stderr.strip()}"
        data = json.loads(result.stdout)
        info = {"path": os.path.abspath(path)}
        fmt = data.get("format", {})
        if fmt:
            info["duration"] = f"{float(fmt.get('duration', 0)):.2f}s"
            info["bitrate"] = fmt.get("bit_rate", "unknown")
            info["format_name"] = fmt.get("format_name", "unknown")
        for s in data.get("streams", []):
            if s.get("codec_type") == "audio":
                info["codec"] = s.get("codec_name", "unknown")
                info["sample_rate"] = s.get("sample_rate", "unknown")
                info["channels"] = s.get("channels", "unknown")
                info["channel_layout"] = s.get("channel_layout", "unknown")
                break
        return json.dumps(info, indent=2)
    except subprocess.TimeoutExpired:
        return "Error: ffprobe timed out"
    except json.JSONDecodeError:
        return "Error: unable to parse ffprobe output"
    except Exception as e:
        return f"Error reading audio info: {e}"


@tool(description="Convert an audio file to a different format (mp3, wav, ogg, flac, m4a, wma)")
def audio_convert(path: str, fmt: str, out: str = "") -> str:
    if not _FFMPEG:
        return _no_ffmpeg()
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    if not out:
        out = _resolve_out(path, f"converted", ext=f".{fmt}")
    _ensure_parent(out)
    ext_map = {
        "mp3": "libmp3lame", "wav": "pcm_s16le", "ogg": "libvorbis",
        "flac": "flac", "m4a": "aac", "wma": "wmav2",
    }
    codec = ext_map.get(fmt.lower())
    if not codec:
        return f"Error: unsupported format '{fmt}'. Use: {', '.join(ext_map)}"
    try:
        result = subprocess.run(
            [_FFMPEG, "-i", path, "-acodec", codec, "-y", out],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            return f"ffmpeg error: {result.stderr.strip()}"
        return f"Converted {path} to {fmt} -> {out}"
    except subprocess.TimeoutExpired:
        return "Error: ffmpeg timed out"
    except Exception as e:
        return f"Error converting audio: {e}"


@tool(description="Trim/extract a segment from an audio file")
def audio_trim(path: str, start: float, duration: float, out: str = "") -> str:
    if not _FFMPEG:
        return _no_ffmpeg()
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    if not out:
        out = _resolve_out(path, f"trim_{start}_{duration}")
    _ensure_parent(out)
    try:
        result = subprocess.run(
            [_FFMPEG, "-ss", str(start), "-i", path, "-t", str(duration),
             "-c", "copy", "-y", out],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            return f"ffmpeg error: {result.stderr.strip()}"
        return f"Trimmed {path} from {start}s for {duration}s -> {out}"
    except subprocess.TimeoutExpired:
        return "Error: ffmpeg timed out"
    except Exception as e:
        return f"Error trimming audio: {e}"


@tool(description="Join multiple audio files sequentially into one file")
def audio_concatenate(paths: list, out: str) -> str:
    if not _FFMPEG:
        return _no_ffmpeg()
    if len(paths) < 2:
        return "Error: need at least 2 audio paths"
    if not out:
        return "Error: 'out' path is required"
    for p in paths:
        err = _validate_path(p)
        if err:
            return err
        if not os.path.isfile(p):
            return f"Error: file not found: {p}"
    _ensure_parent(out)
    list_path = os.path.join(os.path.dirname(out) or ".", "_concat_temp.txt")
    try:
        with open(list_path, "w") as f:
            for p in paths:
                f.write(f"file '{os.path.abspath(p)}'\n")
        result = subprocess.run(
            [_FFMPEG, "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", "-y", out],
            capture_output=True, text=True, timeout=600,
        )
        os.unlink(list_path)
        if result.returncode != 0:
            return f"ffmpeg error: {result.stderr.strip()}"
        return f"Concatenated {len(paths)} audio files -> {out}"
    except subprocess.TimeoutExpired:
        if os.path.exists(list_path):
            os.unlink(list_path)
        return "Error: ffmpeg timed out"
    except Exception as e:
        if os.path.exists(list_path):
            os.unlink(list_path)
        return f"Error concatenating audio: {e}"


@tool(description="Mix/overlay two audio tracks together. The overlay audio is looped if shorter than base.")
def audio_overlay(base: str, overlay: str, out: str = "", volume_ratio: float = 0.5) -> str:
    if not _FFMPEG:
        return _no_ffmpeg()
    err = _validate_path(base)
    if err:
        return err
    err = _validate_path(overlay)
    if err:
        return err
    if not os.path.isfile(base):
        return f"Error: base file not found: {base}"
    if not os.path.isfile(overlay):
        return f"Error: overlay file not found: {overlay}"
    if not out:
        out = _resolve_out(base, "overlayed")
    _ensure_parent(out)
    try:
        result = subprocess.run(
            [_FFMPEG, "-i", base, "-i", overlay,
             "-filter_complex",
             f"[1:a]volume={volume_ratio}[a1];[0:a][a1]amix=inputs=2:duration=first:dropout_transition=2",
             "-y", out],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            return f"ffmpeg error: {result.stderr.strip()}"
        return f"Overlayed {overlay} onto {base} (volume_ratio={volume_ratio}) -> {out}"
    except subprocess.TimeoutExpired:
        return "Error: ffmpeg timed out"
    except Exception as e:
        return f"Error overlaying audio: {e}"


@tool(description="Change the sample rate of an audio file")
def audio_resample(path: str, sample_rate: int, out: str = "") -> str:
    if not _FFMPEG:
        return _no_ffmpeg()
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    if not out:
        out = _resolve_out(path, f"{sample_rate}hz")
    _ensure_parent(out)
    try:
        result = subprocess.run(
            [_FFMPEG, "-i", path, "-ar", str(sample_rate), "-y", out],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            return f"ffmpeg error: {result.stderr.strip()}"
        return f"Resampled {path} to {sample_rate} Hz -> {out}"
    except subprocess.TimeoutExpired:
        return "Error: ffmpeg timed out"
    except Exception as e:
        return f"Error resampling audio: {e}"


@tool(description="Change the volume of an audio file by a factor (0.0 = silence, 1.0 = original, 2.0 = double)")
def audio_change_volume(path: str, factor: float, out: str = "") -> str:
    if not _FFMPEG:
        return _no_ffmpeg()
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    if not out:
        suffix = "silence" if factor == 0 else f"vol_{factor}"
        out = _resolve_out(path, suffix)
    _ensure_parent(out)
    try:
        result = subprocess.run(
            [_FFMPEG, "-i", path, "-filter:a", f"volume={factor}", "-y", out],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            return f"ffmpeg error: {result.stderr.strip()}"
        return f"Changed volume of {path} by factor {factor} -> {out}"
    except subprocess.TimeoutExpired:
        return "Error: ffmpeg timed out"
    except Exception as e:
        return f"Error changing volume: {e}"


@tool(description="Change playback speed without changing pitch (0.5 = half speed, 2.0 = double)")
def audio_change_speed(path: str, rate: float, out: str = "") -> str:
    if not _FFMPEG:
        return _no_ffmpeg()
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    if not out:
        out = _resolve_out(path, f"speed_{rate}")
    _ensure_parent(out)
    try:
        result = subprocess.run(
            [_FFMPEG, "-i", path, "-filter:a", f"atempo={rate}", "-y", out],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            return f"ffmpeg error: {result.stderr.strip()}"
        return f"Changed speed of {path} by factor {rate} -> {out}"
    except subprocess.TimeoutExpired:
        return "Error: ffmpeg timed out"
    except Exception as e:
        return f"Error changing speed: {e}"


@tool(description="Reverse an audio file (plays backwards)")
def audio_reverse(path: str, out: str = "") -> str:
    if not _FFMPEG:
        return _no_ffmpeg()
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    if not out:
        out = _resolve_out(path, "reversed")
    _ensure_parent(out)
    try:
        result = subprocess.run(
            [_FFMPEG, "-i", path, "-filter:a", "areverse", "-y", out],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            return f"ffmpeg error: {result.stderr.strip()}"
        return f"Reversed {path} -> {out}"
    except subprocess.TimeoutExpired:
        return "Error: ffmpeg timed out"
    except Exception as e:
        return f"Error reversing audio: {e}"


@tool(description="Split a stereo audio file into separate left and right mono channels")
def audio_split_channels(path: str, out_dir: str = "") -> str:
    if not _FFMPEG:
        return _no_ffmpeg()
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    base, _ = os.path.splitext(os.path.basename(path))
    if not out_dir:
        out_dir = os.path.dirname(path) or "."
    _ensure_parent(out_dir)
    left_out = os.path.join(out_dir, f"{base}_left.wav")
    right_out = os.path.join(out_dir, f"{base}_right.wav")
    try:
        result = subprocess.run(
            [_FFMPEG, "-i", path,
             "-map_channel", "0.0.0", "-y", left_out,
             "-map_channel", "0.0.1", "-y", right_out],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            return f"ffmpeg error: {result.stderr.strip()}"
        return f"Split {path} -> {left_out}, {right_out}"
    except subprocess.TimeoutExpired:
        return "Error: ffmpeg timed out"
    except Exception as e:
        return f"Error splitting channels: {e}"


@tool(description="Detect silent segments in an audio file. Requires pydub (pip install pydub)")
def audio_silence_detect(path: str, silence_thresh: int = -40, min_silence_len: int = 500) -> str:
    if not HAS_PYDUB:
        return "Error: pydub is required. Run: pip install pydub"
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    try:
        audio = pydub.AudioSegment.from_file(path)
        silences = pydub.silence.detect_silence(
            audio,
            silence_thresh=silence_thresh,
            min_silence_len=min_silence_len,
        )
        segments = []
        for i, (start_ms, end_ms) in enumerate(silences):
            segments.append({
                "segment": i + 1,
                "start_sec": round(start_ms / 1000, 2),
                "end_sec": round(end_ms / 1000, 2),
                "duration_sec": round((end_ms - start_ms) / 1000, 2),
            })
        summary = {
            "total_silent_segments": len(segments),
            "silence_thresh_db": silence_thresh,
            "min_silence_len_ms": min_silence_len,
            "segments": segments,
        }
        return json.dumps(summary, indent=2)
    except Exception as e:
        return f"Error detecting silence: {e}"


@tool(description="Get amplitude statistics of an audio file (min, max, mean, RMS)")
def audio_histogram(path: str) -> str:
    if not _FFMPEG:
        return _no_ffmpeg()
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    with tempfile.TemporaryDirectory() as tmp:
        wav_out = os.path.join(tmp, "temp.wav")
        try:
            subprocess.run(
                [_FFMPEG, "-i", path, "-acodec", "pcm_s16le", "-f", "wav", "-y", wav_out],
                capture_output=True, text=True, timeout=120,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            return f"ffmpeg error: {e.stderr.strip()}"
        except subprocess.TimeoutExpired:
            return "Error: ffmpeg timed out"
        try:
            import struct
            with open(wav_out, "rb") as f:
                data = f.read()
            header_size = 44
            samples = struct.unpack(f"<{((len(data) - header_size) // 2)}h", data[header_size:])
            if not samples:
                return "Error: no audio samples found"
            total = len(samples)
            mean_val = sum(samples) / total
            rms = (sum(s * s for s in samples) / total) ** 0.5
            min_val = min(samples)
            max_val = max(samples)
            summary = {
                "total_samples": total,
                "min_amplitude": int(min_val),
                "max_amplitude": int(max_val),
                "mean_amplitude": round(mean_val, 2),
                "rms_amplitude": round(rms, 2),
                "bit_depth": 16,
            }
            return json.dumps(summary, indent=2)
        except Exception as e:
            return f"Error analyzing audio: {e}"
