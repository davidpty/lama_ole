"""Video processing tools for lama_ole — requires ffmpeg/ffprobe."""

import json
import os
import shutil
import subprocess

from tool_base import tool
from tools_security.validate_path import validate_path as _validate_path


_FFMPEG = shutil.which("ffmpeg")
_FFPROBE = shutil.which("ffprobe")


def _no_ffmpeg():
    return "Error: ffmpeg is not installed (required for video tools)"


def _ensure_parent(path):
    parent = os.path.dirname(path)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)


@tool(description="Get metadata about a video file (duration, codec, resolution, fps, bitrate)")
def video_info(path: str) -> str:
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
            if s.get("codec_type") == "video":
                info["video_codec"] = s.get("codec_name", "unknown")
                info["resolution"] = f"{s.get('width', '?')}x{s.get('height', '?')}"
                info["fps"] = s.get("r_frame_rate", "unknown")
                break
        return json.dumps(info, indent=2)
    except subprocess.TimeoutExpired:
        return "Error: ffprobe timed out"
    except json.JSONDecodeError:
        return "Error: unable to parse ffprobe output"
    except Exception as e:
        return f"Error reading video info: {e}"


@tool(description="Extract frames from a video at a given frame rate")
def video_extract_frames(path: str, out_dir: str, fps: float = 1) -> str:
    if not _FFMPEG:
        return _no_ffmpeg()
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    _ensure_parent(out_dir)
    try:
        result = subprocess.run(
            [_FFMPEG, "-i", path, "-vf", f"fps={fps}", os.path.join(out_dir, "frame_%04d.png")],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            return f"ffmpeg error: {result.stderr.strip()}"
        frames = [f for f in os.listdir(out_dir) if f.startswith("frame_")]
        return f"Extracted {len(frames)} frames at {fps} fps to {out_dir}/"
    except subprocess.TimeoutExpired:
        return "Error: ffmpeg timed out"
    except Exception as e:
        return f"Error extracting frames: {e}"


@tool(description="Trim a segment from a video (copy codec, no re-encode)")
def video_trim(path: str, start: float, duration: float, out: str) -> str:
    if not _FFMPEG:
        return _no_ffmpeg()
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    if not out:
        return "Error: 'out' path is required"
    _ensure_parent(out)
    try:
        result = subprocess.run(
            [_FFMPEG, "-ss", str(start), "-i", path, "-t", str(duration),
             "-c", "copy", "-y", out],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            return f"ffmpeg error: {result.stderr.strip()}"
        return f"Trimmed {path} ({start}s, dur={duration}s) -> {out}"
    except subprocess.TimeoutExpired:
        return "Error: ffmpeg timed out"
    except Exception as e:
        return f"Error trimming video: {e}"


@tool(description="Convert a video to a different container format (mp4, webm, avi, mkv)")
def video_convert(path: str, fmt: str, out: str) -> str:
    if not _FFMPEG:
        return _no_ffmpeg()
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    if not out:
        return "Error: 'out' path is required"
    _ensure_parent(out)
    try:
        result = subprocess.run(
            [_FFMPEG, "-i", path, "-y", out],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            return f"ffmpeg error: {result.stderr.strip()}"
        return f"Converted {path} to {fmt} -> {out}"
    except subprocess.TimeoutExpired:
        return "Error: ffmpeg timed out"
    except Exception as e:
        return f"Error converting video: {e}"


@tool(description="Resize/scale a video to the given resolution")
def video_resize(path: str, width: int, height: int, out: str) -> str:
    if not _FFMPEG:
        return _no_ffmpeg()
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    if not out:
        return "Error: 'out' path is required"
    _ensure_parent(out)
    try:
        result = subprocess.run(
            [_FFMPEG, "-i", path, "-vf", f"scale={width}:{height}", "-y", out],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            return f"ffmpeg error: {result.stderr.strip()}"
        return f"Resized {path} to {width}x{height} -> {out}"
    except subprocess.TimeoutExpired:
        return "Error: ffmpeg timed out"
    except Exception as e:
        return f"Error resizing video: {e}"


@tool(description="Extract a single frame from a video at a given time as a JPEG image")
def video_thumbnail(path: str, time: float = 0, out: str = "") -> str:
    if not _FFMPEG:
        return _no_ffmpeg()
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    if not out:
        base, _ = os.path.splitext(path)
        out = f"{base}_thumb.jpg"
    _ensure_parent(out)
    try:
        result = subprocess.run(
            [_FFMPEG, "-ss", str(time), "-i", path, "-vframes", "1", "-q:v", "2", "-y", out],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return f"ffmpeg error: {result.stderr.strip()}"
        return f"Extracted thumbnail at {time}s from {path} -> {out}"
    except subprocess.TimeoutExpired:
        return "Error: ffmpeg timed out"
    except Exception as e:
        return f"Error extracting video thumbnail: {e}"


@tool(description="Extract audio from a video file to a separate audio file")
def video_extract_audio(path: str, fmt: str = "mp3", out: str = "") -> str:
    if not _FFMPEG:
        return _no_ffmpeg()
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    if not out:
        base, _ = os.path.splitext(path)
        out = f"{base}_audio.{fmt}"
    _ensure_parent(out)
    try:
        acodec = "libmp3lame" if fmt == "mp3" else "copy"
        result = subprocess.run(
            [_FFMPEG, "-i", path, "-vn", "-acodec", acodec, "-y", out],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            return f"ffmpeg error: {result.stderr.strip()}"
        return f"Extracted audio from {path} -> {out}"
    except subprocess.TimeoutExpired:
        return "Error: ffmpeg timed out"
    except Exception as e:
        return f"Error extracting audio: {e}"


@tool(description="Concatenate (join) multiple video files into one. Files must have the same codec and resolution.")
def video_concatenate(paths: list, out: str) -> str:
    if not _FFMPEG:
        return _no_ffmpeg()
    if len(paths) < 2:
        return "Error: need at least 2 video paths"
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
        return f"Concatenated {len(paths)} videos -> {out}"
    except subprocess.TimeoutExpired:
        if os.path.exists(list_path):
            os.unlink(list_path)
        return "Error: ffmpeg timed out"
    except Exception as e:
        if os.path.exists(list_path):
            os.unlink(list_path)
        return f"Error concatenating videos: {e}"
