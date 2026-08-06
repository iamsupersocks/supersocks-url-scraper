"""Testable document extraction provider.

Local path (default):
  - Office/EPUB/CSV/RTF → anydoc
  - PDF → pdf-inspector classify+extract, then PyMuPDF compatibility fallback

Cloud OCR (strictly opt-in via DOCUMENT_MODE=auto|firecrawl + FIRECRAWL_API_KEY):
  - Only for PDF scanned / image_based / mixed, or when local extraction is unusable
  - Stdlib HTTP to Firecrawl v2 scrape/parse; never activated by key presence alone
"""

from __future__ import annotations

import os
from typing import Any, Callable

from .detect import (
    ALL_DOCUMENT_FORMATS,
    DOCUMENT_FORMATS,
    detect_document_format,
    title_from_markdown,
)
from .firecrawl import (
    DEFAULT_MAX_PAGES,
    firecrawl_api_key,
    firecrawl_ocr_allowed,
    firecrawl_scrape_url,
    resolve_document_mode,
    scrape_pdf_ocr,
)
from .models import DocumentContent, DocumentDependencyError, DocumentParseError, FirecrawlOcrError

PDF_NEEDS_OCR = frozenset({"scanned", "image_based", "mixed"})


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


def _local_pdf_unusable(content: DocumentContent | None) -> bool:
    if content is None:
        return True
    if content.pdf_classification in PDF_NEEDS_OCR:
        return True
    return not (content.text or "").strip()


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


def _ocr_enabled(mode: str, *, api_key: str, allow_ocr: bool | None) -> bool:
    """Cloud OCR requires explicit mode (auto|firecrawl) AND key; allow_ocr cannot bypass local mode."""
    if allow_ocr is False:
        return False
    return firecrawl_ocr_allowed(mode, api_key=api_key)


class DocumentProvider:
    """Orchestrates local document engines and optional Firecrawl OCR."""

    def __init__(
        self,
        *,
        mode: str | None = None,
        api_key: str | None = None,
        scrape_endpoint: str | None = None,
        max_pages: int | None = None,
        ocr_timeout: int | None = None,
        environ: dict[str, str] | None = None,
        firecrawl_opener: Callable[..., Any] | None = None,
    ) -> None:
        env = environ if environ is not None else os.environ
        self.mode = resolve_document_mode(mode, environ=env)
        self.api_key = (api_key if api_key is not None else firecrawl_api_key(env)).strip()
        self.scrape_endpoint = (scrape_endpoint or firecrawl_scrape_url(env)).strip()
        try:
            default_pages = int(env.get("DOCUMENT_MAX_PAGES", str(DEFAULT_MAX_PAGES)))
        except (TypeError, ValueError):
            default_pages = DEFAULT_MAX_PAGES
        self.max_pages = max(1, int(max_pages if max_pages is not None else default_pages))
        try:
            default_timeout = int(env.get("FIRECRAWL_TIMEOUT", "60"))
        except (TypeError, ValueError):
            default_timeout = 60
        self.ocr_timeout = max(1, int(ocr_timeout if ocr_timeout is not None else default_timeout))
        self.firecrawl_opener = firecrawl_opener

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
        allow_ocr: bool | None = None,
    ) -> DocumentContent:
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

        # 2) PyMuPDF compatibility fallback (best local text; may still need cloud OCR)
        try:
            pymupdf_result = extract_pdf_pymupdf(data, max_pages=self.max_pages)
        except DocumentDependencyError as exc:
            local_error = local_error or exc
            warnings.append(str(exc))
        except DocumentParseError as exc:
            local_error = local_error or exc
            warnings.append(str(exc))

        local = _best_local_pdf_content(inspector, pymupdf_result, warnings)

        # 3) Optional Firecrawl OCR
        ocr_ok = _ocr_enabled(self.mode, api_key=self.api_key, allow_ocr=allow_ocr)
        needs_ocr = _local_pdf_unusable(local)

        if ocr_ok and needs_ocr and (source_url or final_url):
            try:
                ocr = scrape_pdf_ocr(
                    source_url or final_url or "",
                    api_key=self.api_key,
                    max_pages=self.max_pages,
                    timeout=self.ocr_timeout,
                    scrape_endpoint=self.scrape_endpoint,
                    final_url=final_url,
                    opener=self.firecrawl_opener,
                )
                classification = local.pdf_classification if local else None
                return DocumentContent(
                    title=ocr.title or (local.title if local else None),
                    text=ocr.text,
                    format="pdf",
                    method="firecrawl",
                    page_count=ocr.page_count or (local.page_count if local else None),
                    pdf_classification=classification,
                    ocr_used=True,
                    ocr_provider="firecrawl",
                    warnings=tuple(warnings) + ocr.warnings,
                )
            except FirecrawlOcrError as exc:
                warnings.append(str(exc))
                if local is not None:
                    extra: list[str] = [
                        f"PDF classified as {local.pdf_classification}; OCR fallback failed ({exc.kind})"
                        if local.pdf_classification in PDF_NEEDS_OCR
                        else f"PDF local extraction incomplete; OCR fallback documented as {exc.kind}"
                    ]
                    if local.text.strip() and local.pdf_classification in PDF_NEEDS_OCR:
                        extra.append("Returning best available local text layer from PyMuPDF")
                    return DocumentContent(
                        title=local.title,
                        text=local.text,
                        format="pdf",
                        method=local.method,
                        page_count=local.page_count,
                        pdf_classification=local.pdf_classification,
                        ocr_used=False,
                        ocr_provider=None,
                        warnings=tuple(warnings) + tuple(extra),
                    )
                raise DocumentParseError(str(exc)) from exc

        if local is not None:
            extra: list[str] = []
            if not local.text.strip():
                if local.pdf_classification in PDF_NEEDS_OCR:
                    extra.append(
                        f"PDF classified as {local.pdf_classification}; no extractable text without OCR"
                    )
                else:
                    extra.append("PDF looks like a scan without OCR; no extractable text")
                if not ocr_ok:
                    extra.append(
                        "Firecrawl OCR disabled (set DOCUMENT_MODE=auto|firecrawl and FIRECRAWL_API_KEY to enable)"
                    )
            elif local.pdf_classification in PDF_NEEDS_OCR and not ocr_ok:
                extra.append(
                    f"PDF classified as {local.pdf_classification}; using local text layer only"
                )
                extra.append(
                    "Firecrawl OCR disabled (set DOCUMENT_MODE=auto|firecrawl and FIRECRAWL_API_KEY to enable)"
                )
            return DocumentContent(
                title=local.title,
                text=local.text,
                format="pdf",
                method=local.method,
                page_count=local.page_count,
                pdf_classification=local.pdf_classification,
                ocr_used=False,
                ocr_provider=None,
                warnings=tuple(warnings) + tuple(extra),
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
