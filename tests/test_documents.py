from __future__ import annotations

import io
import inspect
import zipfile
from pathlib import Path
from urllib.request import urlopen

import pytest

from supersocks_url_scraper.documents import (
    DocumentContent,
    DocumentDependencyError,
    DocumentParseError,
    DocumentProvider,
    _format_from_zip_package,
    detect_document_format,
    extract_document_markdown,
    extract_pdf_with_fallback,
)
from supersocks_url_scraper.reader import (
    FetchedResource,
    detect_content_type,
    detect_document_format as detect_document_format_resource,
    read_url,
    to_markdown,
)


def _minimal_docx_bytes(*, title: str = "Hello AnyDoc Document Title", body: str | None = None) -> bytes:
    paragraph = body or (
        "This is a paragraph about document conversion to markdown for testing purposes with enough words."
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>""",
        )
        zf.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>""",
        )
        zf.writestr(
            "word/document.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>{title}</w:t></w:r></w:p>
    <w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>
  </w:body>
</w:document>""",
        )
        zf.writestr(
            "word/_rels/document.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
</Relationships>""",
        )
    return buf.getvalue()


def _resource(
    content: bytes,
    *,
    url: str = "https://files.example/download",
    content_type: str = "application/octet-stream",
    headers: dict[str, str] | None = None,
) -> FetchedResource:
    return FetchedResource(
        url=url,
        final_url=url,
        status_code=200,
        content=content,
        content_type=content_type,
        headers=headers or {},
    )


def test_detect_docx_from_content_despite_misleading_mime() -> None:
    data = _minimal_docx_bytes()
    resource = _resource(data, content_type="text/plain")
    assert detect_document_format_resource(resource) == "docx"
    assert detect_content_type(resource) == "document"


def test_detect_pdf_from_content() -> None:
    resource = _resource(b"%PDF-1.7\n%", content_type="application/octet-stream")
    assert detect_document_format_resource(resource) == "pdf"
    assert detect_content_type(resource) == "pdf"


def test_detect_csv_from_extension_without_signature() -> None:
    resource = _resource(
        b"name,value\nalpha,1\n",
        url="https://files.example/report.csv",
        content_type="application/octet-stream",
    )
    assert detect_document_format_resource(resource) == "csv"
    assert detect_content_type(resource) == "document"


def test_detect_pptx_from_mime_family() -> None:
    resource = _resource(
        b"not-a-real-pptx",
        content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )
    assert detect_document_format_resource(resource) == "pptx"
    assert detect_content_type(resource) == "document"


def test_detect_from_content_disposition_when_url_has_no_extension() -> None:
    resource = _resource(
        b"name,value\nalpha,1\n",
        url="https://files.example/download?id=42",
        content_type="application/octet-stream",
        headers={"content-disposition": 'attachment; filename="prices.csv"'},
    )
    assert detect_document_format_resource(resource) == "csv"
    assert detect_content_type(resource) == "document"


def _patch_zip_declared_uncompressed_size(data: bytes, declared: int) -> bytes:
    out = bytearray(data)
    if out[0:4] == b"PK\x03\x04":
        out[18:22] = declared.to_bytes(4, "little")
    central = data.rfind(b"PK\x01\x02")
    if central >= 0:
        out[central + 24 : central + 28] = declared.to_bytes(4, "little")
    return bytes(out)


def _zip_with_compressed_oversized_mimetype() -> bytes:
    import struct
    import zlib

    payload = b"X" * 4096
    compressed = zlib.compress(payload)[2:-4]
    local = struct.pack(
        "<IHHHHHIIIHH",
        0x04034B50,
        20,
        0,
        zipfile.ZIP_DEFLATED,
        0,
        0,
        0,
        len(compressed),
        len(payload),
        9,
        0,
    ) + b"mimetype\x00" + compressed
    central = struct.pack(
        "<IHHHHHIIIHHHHHII",
        0x02014B50,
        20,
        20,
        0,
        zipfile.ZIP_DEFLATED,
        0,
        0,
        0,
        len(compressed),
        len(payload),
        9,
        0,
        0,
        0,
        0,
        0,
    ) + b"mimetype\x00"
    offset = len(local)
    end = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, len(central), offset, 0)
    return local + central + end


def test_zip_mimetype_oversized_declared_sniff_returns_none_without_error() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("mimetype", "application/epub+zip")
    malicious = _patch_zip_declared_uncompressed_size(buf.getvalue(), 10_000_000)
    assert _format_from_zip_package(malicious) is None
    assert _format_from_zip_package(_zip_with_compressed_oversized_mimetype()) is None


