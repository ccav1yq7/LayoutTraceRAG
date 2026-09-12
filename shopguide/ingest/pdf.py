"""Deterministic PDF rasterization and layout text; no OCR/model inference."""

import io
import math

import pdfplumber
import pypdfium2 as pdfium

from ..coordinates import PageTransform


def render_pdf(source: bytes, index: int, scale: float = 1.0):
    if not source.startswith(b"%PDF-") or not 0 < scale <= 4:
        raise ValueError("invalid PDF or render scale")
    with pdfium.PdfDocument(source) as doc:
        if not 0 <= index < len(doc):
            raise ValueError("page index outside PDF")
        page = doc[index]
        try:
            width, height = page.get_size()
            if math.ceil(width * scale) * math.ceil(height * scale) > 25_000_000:
                raise ValueError("render pixel limit")
            bitmap = page.render(scale=scale)
            try:
                image = bitmap.to_pil().copy().convert("RGB")
            finally:
                bitmap.close()
        finally:
            page.close()
    if image.width * image.height > 25_000_000:
        raise ValueError("render pixel limit")
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def pdf_pages(source: bytes):
    with (
        pdfplumber.open(io.BytesIO(source)) as document,
        pdfium.PdfDocument(source) as raster,
    ):
        for index, page in enumerate(document.pages):
            # pdfplumber's default is MediaBox; rasterization uses the CropBox.
            crop = page.cropbox
            media = page.mediabox
            visible = (
                max(crop[0], media[0]),
                max(crop[1], media[1]),
                min(crop[2], media[2]),
                min(crop[3], media[3]),
            )
            raster_page = raster[index]
            try:
                left, bottom, right, top = raster_page.get_bbox()
                source_box = (float(left), float(bottom), float(right), float(top))
                transform = PageTransform(source_box, raster_page.get_rotation())
                matrix = transform.matrix()
                width, height = raster_page.get_size()
                pixel_matrix = (
                    tuple(v * math.ceil(width) for v in matrix[:3])
                    + tuple(v * math.ceil(height) for v in matrix[3:6])
                    + matrix[6:]
                )
            finally:
                raster_page.close()
            yield (
                index,
                page.within_bbox(visible).extract_text() or "",
                {
                    "mediabox": list(page.mediabox),
                    "cropbox": list(page.cropbox),
                    "rotation": page.rotation,
                    "width": page.width,
                    "height": page.height,
                    "source_pdf_box": source_box,
                    "source_to_pixel_matrix": pixel_matrix,
                },
            )
