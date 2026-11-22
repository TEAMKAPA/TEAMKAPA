"""Obraz strony dla VLM: zmniejszenie do ``image_max_side_px`` i numery elementów (tryb 1).

Numery (część ``nnn`` ID ``cv-p{n}-{nnn}``) rysowane są czerwonymi ramkami na kopii obrazu, żeby
model jednoznacznie wiązał elementy z listy z miejscami na stronie (set-of-marks). Obraz jest
kodowany jako PNG w pamięci; nic nie jest zapisywane na dysk.
"""

import io
import re
from collections.abc import Sequence

from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont

from backend.worker.vision.models import ImageArray, VisualElement
from backend.worker.vision.vlm.client import VlmImage

MARK_COLOR = (220, 0, 0)
NUMBER = re.compile(r"-(\d+)$")


def _scaled(image: ImageArray, max_side: int) -> PILImage.Image:
    page = PILImage.fromarray(image).convert("RGB")
    scale = min(1.0, max_side / max(page.size))
    if scale < 1.0:
        size = (max(1, round(page.width * scale)), max(1, round(page.height * scale)))
        page = page.resize(size, PILImage.Resampling.LANCZOS)
    return page


def page_image(
    image: ImageArray,
    max_side: int,
    marks: Sequence[VisualElement] = (),
) -> VlmImage:
    page = _scaled(image, max_side)
    if marks:
        draw = ImageDraw.Draw(page)
        font = ImageFont.load_default(size=max(10, page.width // 90))
        for element in marks:
            b = element.bbox
            box = (
                round(b.x0 * page.width),
                round(b.y0 * page.height),
                max(round(b.x1 * page.width) - 1, round(b.x0 * page.width)),
                max(round(b.y1 * page.height) - 1, round(b.y0 * page.height)),
            )
            draw.rectangle(box, outline=MARK_COLOR, width=1)
            match = NUMBER.search(element.id)
            label = str(int(match.group(1))) if match else element.id
            left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
            x, y = box[0], max(0, box[1] - (bottom - top) - 2)
            draw.rectangle((x, y, x + right - left + 2, y + bottom - top + 2), fill=MARK_COLOR)
            draw.text((x + 1 - left, y + 1 - top), label, font=font, fill=(255, 255, 255))
    buffer = io.BytesIO()
    page.save(buffer, format="PNG", optimize=False)
    return VlmImage(data=buffer.getvalue(), mime="image/png")
