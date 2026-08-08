"""Image processing tools for lama_ole — requires Pillow."""

import json
import os

from tool_base import tool
from tools_security.validate_path import validate_path as _validate_path

try:
    from PIL import Image, ImageFilter as PILFilter, ImageDraw, ImageFont
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False


if HAS_PILLOW:
    _FILTER_MAP = {
        "blur": PILFilter.BLUR,
        "sharpen": PILFilter.SHARPEN,
        "edge_enhance": PILFilter.EDGE_ENHANCE,
        "smooth": PILFilter.SMOOTH,
        "contour": PILFilter.CONTOUR,
        "emboss": PILFilter.EMBOSS,
    }
else:
    _FILTER_MAP = {}


def _no_pillow():
    return "Error: Pillow is not installed. Run: pip install Pillow"


def _resolve_out(path, suffix, ext=None):
    if ext is None:
        ext = os.path.splitext(path)[1]
    base, _ = os.path.splitext(path)
    return f"{base}_{suffix}{ext}"


def _ensure_parent(path):
    parent = os.path.dirname(path)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)


@tool(description="Get metadata about an image file (format, dimensions, mode, size)")
def image_info(path: str) -> str:
    if not HAS_PILLOW:
        return _no_pillow()
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    try:
        img = Image.open(path)
        info = {
            "path": os.path.abspath(path),
            "format": img.format or "unknown",
            "size": f"{img.width}x{img.height}",
            "mode": img.mode,
            "file_size": f"{os.path.getsize(path)} bytes",
        }
        img.close()
        return json.dumps(info, indent=2)
    except Exception as e:
        return f"Error reading image: {e}"


@tool(description="Resize an image to the given width and height")
def image_resize(path: str, width: int, height: int, out: str = "") -> str:
    if not HAS_PILLOW:
        return _no_pillow()
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    if not out:
        out = _resolve_out(path, f"{width}x{height}")
    try:
        img = Image.open(path)
        resized = img.resize((width, height), Image.LANCZOS)
        _ensure_parent(out)
        resized.save(out)
        return f"Resized {path} to {width}x{height} -> {out}"
    except Exception as e:
        return f"Error resizing image: {e}"


@tool(description="Convert an image to a different format (png, jpeg, gif, webp)")
def image_convert(path: str, fmt: str, out: str = "") -> str:
    if not HAS_PILLOW:
        return _no_pillow()
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    ext_map = {"png": ".png", "jpeg": ".jpg", "jpg": ".jpg", "gif": ".gif", "webp": ".webp"}
    ext = ext_map.get(fmt.lower())
    if not ext:
        return f"Error: unsupported format '{fmt}'. Use: png, jpeg, gif, webp"
    if not out:
        out = _resolve_out(path, "converted", ext=ext)
    try:
        img = Image.open(path)
        if fmt.lower() in ("jpeg", "jpg"):
            mode = img.mode
            if mode in ("RGBA", "P"):
                img = img.convert("RGB")
        _ensure_parent(out)
        fmt_upper = fmt.upper()
        if fmt_upper == "JPG":
            fmt_upper = "JPEG"
        img.save(out, format=fmt_upper)
        return f"Converted {path} to {fmt} -> {out}"
    except Exception as e:
        return f"Error converting image: {e}"


@tool(description="Crop a rectangular region from an image")
def image_crop(path: str, x: int, y: int, width: int, height: int, out: str = "") -> str:
    if not HAS_PILLOW:
        return _no_pillow()
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    if not out:
        out = _resolve_out(path, f"crop_{x}_{y}_{width}x{height}")
    try:
        img = Image.open(path)
        cropped = img.crop((x, y, x + width, y + height))
        _ensure_parent(out)
        cropped.save(out)
        return f"Cropped {path} to ({x},{y},{width}x{height}) -> {out}"
    except Exception as e:
        return f"Error cropping image: {e}"


@tool(description="Rotate an image by a given number of degrees")
def image_rotate(path: str, degrees: float, out: str = "") -> str:
    if not HAS_PILLOW:
        return _no_pillow()
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    if not out:
        out = _resolve_out(path, f"rotated_{int(degrees)}")
    try:
        img = Image.open(path)
        rotated = img.rotate(degrees, expand=True, resample=Image.BICUBIC)
        _ensure_parent(out)
        rotated.save(out)
        return f"Rotated {path} by {degrees} degrees -> {out}"
    except Exception as e:
        return f"Error rotating image: {e}"


@tool(description="Flip an image horizontally or vertically")
def image_flip(path: str, direction: str = "horizontal", out: str = "") -> str:
    if not HAS_PILLOW:
        return _no_pillow()
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    if direction not in ("horizontal", "vertical"):
        return "Error: direction must be 'horizontal' or 'vertical'"
    if not out:
        out = _resolve_out(path, f"flip_{direction}")
    try:
        img = Image.open(path)
        if direction == "horizontal":
            flipped = img.transpose(Image.FLIP_LEFT_RIGHT)
        else:
            flipped = img.transpose(Image.FLIP_TOP_BOTTOM)
        _ensure_parent(out)
        flipped.save(out)
        return f"Flipped {path} {direction}ly -> {out}"
    except Exception as e:
        return f"Error flipping image: {e}"


