"""Choose export encodings for image crops from visual OCR."""
import io
import os
import re

from PIL import Image, ImageOps

from .tune import JPEG_QUALITY, JPEG_MAX_RATIO


OCR_CROP_NAME = re.compile(r"img_p[0-9]{4,}_[0-9]+_[0-9]+\.png")


def _looks_like_ink(tile, dark, threshold):
    """Thin strokes have repeated edges in both axes."""
    mask = tile.point([int((v < threshold) == dark) for v in range(256)]).tobytes()
    w = tile.width
    rows = [mask[i:i + w] for i in range(0, len(mask), w)]
    across = sum(sum(a != b for a, b in zip(row, row[1:])) for row in rows)
    down = sum(sum(a != b for a, b in zip(upper, lower))
               for upper, lower in zip(rows, rows[1:]))
    return min(across, down) >= 14 and across + down >= 48


def _looks_photographic(image):
    sample = image.convert("L")
    sample.thumbnail((1280, 1280), getattr(Image, "Resampling", Image).BOX)
    w, h = sample.size
    columns, rows = w // 40, h // 24
    if min(columns, rows) < 2:
        return False
    hist = sample.histogram()
    # Large patches of a single tone are common in diagrams and documents.
    if max(sum(hist[i:i + 8]) for i in range(0, 256, 4)) >= .30 * w * h:
        return False
    varied = 0
    for row in range(rows):
        ink_count = 0
        previous_ink = False
        for column in range(columns):
            tile = sample.crop((column * w // columns, row * h // rows,
                                (column + 1) * w // columns, (row + 1) * h // rows))
            hist = tile.histogram()
            size = tile.width * tile.height
            tone = max(range(0, 256, 8), key=lambda i: sum(hist[i:i + 8]))
            flat = sum(hist[max(0, tone - 8):tone + 16]) >= .70 * size
            varied += not flat
            ink = False
            if flat:
                below = sum(hist[:max(0, tone - 32)])
                above = sum(hist[tone + 40:])
                if .009 * size <= max(below, above) <= .22 * size:
                    dark = below >= above
                    ink = _looks_like_ink(tile, dark, tone - 32 if dark else tone + 40)
                    # Even a single short label can be clear against paper
                    # white or a black panel.
                    if (ink and sum(hist[tone:tone + 8]) >= .75 * size
                            and ((dark and tone >= 232) or (not dark and tone < 24))):
                        return False
            ink_count += ink
            if ink_count >= 3 or (ink and previous_ink):
                return False
            previous_ink = ink
    return varied > .40 * columns * rows


def _encode(image, fmt, **options):
    buf = io.BytesIO()
    image.save(buf, format=fmt, **options)
    return buf.getvalue()


def _png_image(image):
    if image.mode == "CMYK":
        profile = image.info.get("icc_profile")
        if profile:
            try:
                from PIL import ImageCms
            except ImportError:
                ImageCms = None
            if ImageCms is not None:
                try:
                    return ImageCms.profileToProfile(
                        image, io.BytesIO(profile), ImageCms.createProfile("sRGB"),
                        outputMode="RGB")
                except (ImageCms.PyCMSError, OSError, ValueError):
                    pass
        rgb = image.convert("RGB")
        rgb.info.pop("icc_profile", None)
        return rgb
    if image.mode == "I;16L":
        return image.convert("I;16")
    if image.mode in ("RGBX", "YCbCr", "HSV"):
        return image.convert("RGB")
    if image.mode == "PA":
        return image.convert("RGBA")
    return image


def _raster(raw, mode):
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if getattr(source, "n_frames", 1) != 1:
                return None, raw
            if mode == "auto" and source.format == "JPEG":
                return "jpg", raw
            if mode == "png" and source.format == "PNG":
                return "png", raw
            image = ImageOps.exif_transpose(source)
            png = raw if source.format == "PNG" else _encode(_png_image(image), "PNG")
            if (mode == "png" or "transparency" in image.info
                    or image.mode not in ("L", "LA", "RGB", "RGBA", "P")
                    or ("A" in image.getbands() and image.getchannel("A").getextrema()[0] < 255)
                    or not _looks_photographic(image)):
                return "png", png
            photo = image
            if image.mode == "LA":
                photo = image.convert("L")
            elif image.mode in ("P", "RGBA"):
                photo = image.convert("RGB")
            jpg = _encode(photo, "JPEG", quality=JPEG_QUALITY, subsampling=0,
                          optimize=True, icc_profile=photo.info.get("icc_profile"),
                          dpi=photo.info.get("dpi", (0, 0)))
            return ("jpg", jpg) if len(jpg) <= JPEG_MAX_RATIO * len(png) else ("png", png)
    except (OSError, ValueError, Image.DecompressionBombError):
        return None, raw


def free_name(name, taken):
    """An output path distinct even on case-insensitive filesystems."""
    if name.casefold() not in taken:
        return name
    stem, ext = os.path.splitext(name)
    n = 2
    while (candidate := f"{stem}_{n}{ext}").casefold() in taken:
        n += 1
    return candidate


def _ocr_crop(block):
    if "ocr_crop" in block:
        return block["ocr_crop"]
    # Older visual PDF caches have both the OCR block ID and the crop name.
    return (block.get("_page") and re.fullmatch(r"s[0-9]+\.img[0-9]+", block["id"])
            and OCR_CROP_NAME.fullmatch(block["text"]))


def prepare(blocks, images, mode):
    """Convert OCR crops and remap their file names for the writers."""
    names = dict.fromkeys(b["text"] for b in blocks
                          if b["kind"] == "image" and not b.get("drop") and _ocr_crop(b))
    if not names:
        return blocks, images
    taken = {n.casefold() for n in images}
    out = dict(images)
    renamed = {}
    for name in names:
        if name not in images:
            continue
        ext, raw = _raster(images[name], mode)
        new_name = name
        if ext:
            stem, old_ext = os.path.splitext(name)
            compatible = (".jpg", ".jpeg") if ext == "jpg" else (".png",)
            if old_ext.lower() not in compatible:
                new_name = free_name(f"{stem}.{ext}", taken)
                taken.add(new_name.casefold())
                renamed[name] = new_name
        out[new_name] = raw
    if renamed:
        blocks = [dict(b, text=renamed[b["text"]])
                  if b["kind"] == "image" and b["text"] in renamed and _ocr_crop(b) else b
                  for b in blocks]
    return blocks, out
