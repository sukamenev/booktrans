#!/usr/bin/env python3
"""Check image conversion, matching references, and collision-free names."""
import base64
import io
from pathlib import Path
import random
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from PIL import Image, ImageDraw, JpegImagePlugin

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from booktrans import build, pictures


def main():
    rng = random.Random(1729)
    noise = Image.frombytes("RGB", (128, 96),
                            bytes(rng.randrange(256) for _ in range(128 * 96 * 3)))
    photo = noise.resize((512, 384))
    png = pictures._encode(photo, "PNG")
    ext, jpg = pictures._raster(png, "auto")
    assert ext == "jpg" and len(jpg) <= .8 * len(png)
    with Image.open(io.BytesIO(jpg)) as im:
        assert im.size == photo.size and JpegImagePlugin.get_sampling(im) == 0

    captioned = photo.copy()
    draw = ImageDraw.Draw(captioned)
    draw.rectangle((0, 320, 511, 383), fill="white")
    draw.text((12, 337), "Note 12", fill="black")
    alpha = photo.convert("RGBA")
    alpha.putpixel((5, 5), (10, 20, 30, 128))
    diagram = Image.new("RGB", photo.size, "white")
    ImageDraw.Draw(diagram).line((10, 10, 500, 300), fill="black", width=2)
    for image in (captioned, alpha, diagram):
        raw = pictures._encode(image, "PNG")
        assert pictures._raster(raw, "auto") == ("png", raw)
    assert pictures._raster(jpg, "auto") == ("jpg", jpg)

    legacy = "img_p0002_10_20.png"
    embedded = "img_p0003_30_40.png"
    blocks = [
        {"kind": "image", "id": "s01.img0001", "text": "chart.png", "ocr_crop": True},
        {"kind": "image", "id": "s01.img0002", "text": legacy, "_page": 2},
        {"kind": "image", "id": "s01.i0003", "text": embedded},
        {"kind": "image", "id": "s01.i0004", "text": "chart.jpg"},
        {"kind": "image", "id": "s01.i0005", "text": "cover.jpg"},
    ]
    images = {"chart.png": png, legacy: png, embedded: png,
              "chart.jpg": jpg, "cover.jpg": pictures._encode(diagram, "JPEG")}
    meta = {"title": "Pictures", "target_lang": "en"}
    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        epub = out / "images.epub"
        build.build_book(d, meta, blocks, jpg, str(epub), print, images=dict(images))
        with zipfile.ZipFile(epub) as z:
            assert z.read("OEBPS/img/chart.jpg") == jpg
            assert z.read("OEBPS/img/chart_2.jpg") == jpg
            assert z.read("OEBPS/img/" + legacy.replace(".png", ".jpg")) == jpg
            assert z.read("OEBPS/img/" + embedded) == png
            assert z.read("OEBPS/img/cover.jpg") == images["cover.jpg"]
            xhtml = b"".join(z.read(n) for n in z.namelist() if n.endswith(".xhtml"))
            assert b'src="img/chart_2.jpg"' in xhtml

        # The explicit PNG mode still preserves original source assets.
        fb2 = out / "images.fb2"
        build.build_book(d, meta, blocks, jpg, str(fb2), print, images=dict(images),
                         image_format="png")
        tree = ET.parse(fb2)
        assets = {el.get("id"): el for el in tree.findall("{*}binary")}
        assert set(assets) == set(images) | {"cover_2.jpg"}
        for name, data in (images | {"cover_2.jpg": jpg}).items():
            el = assets[name]
            assert el.get("content-type") == ("image/png" if name.endswith(".png") else "image/jpeg")
            assert base64.b64decode(el.text) == data
        refs = {el.get("{http://www.w3.org/1999/xlink}href")[1:]
                for el in tree.findall(".//{*}image")}
        assert refs == set(assets)
    assert [b["text"] for b in blocks] == list(images)
    print("Image export checks passed")


if __name__ == "__main__":
    main()
