from pathlib import Path

from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


def text_pdf(path: Path, *, pages: int = 1) -> Path:
    document = canvas.Canvas(str(path), pagesize=letter)
    for page in range(1, pages + 1):
        document.drawString(72, 720, f"Synthetic document page {page}")
        document.drawString(72, 690, "This fixture has a deterministic text layer.")
        document.showPage()
    document.save()
    return path


def image_only_pdf(path: Path) -> Path:
    image_path = path.with_suffix(".png")
    image = Image.new("RGB", (400, 200), "white")
    drawing = ImageDraw.Draw(image)
    drawing.rectangle((20, 20, 380, 180), outline="black", width=3)
    drawing.line((40, 150, 120, 80, 220, 120, 350, 40), fill="black", width=4)
    image.save(image_path)
    document = canvas.Canvas(str(path), pagesize=letter)
    document.drawImage(ImageReader(str(image_path)), 72, 500, width=400, height=200)
    document.showPage()
    document.save()
    image_path.unlink()
    return path


def encrypted_pdf(path: Path) -> Path:
    document = canvas.Canvas(str(path), pagesize=letter)
    document.setEncrypt("fixture-password")
    document.drawString(72, 720, "Encrypted synthetic fixture")
    document.showPage()
    document.save()
    return path


def corrupt_pdf(path: Path) -> Path:
    path.write_bytes(b"%PDF-1.4\nthis is deliberately truncated")
    return path
