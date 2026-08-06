"""Optional documentary extraction: anydoc + pdf-inspector + PyMuPDF + Firecrawl OCR."""

from __future__ import annotations

from .detect import (
    ALL_DOCUMENT_FORMATS,
    DOCUMENT_FORMATS,
    detect_document_format,
    format_from_zip_package,
    title_from_markdown,
)
from .firecrawl import (
    assert_safe_cloud_url,
    firecrawl_api_key,
    firecrawl_ocr_allowed,
    resolve_document_mode,
    scrape_pdf_ocr,
)
from .models import DocumentContent, DocumentDependencyError, DocumentParseError, FirecrawlOcrError
from .provider import (
    DocumentProvider,
    anydoc_available,
    default_provider,
    extract_office_markdown,
    extract_pdf_pymupdf,
    pdf_inspector_available,
    provenance_fields,
    pymupdf_available,
)

# Backward-compatible aliases used by older tests / callers.
ANYDOC_DOCUMENT_FORMATS = DOCUMENT_FORMATS
ANYDOC_ALL_FORMATS = ALL_DOCUMENT_FORMATS
extract_document_markdown = extract_office_markdown
_format_from_zip_package = format_from_zip_package


def extract_pdf_with_fallback(
    data: bytes,
    *,
    source_url: str | None = None,
    final_url: str | None = None,
    provider: DocumentProvider | None = None,
) -> DocumentContent:
    engine = provider or default_provider()
    return engine.extract_pdf(data, source_url=source_url, final_url=final_url)


__all__ = [
    "ALL_DOCUMENT_FORMATS",
    "ANYDOC_ALL_FORMATS",
    "ANYDOC_DOCUMENT_FORMATS",
    "DOCUMENT_FORMATS",
    "DocumentContent",
    "DocumentDependencyError",
    "DocumentParseError",
    "DocumentProvider",
    "FirecrawlOcrError",
    "_format_from_zip_package",
    "anydoc_available",
    "assert_safe_cloud_url",
    "default_provider",
    "detect_document_format",
    "extract_document_markdown",
    "extract_office_markdown",
    "extract_pdf_pymupdf",
    "extract_pdf_with_fallback",
    "firecrawl_api_key",
    "firecrawl_ocr_allowed",
    "format_from_zip_package",
    "pdf_inspector_available",
    "provenance_fields",
    "pymupdf_available",
    "resolve_document_mode",
    "scrape_pdf_ocr",
    "title_from_markdown",
]
