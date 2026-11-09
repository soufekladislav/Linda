from __future__ import annotations

import io
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .config import settings

APP_ROOT = Path(__file__).resolve().parents[1]
UPLOAD_ROOT = (APP_ROOT / "storage" / "uploads").resolve()


def resolve_upload_path(stored: str, *, allow_scan: bool = True) -> Path | None:
    """Resolve legacy Linda media paths safely inside storage/uploads.

    Supports old relative paths, paths with uploads/storage prefixes and old
    absolute roots. As a final recovery step it looks for the basename in
    nested upload folders because older Linda versions moved generated assets
    between subdirectories without always rewriting DB rows.
    """
    if not stored:
        return None
    raw = unquote(str(stored).strip()).replace("\\", "/")
    if raw.startswith(("http://", "https://")):
        return None

    candidates: list[Path] = []
    try:
        direct = Path(raw)
        if direct.is_absolute():
            candidates.append(direct.resolve())
    except Exception:
        pass

    clean = raw.lstrip("/")
    # If an old absolute/rooted path contains storage/uploads, preserve only its safe suffix.
    marker = "storage/uploads/"
    if marker in clean:
        suffix = clean.split(marker, 1)[1]
        candidates.append((UPLOAD_ROOT / suffix).resolve())
    marker2 = "uploads/"
    if marker2 in clean and marker not in clean:
        suffix = clean.split(marker2, 1)[1]
        candidates.append((UPLOAD_ROOT / suffix).resolve())

    candidates.append((APP_ROOT / clean).resolve())
    if clean.startswith("storage/uploads/"):
        candidates.append((UPLOAD_ROOT / clean[len("storage/uploads/"):]).resolve())
    elif clean.startswith("uploads/"):
        candidates.append((UPLOAD_ROOT / clean[len("uploads/"):]).resolve())
    else:
        candidates.append((UPLOAD_ROOT / clean).resolve())

    basename = Path(clean).name
    if basename:
        candidates.append((UPLOAD_ROOT / basename).resolve())

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        try:
            candidate.relative_to(UPLOAD_ROOT)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate

    # Recovery for legacy directory moves. Keep the scan bounded to the uploads tree.
    if allow_scan and basename and UPLOAD_ROOT.exists():
        try:
            exact = next((p for p in UPLOAD_ROOT.rglob(basename) if p.is_file()), None)
            if exact:
                return exact.resolve()
            low = basename.lower()
            match = next((p for p in UPLOAD_ROOT.rglob("*") if p.is_file() and p.name.lower() == low), None)
            if match:
                return match.resolve()
        except Exception:
            pass
    return None


def _pil():
    try:
        from PIL import Image, ImageOps  # type: ignore
        return Image, ImageOps
    except Exception:
        return None, None


def pillow_available() -> bool:
    """Return whether Linda can validate/optimize image assets in this runtime."""
    Image, _ImageOps = _pil()
    return Image is not None


def require_pillow() -> None:
    """Fail before a paid image provider call when the local media runtime is incomplete."""
    if not pillow_available():
        raise RuntimeError(
            "Linda nemůže zpracovat obrázky, protože na serveru chybí Pillow. "
            "Nainstaluj requirements.txt do aktivního virtuálního prostředí a restartuj aplikaci."
        )