def test_detect_rtf_and_epub_families() -> None:
    rtf = _resource(b"{\\rtf1\\ansi Hello}", content_type="application/octet-stream")
    assert detect_document_format_resource(rtf) == "rtf"
    assert detect_content_type(rtf) == "document"

    epub_buf = io.BytesIO()
    with zipfile.ZipFile(epub_buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", "<container/>")
    epub = _resource(epub_buf.getvalue(), content_type="application/octet-stream")
    assert detect_document_format_resource(epub) == "epub"
    assert detect_content_type(epub) == "document"


def test_no_cloud_env_or_options_in_document_package() -> None:
    docs_root = Path(__file__).resolve().parents[1] / "src" / "supersocks_url_scraper" / "documents"
    forbidden = ("FIRECRAWL_API_KEY", "FIRECRAWL_API_URL", "FIRECRAWL_TIMEOUT", "DOCUMENT_MODE")
    for path in docs_root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{token} found in {path.name}"

    from supersocks_url_scraper import cli

    health = cli.health_payload()
    assert "firecrawl_key_configured" not in health["documents"]
    assert "firecrawl_ocr_enabled" not in health["documents"]
    assert "mode" not in health["documents"]

    openapi = cli.openapi_payload()
    props = openapi["paths"]["/summarize"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]
    assert "document_mode" not in props

    sig = inspect.signature(DocumentProvider.__init__)
    assert "api_key" not in sig.parameters
    assert "mode" not in sig.parameters


def test_extract_document_missing_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "anydoc" or name.startswith("anydoc."):
            raise ImportError("no anydoc")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(DocumentDependencyError, match="documents"):
        extract_document_markdown(_minimal_docx_bytes(), format_hint="docx")


def test_extract_document_corrupted_payload() -> None:
    pytest.importorskip("anydoc")
    with pytest.raises(DocumentParseError):
        extract_document_markdown(b"this-is-not-a-document", format_hint="docx")


def test_real_docx_to_markdown_and_read_url(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("anydoc")
    data = _minimal_docx_bytes()
    document = extract_document_markdown(data, format_hint="docx")
    assert "Hello AnyDoc Document Title" in document.text
    assert document.method == "anydoc"
    assert document.format == "docx"

    resource = _resource(
        data,
        url="https://files.example/memo.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    monkeypatch.setattr("supersocks_url_scraper.reader._fetch_with_pipeline", lambda url, **kwargs: resource)
    result = read_url("https://files.example/memo.docx", include_content=True, length=400)
    assert result["status"] == "ok"
    assert result["content_type"] == "document"
    assert result["document_format"] == "docx"
    assert result["extraction_engine"] == "anydoc"
    assert result["ocr_used"] is False
    assert result["ocr_provider"] is None
    assert "Hello AnyDoc" in (result["title"] or "")
    assert "Hello AnyDoc Document Title" in result["content"]
    md = to_markdown(result)
    assert "Document format: docx" in md
    assert "## Content" in md


def test_real_csv_markdown_table() -> None:
    pytest.importorskip("anydoc")
    document = extract_document_markdown(b"name,value\nalpha,1\nbeta,2\n", format_hint="csv")
    assert document.format == "csv"
    assert "|" in document.text
    assert "alpha" in document.text


def test_pdf_prefers_pdf_inspector_text_based(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_inspector(data: bytes, *, max_pages=None):
        calls.append("pdf-inspector")
        return DocumentContent(
            title="From Inspector",
            text="Readable PDF text layer about supersocks documents.",
            format="pdf",
            method="pdf-inspector",
            page_count=2,
            pdf_classification="text_based",
        )

    def boom(*args, **kwargs):
        calls.append("pymupdf")
        raise AssertionError("PyMuPDF should not run when pdf-inspector returns text")

    monkeypatch.setattr("supersocks_url_scraper.documents.provider.pdf_inspector_available", lambda: True)
    monkeypatch.setattr("supersocks_url_scraper.documents.provider.classify_and_extract_pdf_inspector", fake_inspector)
    monkeypatch.setattr("supersocks_url_scraper.documents.provider.extract_pdf_pymupdf", boom)
    result = extract_pdf_with_fallback(b"%PDF-1.7\n")
    assert result.method == "pdf-inspector"
    assert result.pdf_classification == "text_based"
    assert result.ocr_used is False
    assert result.ocr_provider is None
    assert calls == ["pdf-inspector"]


def test_pdf_falls_back_to_pymupdf_when_inspector_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("supersocks_url_scraper.documents.provider.pdf_inspector_available", lambda: True)
    monkeypatch.setattr(
        "supersocks_url_scraper.documents.provider.classify_and_extract_pdf_inspector",
        lambda data, *, max_pages=None: DocumentContent(
            title="Scan",
            text="",
            format="pdf",
            method="pdf-inspector",
            page_count=1,
            pdf_classification="scanned",
        ),
    )
    monkeypatch.setattr(
        "supersocks_url_scraper.documents.provider.extract_pdf_pymupdf",
        lambda data, *, max_pages=None: DocumentContent(
            title="Scan",
            text="Recovered via PyMuPDF compatibility path with enough text.",
            format="pdf",
            method="pymupdf",
            page_count=1,
        ),
    )
    result = extract_pdf_with_fallback(b"%PDF-1.7\n", provider=DocumentProvider())
    assert result.method == "pymupdf"
    assert "Recovered via PyMuPDF" in result.text
    assert result.pdf_classification == "scanned"
    assert result.ocr_used is False
    assert result.ocr_provider is None
    assert any("local text layer only" in w for w in result.warnings)
    assert any("no local ocr" in w.lower() for w in result.warnings)


def _mixed_scanned_pymupdf_mocks(monkeypatch: pytest.MonkeyPatch, *, classification: str = "mixed") -> None:
    monkeypatch.setattr("supersocks_url_scraper.documents.provider.pdf_inspector_available", lambda: True)
    monkeypatch.setattr(
        "supersocks_url_scraper.documents.provider.classify_and_extract_pdf_inspector",
        lambda data, *, max_pages=None: DocumentContent(
            title="Mixed",
            text="",
            format="pdf",
            method="pdf-inspector",
            page_count=2,
            pdf_classification=classification,
        ),
    )
    monkeypatch.setattr(
        "supersocks_url_scraper.documents.provider.extract_pdf_pymupdf",
        lambda data, *, max_pages=None: DocumentContent(
            title="Mixed",
            text="Partial PyMuPDF text layer from mixed PDF document.",
            format="pdf",
            method="pymupdf",
            page_count=2,
        ),
    )


def test_pdf_mixed_with_pymupdf_text_returns_local_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _mixed_scanned_pymupdf_mocks(monkeypatch, classification="mixed")
    provider = DocumentProvider()
    result = provider.extract_pdf(
        b"%PDF-1.4\n%",
        source_url="https://files.example/mixed.pdf",
        final_url="https://cdn.example/mixed.pdf",
    )
    assert result.method == "pymupdf"
    assert result.ocr_used is False
    assert result.ocr_provider is None
    assert result.pdf_classification == "mixed"
    assert "Partial PyMuPDF text layer" in result.text
    assert any("no local ocr" in w.lower() for w in result.warnings)


def test_pdf_scanned_with_pymupdf_text_returns_local_with_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    _mixed_scanned_pymupdf_mocks(monkeypatch, classification="scanned")
    provider = DocumentProvider()
    result = provider.extract_pdf(
        b"%PDF-1.4\n%",
        source_url="https://files.example/scan.pdf",
    )
    assert result.method == "pymupdf"
    assert result.ocr_used is False
    assert result.pdf_classification == "scanned"
    assert "Partial PyMuPDF text layer" in result.text
    assert any("no local ocr" in w.lower() for w in result.warnings)


def test_pdf_scan_without_text_is_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("supersocks_url_scraper.documents.provider.pdf_inspector_available", lambda: True)
    monkeypatch.setattr(
        "supersocks_url_scraper.documents.provider.classify_and_extract_pdf_inspector",
        lambda data, *, max_pages=None: DocumentContent(
            title="Scan",
            text="",
            format="pdf",
            method="pdf-inspector",
            page_count=3,
            pdf_classification="scanned",
        ),
    )
    monkeypatch.setattr(
        "supersocks_url_scraper.documents.provider.extract_pdf_pymupdf",
        lambda data, *, max_pages=None: DocumentContent(
            title="Scan", text="", format="pdf", method="pymupdf", page_count=3
        ),
    )
    resource = _resource(b"%PDF-1.4\n%", url="https://files.example/scan.pdf", content_type="application/pdf")
    monkeypatch.setattr("supersocks_url_scraper.reader._fetch_with_pipeline", lambda url, **kwargs: resource)
    result = read_url("https://files.example/scan.pdf")
    assert result["status"] == "partial"
    assert result["content_type"] == "pdf"
    assert result["pdf_classification"] == "scanned"
    assert result["ocr_used"] is False
    assert result["ocr_provider"] is None
    assert any("no local ocr" in w.lower() for w in result["warnings"])


def test_image_based_pdf_without_text_warns_no_local_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("supersocks_url_scraper.documents.provider.pdf_inspector_available", lambda: True)
    monkeypatch.setattr(
        "supersocks_url_scraper.documents.provider.classify_and_extract_pdf_inspector",
        lambda data, *, max_pages=None: DocumentContent(
            title="Image PDF",
            text="",
            format="pdf",
            method="pdf-inspector",
            page_count=1,
            pdf_classification="image_based",
        ),
    )
    monkeypatch.setattr(
        "supersocks_url_scraper.documents.provider.extract_pdf_pymupdf",
        lambda data, *, max_pages=None: DocumentContent(
            title="Image PDF", text="", format="pdf", method="pymupdf", page_count=1
        ),
    )
    result = extract_pdf_with_fallback(b"%PDF-1.4\n%", provider=DocumentProvider())
    assert not result.text.strip()
    assert result.pdf_classification == "image_based"
    assert result.ocr_used is False
    assert any("image_based" in w for w in result.warnings)
    assert any("no local ocr" in w.lower() for w in result.warnings)


def test_document_pipeline_makes_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"urlopen": False}

    def boom(*args, **kwargs):
        called["urlopen"] = True
        raise AssertionError("document pipeline must not perform HTTP requests")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    monkeypatch.setattr("supersocks_url_scraper.documents.provider.pdf_inspector_available", lambda: True)
    monkeypatch.setattr(
        "supersocks_url_scraper.documents.provider.classify_and_extract_pdf_inspector",
        lambda data, *, max_pages=None: DocumentContent(
            title="Scan",
            text="",
            format="pdf",
            method="pdf-inspector",
            page_count=1,
            pdf_classification="scanned",
        ),
    )
    monkeypatch.setattr(
        "supersocks_url_scraper.documents.provider.extract_pdf_pymupdf",
        lambda data, *, max_pages=None: DocumentContent(
            title="Scan",
            text="Local fallback text from PyMuPDF.",
            format="pdf",
            method="pymupdf",
            page_count=1,
        ),
    )
    extract_pdf_with_fallback(
        b"%PDF-1.4\n%",
        source_url="https://files.example/scan.pdf",
        final_url="https://cdn.example/scan.pdf",
        provider=DocumentProvider(),
    )
    assert called["urlopen"] is False
    # Sanity: urlopen import in test module still works; pipeline did not call it.
    assert urlopen is not None


def test_pdf_missing_both_local_engines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("supersocks_url_scraper.documents.provider.pdf_inspector_available", lambda: False)

    def no_pymupdf(data: bytes, *, max_pages=None):
        raise DocumentDependencyError("no pymupdf")

    monkeypatch.setattr("supersocks_url_scraper.documents.provider.extract_pdf_pymupdf", no_pymupdf)
    with pytest.raises(DocumentDependencyError, match="pdf-inspector|PyMuPDF"):
        extract_pdf_with_fallback(b"%PDF-1.7\n", provider=DocumentProvider())


def test_document_missing_dependency_via_read_url(monkeypatch: pytest.MonkeyPatch) -> None:
    resource = _resource(
        _minimal_docx_bytes(),
        url="https://files.example/memo.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    monkeypatch.setattr("supersocks_url_scraper.reader._fetch_with_pipeline", lambda url, **kwargs: resource)

    def boom(data: bytes, *, format_hint: str | None = None):
        raise DocumentDependencyError(
            "firecrawl-anydoc (package extra 'documents') is required to extract office/document Markdown"
        )

    monkeypatch.setattr("supersocks_url_scraper.reader.extract_document_markdown", boom)
    result = read_url("https://files.example/memo.docx")
    assert result["status"] == "error"
    assert result["content_type"] == "document"
    assert any("documents" in w for w in result["warnings"])


def test_openapi_and_health_advertise_documents() -> None:
    from supersocks_url_scraper import cli

    health = cli.health_payload()
    assert "documents" in health
    assert "docx" in health["documents"]["formats"]
    assert "anydoc_installed" in health["documents"]
    assert "firecrawl_ocr_enabled" not in health["documents"]

    openapi = cli.openapi_payload()
    result_schema = openapi["paths"]["/summarize"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
    for field in ("document_format", "extraction_engine", "pdf_classification", "ocr_used", "ocr_provider"):
        assert field in result_schema["properties"]
    props = openapi["paths"]["/summarize"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]
    assert "document_max_pages" in props
    assert "document_mode" not in props


def test_base_install_imports_without_document_extras() -> None:
    # documents package must import even when anydoc/pdf_inspector/fitz are absent.
    from supersocks_url_scraper import documents

    assert documents.DOCUMENT_FORMATS


def test_detect_fields_api_without_resource() -> None:
    assert detect_document_format(content=b"%PDF-1.4\n") == "pdf"
    assert detect_document_format(content_type="text/csv", url="https://x.example/a.bin") == "csv"
