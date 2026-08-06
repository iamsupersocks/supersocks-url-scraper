"""Testable document extraction provider.

Local path (default):
  - Office/EPUB/CSV/RTF → anydoc
  - PDF → pdf-inspector classify+extract, then PyMuPDF compatibility fallback
  - No cloud OCR; scanned/image_based/mixed without text return partial with warnings
"""

from __future__ import annotations

import os
from typing import Any

from .detect import (
    ALL_DOCUMENT_FORMATS,
    DOCUMENT_FORMATS,
    detect_document_format,
    title_from_markdown,
)
from .models import DocumentContent, DocumentDependencyError, DocumentParseError

PDF_NEEDS_OCR = frozenset({"scanned", "image_based", "mixed"})
DEFAULT_MAX_PAGES = 50
NO_LOCAL_OCR_WARNING = "No local OCR configured; install a local OCR engine to extract text from scans"


def anydoc_available() -> bool:
    try:
        import anydoc  # noqa: F401
    except ImportError:
        return False
    return True


def pdf_inspector_available() -> bool:
    try:
        import pdf_inspector  # noqa: F401
    except ImportError:
        return False
    return True


def pymupdf_available() -> bool:
    try:
        import fitz  # noqa: F401
    except ImportError:
        return False
    return True


def extract_office_markdown(data: bytes, *, format_hint: str | None = None) -> DocumentContent:
    try:
        import anydoc  # type: ignore[import-not-found]
    except ImportError as exc:
        raise DocumentDependencyError(
            "firecrawl-anydoc (package extra 'documents') is required to extract office/document Markdown"
        ) from exc
    hint = format_hint if format_hint in ALL_DOCUMENT_FORMATS else None
    try:
        detected = hint or anydoc.format_from_bytes(data)
        if detected is None and hint is None:
            raise DocumentParseError(
                "Cannot detect document format; name it via URL extension, MIME, or Content-Disposition"
            )
        markdown = anydoc.to_markdown_bytes(data, detected) if detected else anydoc.to_markdown_bytes(data)
    except DocumentParseError:
        raise
    except Exception as exc:
        raise DocumentParseError(f"Cannot convert document: {exc}") from exc
    text = (markdown or "").strip()
    fmt = str(detected or hint or "unknown")
    return DocumentContent(title=title_from_markdown(text), text=text, format=fmt, method="anydoc")


def _normalize_pdf_type(value: object) -> str | None:
    if value is None:
        return None
    raw = str(value).strip().lower().replace("-", "_")
    aliases = {
        "textbased": "text_based",
        "text_based": "text_based",
        "scanned": "scanned",
        "imagebased": "image_based",
        "image_based": "image_based",
        "mixed": "mixed",
    }
    return aliases.get(raw, raw if raw in PDF_NEEDS_OCR | {"text_based"} else None)


def classify_and_extract_pdf_inspector(data: bytes, *, max_pages: int | None = None) -> DocumentContent:
    try:
        import pdf_inspector  # type: ignore[import-not-found]
    except ImportError as exc:
        raise DocumentDependencyError(
            "pdf-inspector (package extra 'documents') is required to classify/extract PDF text"
        ) from exc

    pages = None
    if max_pages is not None and max_pages > 0:
        # pdf-inspector pages are 0-indexed; cap by constructing a range when possible.
        pages = list(range(max(1, int(max_pages))))

    try:
        if pages is not None:
            result = pdf_inspector.process_pdf_bytes(data, pages=pages)
        else:
            result = pdf_inspector.process_pdf_bytes(data)
    except TypeError:
        # Older bindings may not accept pages=
        result = pdf_inspector.process_pdf_bytes(data)
    except Exception as exc:
        raise DocumentParseError(f"pdf-inspector failed: {exc}") from exc

    pdf_type = _normalize_pdf_type(getattr(result, "pdf_type", None))
    markdown = getattr(result, "markdown", None)
    text = (markdown or "").strip() if isinstance(markdown, str) else ""
    if not text:
        plain = getattr(result, "text", None)
        if isinstance(plain, str):
            text = plain.strip()
    title = getattr(result, "title", None)
    if isinstance(title, str):
        title = title.strip() or None
    else:
        title = title_from_markdown(text) if text else None
    page_count = getattr(result, "page_count", None)
    if not isinstance(page_count, int):
        page_count = None

    return DocumentContent(
        title=title,
        text=text,
        format="pdf",
        method="pdf-inspector",
        page_count=page_count,
        pdf_classification=pdf_type,
        ocr_used=False,
        ocr_provider=None,
    )


def extract_pdf_pymupdf(data: bytes, *, max_pages: int | None = None) -> DocumentContent:
    try:
        import fitz  # type: ignore[import-not-found]
    except ImportError as exc:
        raise DocumentDependencyError("PyMuPDF (package 'pymupdf') is required to extract PDF text") from exc
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise DocumentParseError(f"Cannot open PDF: {exc}") from exc
    try:
        meta = doc.metadata or {}
        title = (meta.get("title") or "").strip() or None
        parts: list[str] = []
        limit = doc.page_count
        if max_pages is not None and max_pages > 0:
            limit = min(limit, int(max_pages))
        for index in range(limit):
            try:
                parts.append(doc.load_page(index).get_text("text"))
            except Exception:
                continue
        text = "\n".join(p.strip() for p in parts if p and p.strip())
        return DocumentContent(
            title=title,
            text=text,
            format="pdf",
            method="pymupdf",
            page_count=doc.page_count,
            pdf_classification=None,
            ocr_used=False,
            ocr_provider=None,
        )
    finally:
        doc.close()


