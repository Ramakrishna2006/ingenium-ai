"""Resume text extraction and rule-based information extraction.

PDF: pypdf. DOCX: python-docx. Scanned PDFs (no text layer) are marked
``needs_ocr``; when ENABLE_OCR=true and pytesseract + pdf2image + Tesseract are
installed, OCR is attempted. Extraction is a starting point the candidate and
recruiters can correct — it is not treated as verified fact.
"""
from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field

from app.services.skills import extract_skills_from_text

log = logging.getLogger(__name__)
MAX_TEXT_CHARS = 60_000


@dataclass
class ParsedResume:
    status: str
    text: str = ""
    error: str | None = None
    extracted: dict = field(default_factory=dict)


def extract_text(data: bytes, extension: str, enable_ocr: bool = False) -> tuple[str, str, str | None]:
    """Return (text, status, error)."""
    try:
        if extension == "pdf":
            text = _pdf_text(data)
            if len(text.strip()) < 40:
                if enable_ocr:
                    ocr = _ocr_pdf(data)
                    if ocr:
                        return ocr[:MAX_TEXT_CHARS], "parsed", None
                return text, "needs_ocr", "No text layer found (scanned PDF). OCR is not enabled."
            return text[:MAX_TEXT_CHARS], "parsed", None
        if extension == "docx":
            return _docx_text(data)[:MAX_TEXT_CHARS], "parsed", None
        return "", "failed", "Unsupported file type."
    except Exception as exc:  # parsing libraries raise many exception types
        log.warning("Resume text extraction failed: %s", type(exc).__name__)
        return "", "failed", "The document could not be read. Please upload a different file."


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:  # pragma: no cover
            raise ValueError("encrypted") from exc
    pages = []
    for page in reader.pages[:30]:
        pages.append(page.extract_text() or "")
    return "\n".join(pages)


def _docx_text(data: bytes) -> str:
    import docx

    document = docx.Document(io.BytesIO(data))
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(parts)


def _ocr_pdf(data: bytes) -> str | None:  # pragma: no cover - optional dependency
    try:
        import pytesseract
        from pdf2image import convert_from_bytes
    except ImportError:
        log.info("OCR requested but pytesseract/pdf2image are not installed")
        return None
    try:
        images = convert_from_bytes(data, first_page=1, last_page=5)
        return "\n".join(pytesseract.image_to_string(img) for img in images)
    except Exception as exc:
        log.warning("OCR failed: %s", type(exc).__name__)
        return None


_EDU_KEYWORDS = re.compile(
    r"\b(B\.?\s?Tech|B\.?\s?E\.?|B\.?\s?Sc|M\.?\s?Sc|M\.?\s?Tech|MBA|BBA|BCA|MCA|Ph\.?\s?D|Bachelor(?:'s)?|Master(?:'s)?|"
    r"Diploma|Degree)\b[^\n]{0,90}",
    re.IGNORECASE,
)
_CERT_KEYWORDS = re.compile(r"[^\n]{0,60}\b(Certified|Certification|Certificate)\b[^\n]{0,60}", re.IGNORECASE)
_YEARS = re.compile(r"(\d{1,2}(?:\.\d)?)\s*\+?\s*(?:years|yrs)(?:\s+of)?\s+(?:\w+\s+){0,3}experience", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?:\+?\d[\s-]?){9,14}\d")


def extract_profile(text: str) -> dict:
    """Rule-based extraction of skills, education, certifications and years of experience."""
    text = text or ""
    education = []
    for m in _EDU_KEYWORDS.finditer(text):
        line = re.sub(r"\s+", " ", m.group(0)).strip(" ,;-")
        if line and line not in education:
            education.append(line[:120])
    certifications = []
    for m in _CERT_KEYWORDS.finditer(text):
        line = re.sub(r"\s+", " ", m.group(0)).strip(" ,;-•")
        if line and line not in certifications:
            certifications.append(line[:120])
    years = [float(m.group(1)) for m in _YEARS.finditer(text)]
    return {
        "skills": extract_skills_from_text(text),
        "education": education[:8],
        "certifications": certifications[:8],
        "years_experience": max(years) if years else None,
    }


def parse_resume(data: bytes, extension: str, enable_ocr: bool = False) -> ParsedResume:
    text, status, error = extract_text(data, extension, enable_ocr)
    extracted = extract_profile(text) if status == "parsed" else {}
    return ParsedResume(status=status, text=text, error=error, extracted=extracted)


def redact_pii(text: str) -> str:
    """Remove obvious contact details before text is sent to an AI model."""
    text = _EMAIL.sub("[email]", text or "")
    return _PHONE.sub("[phone]", text)