def validate_image_bytes(data: bytes, mime_hint: str = "") -> dict[str, Any]:
    """Strictly verify that *data* is a readable static raster image.

    Returns normalized metadata used by the queue before an image is persisted.
    Pillow is a runtime dependency of Linda, so an unreadable payload is treated as
    an actual job failure instead of being stored as an arbitrary .png/.jpg file.
    """
    if not data:
        raise RuntimeError("AI Engine nevytvořil žádná obrazová data.")
    Image, ImageOps = _pil()
    if Image is None:
        raise RuntimeError("Linda nemůže ověřit obrázek, protože na serveru chybí Pillow.")
    hinted = str(mime_hint or "").split(";", 1)[0].strip().lower()
    if hinted and hinted not in {"application/octet-stream", "binary/octet-stream"} and not hinted.startswith("image/"):
        raise RuntimeError(f"AI Engine vrátil neplatný MIME typ pro obrázek: {hinted}.")
    try:
        # verify() catches truncated/corrupt payloads without fully decoding them.
        with Image.open(io.BytesIO(data)) as probe:
            fmt = str(getattr(probe, "format", "") or "").upper()
            probe.verify()
        with Image.open(io.BytesIO(data)) as opened:
            if getattr(opened, "is_animated", False):
                raise RuntimeError("AI Engine vrátil animovaný soubor místo statického obrázku.")
            image = ImageOps.exif_transpose(opened)
            image.load()
            width, height = image.size
            if width < 1 or height < 1:
                raise RuntimeError("Vygenerovaný obrázek má neplatné rozměry.")
            fmt = str(getattr(opened, "format", "") or fmt or "").upper()
            mime_map = {"JPEG":"image/jpeg", "JPG":"image/jpeg", "PNG":"image/png", "WEBP":"image/webp", "BMP":"image/bmp", "TIFF":"image/tiff"}
            ext_map = {"JPEG":"jpg", "JPG":"jpg", "PNG":"png", "WEBP":"webp", "BMP":"bmp", "TIFF":"tiff"}
            actual_mime = mime_map.get(fmt)
            actual_ext = ext_map.get(fmt)
            if not actual_mime or not actual_ext:
                raise RuntimeError(f"Vygenerovaný soubor není podporovaný rastrový obrázek ({fmt or 'neznámý formát'}).")
            has_alpha = "A" in image.getbands() or (image.mode == "P" and "transparency" in image.info)
            has_transparency = False
            if has_alpha:
                try:
                    alpha_min, _alpha_max = image.convert("RGBA").getchannel("A").getextrema()
                    has_transparency = int(alpha_min) < 255
                except Exception:
                    has_transparency = True
            return {
                "mime_type": actual_mime,
                "format": actual_ext,
                "width": int(width),
                "height": int(height),
                "bytes": len(data),
                "has_transparency": bool(has_transparency),
            }
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Vygenerovaný soubor není čitelný obrázek ({type(exc).__name__}).") from exc


def optimize_image_bytes(data: bytes, filename_hint: str = "image.png") -> tuple[bytes, str, dict[str, Any]]:
    """Compress a raster image for Linda storage.

    Default is JPEG for broad social-network compatibility. PNG is retained for
    alpha/transparency. If Pillow is unavailable or the file is unsupported the
    original bytes are returned unchanged.
    """
    original_size = len(data)
    info: dict[str, Any] = {"original_bytes": original_size, "optimized": False}
    if not settings.media_optimize_images or not data:
        return data, Path(filename_hint).suffix.lstrip(".").lower() or "bin", info
    Image, ImageOps = _pil()
    if Image is None:
        info["reason"] = "pillow_unavailable"
        return data, Path(filename_hint).suffix.lstrip(".").lower() or "bin", info
    try:
        with Image.open(io.BytesIO(data)) as opened:
            fmt = str(getattr(opened, "format", "") or "").upper()
            if fmt in {"GIF", "SVG", "PDF"} or getattr(opened, "is_animated", False):
                info["reason"] = "unsupported_or_animated"
                return data, Path(filename_hint).suffix.lstrip(".").lower() or fmt.lower() or "bin", info
            image = ImageOps.exif_transpose(opened)
            image.load()
            width, height = image.size
            max_dim = max(640, int(settings.media_max_dimension))
            if max(width, height) > max_dim:
                ratio = max_dim / float(max(width, height))
                image = image.resize((max(1, round(width * ratio)), max(1, round(height * ratio))), Image.Resampling.LANCZOS)

            bands = image.getbands()
            # Do not keep PNG merely because the provider returned an RGBA container.
            # Many AI image APIs return an alpha channel that is fully opaque, which
            # used to leave 2–4 MB PNG files in Linda. Preserve PNG only when there is
            # actual transparency in the pixels.
            has_alpha_channel = "A" in bands or (image.mode == "P" and "transparency" in image.info)
            has_transparency = False
            if has_alpha_channel:
                try:
                    rgba = image.convert("RGBA")
                    alpha_min, alpha_max = rgba.getchannel("A").getextrema()
                    has_transparency = int(alpha_min) < 255
                except Exception:
                    has_transparency = True
            output = io.BytesIO()
            if has_transparency:
                if image.mode not in {"RGBA", "LA"}:
                    image = image.convert("RGBA")
                image.save(output, format="PNG", optimize=True, compress_level=9)
                ext = "png"
            else:
                if image.mode != "RGB":
                    image = image.convert("RGB")
                image.save(
                    output,
                    format="JPEG",
                    quality=max(60, min(95, int(settings.media_image_quality))),
                    optimize=True,
                    progressive=True,
                )
                ext = "jpg"
            optimized = output.getvalue()
            # Avoid replacing an already-small file with something bigger.
            if len(optimized) >= original_size and original_size > 0:
                info.update({"reason": "optimized_not_smaller", "width": width, "height": height})
                return data, Path(filename_hint).suffix.lstrip(".").lower() or ext, info
            info.update({
                "optimized": True,
                "optimized_bytes": len(optimized),
                "saved_bytes": max(0, original_size - len(optimized)),
                "width": image.size[0],
                "height": image.size[1],
                "format": ext,
            })
            return optimized, ext, info
    except Exception as exc:
        info["reason"] = f"image_error:{type(exc).__name__}"
        return data, Path(filename_hint).suffix.lstrip(".").lower() or "bin", info