def _best_local_pdf_content(
    inspector: DocumentContent | None,
    pymupdf_result: DocumentContent | None,
    warnings: list[str],
) -> DocumentContent | None:
    """Prefer the richest local text while preserving pdf-inspector classification."""
    classification = inspector.pdf_classification if inspector else None
    inspector_text = (inspector.text or "").strip() if inspector else ""
    pymupdf_text = (pymupdf_result.text or "").strip() if pymupdf_result else ""

    if pymupdf_text and (not inspector_text or len(pymupdf_text) > len(inspector_text)):
        return DocumentContent(
            title=pymupdf_result.title or (inspector.title if inspector else None),
            text=pymupdf_result.text,
            format="pdf",
            method="pymupdf",
            page_count=pymupdf_result.page_count or (inspector.page_count if inspector else None),
            pdf_classification=classification or pymupdf_result.pdf_classification,
            ocr_used=False,
            ocr_provider=None,
            warnings=tuple(warnings),
        )

    if inspector is not None:
        return DocumentContent(
            title=inspector.title,
            text=inspector.text,
            format="pdf",
            method=inspector.method,
            page_count=inspector.page_count,
            pdf_classification=classification,
            ocr_used=False,
            ocr_provider=None,
            warnings=tuple(warnings),
        )

    if pymupdf_result is not None:
        return DocumentContent(
            title=pymupdf_result.title,
            text=pymupdf_result.text,
            format="pdf",
            method=pymupdf_result.method,
            page_count=pymupdf_result.page_count,
            pdf_classification=pymupdf_result.pdf_classification,
            ocr_used=False,
            ocr_provider=None,
            warnings=tuple(warnings),
        )

    return None


def _local_pdf_warnings(local: DocumentContent) -> list[str]:
    extra: list[str] = []
    if not local.text.strip():
        if local.pdf_classification in PDF_NEEDS_OCR:
            extra.append(
                f"PDF classified as {local.pdf_classification}; no extractable text without OCR"
            )
        else:
            extra.append("PDF looks like a scan without OCR; no extractable text")
        extra.append(NO_LOCAL_OCR_WARNING)
    elif local.pdf_classification in PDF_NEEDS_OCR:
        extra.append(f"PDF classified as {local.pdf_classification}; using local text layer only")
        extra.append(NO_LOCAL_OCR_WARNING)
    return extra


class DocumentProvider:
    """Orchestrates local document engines (anydoc, pdf-inspector, PyMuPDF)."""

    def __init__(
        self,
        *,
        max_pages: int | None = None,
        environ: dict[str, str] | None = None,
    ) -> None:
        env = environ if environ is not None else os.environ
        try:
            default_pages = int(env.get("DOCUMENT_MAX_PAGES", str(DEFAULT_MAX_PAGES)))
        except (TypeError, ValueError):
            default_pages = DEFAULT_MAX_PAGES
        self.max_pages = max(1, int(max_pages if max_pages is not None else default_pages))

    def extract_document(self, data: bytes, *, format_hint: str | None = None) -> DocumentContent:
        hint = format_hint if format_hint in ALL_DOCUMENT_FORMATS else None
        if hint == "pdf" or (hint is None and data[:5] == b"%PDF-"):
            return self.extract_pdf(data)
        if hint in DOCUMENT_FORMATS or hint is None:
            return extract_office_markdown(data, format_hint=hint)
        return extract_office_markdown(data, format_hint=hint)

    def extract_pdf(
        self,
        data: bytes,
        *,
        source_url: str | None = None,
        final_url: str | None = None,
    ) -> DocumentContent:
        del source_url, final_url  # kept for API compatibility; no cloud OCR uses URLs
        warnings: list[str] = []
        inspector: DocumentContent | None = None
        pymupdf_result: DocumentContent | None = None
        local_error: Exception | None = None

        # 1) pdf-inspector classify + extract
        if pdf_inspector_available():
            try:
                inspector = classify_and_extract_pdf_inspector(data, max_pages=self.max_pages)
                if inspector.text.strip() and inspector.pdf_classification not in PDF_NEEDS_OCR:
                    return inspector
            except (DocumentDependencyError, DocumentParseError) as exc:
                local_error = exc
                warnings.append(f"pdf-inspector unavailable or failed: {exc}")
        else:
            warnings.append("pdf-inspector not installed; trying PyMuPDF")

        # 2) PyMuPDF compatibility fallback (best local text)
        try:
            pymupdf_result = extract_pdf_pymupdf(data, max_pages=self.max_pages)
        except DocumentDependencyError as exc:
            local_error = local_error or exc
            warnings.append(str(exc))
        except DocumentParseError as exc:
            local_error = local_error or exc
            warnings.append(str(exc))

        local = _best_local_pdf_content(inspector, pymupdf_result, warnings)

        if local is not None:
            return DocumentContent(
                title=local.title,
                text=local.text,
                format="pdf",
                method=local.method,
                page_count=local.page_count,
                pdf_classification=local.pdf_classification,
                ocr_used=False,
                ocr_provider=None,
                warnings=tuple(warnings) + tuple(_local_pdf_warnings(local)),
            )

        if isinstance(local_error, DocumentParseError):
            raise local_error
        raise DocumentDependencyError(
            "pdf-inspector (extra 'documents') or PyMuPDF (extra 'pdf') is required to extract PDF text"
        )


def default_provider(**kwargs: Any) -> DocumentProvider:
    return DocumentProvider(**kwargs)


def provenance_fields(content: DocumentContent) -> dict[str, Any]:
    """Safe provenance fields for JSON payloads (never includes secrets)."""
    return {
        "extraction_engine": content.extraction_engine,
        "document_format": content.format,
        "pdf_classification": content.pdf_classification,
        "ocr_used": bool(content.ocr_used),
        "ocr_provider": content.ocr_provider,
    }