@tool(description="Apply a filter to an image: blur, sharpen, edge_enhance, smooth, contour, emboss")
def image_filter(path: str, filter_name: str, out: str = "") -> str:
    if not HAS_PILLOW:
        return _no_pillow()
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    pil_filter = _FILTER_MAP.get(filter_name.lower())
    if pil_filter is None:
        return f"Error: unknown filter '{filter_name}'. Options: {', '.join(_FILTER_MAP)}"
    if not out:
        out = _resolve_out(path, filter_name.lower())
    try:
        img = Image.open(path)
        filtered = img.filter(pil_filter)
        _ensure_parent(out)
        filtered.save(out)
        return f"Applied '{filter_name}' filter to {path} -> {out}"
    except Exception as e:
        return f"Error filtering image: {e}"


@tool(description="Convert an image to grayscale")
def image_grayscale(path: str, out: str = "") -> str:
    if not HAS_PILLOW:
        return _no_pillow()
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    if not out:
        out = _resolve_out(path, "grayscale")
    try:
        img = Image.open(path)
        gray = img.convert("L")
        _ensure_parent(out)
        gray.save(out)
        return f"Converted {path} to grayscale -> {out}"
    except Exception as e:
        return f"Error grayscaling image: {e}"


@tool(description="Create a square thumbnail of an image fitting within size x size")
def image_thumbnail(path: str, size: int, out: str = "") -> str:
    if not HAS_PILLOW:
        return _no_pillow()
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    if not out:
        out = _resolve_out(path, f"thumb_{size}")
    try:
        img = Image.open(path)
        img.thumbnail((size, size), Image.LANCZOS)
        _ensure_parent(out)
        img.save(out)
        return f"Created {img.width}x{img.height} thumbnail from {path} -> {out}"
    except Exception as e:
        return f"Error creating thumbnail: {e}"


@tool(description="Get the RGB histogram of an image (per-channel pixel counts)")
def image_histogram(path: str) -> str:
    if not HAS_PILLOW:
        return _no_pillow()
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    try:
        img = Image.open(path)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        hist = img.histogram()
        r = hist[0:256]
        g = hist[256:512]
        b = hist[512:768]
        total = sum(r)
        summary = {
            "channels": ["R", "G", "B"],
            "total_pixels": total,
            "mean": [
                round(sum(r) / total, 1),
                round(sum(g) / total, 1),
                round(sum(b) / total, 1),
            ],
            "min": [min(r), min(g), min(b)],
            "max": [max(r), max(g), max(b)],
        }
        img.close()
        return json.dumps(summary, indent=2)
    except Exception as e:
        return f"Error computing histogram: {e}"


@tool(description="Overlay one image on top of another at a given position")
def image_overlay(base_path: str, overlay_path: str, x: int = 0, y: int = 0, out: str = "") -> str:
    if not HAS_PILLOW:
        return _no_pillow()
    err = _validate_path(base_path)
    if err:
        return err
    err = _validate_path(overlay_path)
    if err:
        return err
    if not os.path.isfile(base_path):
        return f"Error: base file not found: {base_path}"
    if not os.path.isfile(overlay_path):
        return f"Error: overlay file not found: {overlay_path}"
    if not out:
        base, _ = os.path.splitext(base_path)
        out = f"{base}_overlayed{os.path.splitext(base_path)[1]}"
    try:
        base = Image.open(base_path).convert("RGBA")
        overlay_img = Image.open(overlay_path).convert("RGBA")
        result = base.copy()
        result.paste(overlay_img, (x, y), overlay_img)
        _ensure_parent(out)
        final = result.convert("RGB") if out.lower().endswith((".jpg", ".jpeg")) else result
        final.save(out)
        return f"Overlayed {overlay_path} onto {base_path} at ({x},{y}) -> {out}"
    except Exception as e:
        return f"Error overlaying image: {e}"


@tool(description="Add text to an image at the specified position")
def image_add_text(path: str, text: str, x: int = 0, y: int = 0, size: int = 24, out: str = "") -> str:
    if not HAS_PILLOW:
        return _no_pillow()
    err = _validate_path(path)
    if err:
        return err
    if not os.path.isfile(path):
        return f"Error: file not found: {path}"
    if not out:
        out = _resolve_out(path, "text")
    try:
        img = Image.open(path).convert("RGBA")
        txt = Image.new("RGBA", img.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(txt)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
        except (OSError, IOError):
            font = ImageFont.load_default()
        draw.text((x, y), text, fill=(255, 255, 255, 255), font=font)
        result = Image.alpha_composite(img, txt)
        _ensure_parent(out)
        final = result.convert("RGB") if out.lower().endswith((".jpg", ".jpeg")) else result
        final.save(out)
        return f"Added text to {path} at ({x},{y}) -> {out}"
    except Exception as e:
        return f"Error adding text to image: {e}"
