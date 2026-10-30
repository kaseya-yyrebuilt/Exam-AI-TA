"""Optional local Tesseract hint; do not treat this as reliable handwriting recognition."""
import shutil
from PIL import Image


def available() -> bool:
    return shutil.which("tesseract") is not None


def ocr_image(image: Image.Image) -> str:
    if not available():
        return "[Tesseract executable not installed; OCR hint unavailable]"
    try:
        import pytesseract
        return pytesseract.image_to_string(image, config="--psm 6").strip()
    except Exception as error:
        return f"[Local Tesseract OCR failed: {error}]"