def thumbnail_path(media_id: int, source: Path) -> Path | None:
    Image, ImageOps = _pil()
    if Image is None or not source.is_file():
        return None
    cache = UPLOAD_ROOT / ".thumbs"
    cache.mkdir(parents=True, exist_ok=True)
    try:
        stamp = int(source.stat().st_mtime)
        target = cache / f"{media_id}-{stamp}.webp"
        if target.is_file():
            return target
        with Image.open(source) as opened:
            if getattr(opened, "is_animated", False):
                return None
            image = ImageOps.exif_transpose(opened)
            image.thumbnail((settings.media_thumbnail_dimension, settings.media_thumbnail_dimension), Image.Resampling.LANCZOS)
            if "A" in image.getbands():
                if image.mode != "RGBA": image = image.convert("RGBA")
            elif image.mode != "RGB":
                image = image.convert("RGB")
            image.save(target, "WEBP", quality=78, method=4)
        # remove stale versions for this media id
        for old in cache.glob(f"{media_id}-*.webp"):
            if old != target:
                try: old.unlink()
                except Exception: pass
        return target
    except Exception:
        return None


def file_mime(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}


def ffmpeg_available() -> bool:
    try:
        import shutil
        return bool(shutil.which("ffmpeg"))
    except Exception:
        return False


def optimize_video_path(source: Path) -> tuple[Path, dict[str, Any]]:
    """Best-effort video compression for user uploads.

    Uses ffmpeg when available. The source is preserved when transcoding fails or
    does not save at least ~5 %. Output is web/social-friendly H.264 MP4.
    """
    info: dict[str, Any] = {"optimized": False, "source": str(source)}
    if not settings.media_optimize_videos or not source.is_file() or source.suffix.lower() not in VIDEO_EXTS:
        info["reason"] = "disabled_or_not_video"
        return source, info
    try:
        import shutil
        import subprocess
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            info["reason"] = "ffmpeg_unavailable"
            return source, info
        original_size = source.stat().st_size
        target = source.with_name(source.stem + "-optimized.mp4")
        max_dim = max(720, int(settings.media_video_max_dimension))
        vf = f"scale=w='min({max_dim},iw)':h='min({max_dim},ih)':force_original_aspect_ratio=decrease:force_divisible_by=2"
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(max(20, min(36, int(settings.media_video_crf)))),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-c:a", "aac", "-b:a", "128k", str(target),
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=900)
        if not target.is_file() or target.stat().st_size <= 0:
            info["reason"] = "ffmpeg_no_output"
            return source, info
        new_size = target.stat().st_size
        if original_size and new_size >= int(original_size * 0.95):
            try: target.unlink()
            except Exception: pass
            info.update({"reason": "optimized_not_smaller", "original_bytes": original_size, "optimized_bytes": new_size})
            return source, info
        info.update({
            "optimized": True,
            "original_bytes": original_size,
            "optimized_bytes": new_size,
            "saved_bytes": max(0, original_size - new_size),
            "format": "mp4",
        })
        return target, info
    except Exception as exc:
        try:
            target = source.with_name(source.stem + "-optimized.mp4")
            if target.exists(): target.unlink()
        except Exception:
            pass
        info["reason"] = f"video_error:{type(exc).__name__}"
        return source, info
