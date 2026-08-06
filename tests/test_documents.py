from __future__ import annotations

import io
import json
import zipfile
from urllib.error import HTTPError, URLError

import pytest

from supersocks_url_scraper.documents import (
    DocumentContent,
    DocumentDependencyError,
    DocumentParseError,
    DocumentProvider,
    FirecrawlOcrError,
    _format_from_zip_package,
    detect_document_format,
    extract_document_markdown,
    extract_pdf_with_fallback,
    firecrawl_ocr_allowed,
    resolve_document_mode,
    scrape_pdf_ocr,
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


def test_document_mode_defaults_and_key_alone_does_not_enable_ocr() -> None:
    assert resolve_document_mode(environ={}) == "local"
    assert resolve_document_mode(environ={"DOCUMENT_MODE": "auto"}) == "auto"
    assert firecrawl_ocr_allowed("local", api_key="fc-secret") is False
    assert firecrawl_ocr_allowed("auto", api_key="") is False
    assert firecrawl_ocr_allowed("auto", api_key="fc-secret") is True
    assert firecrawl_ocr_allowed("firecrawl", api_key="fc-secret") is True


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
    result = extract_pdf_with_fallback(b"%PDF-1.7\n", provider=DocumentProvider(mode="local"))
    assert result.method == "pymupdf"
    assert "Recovered via PyMuPDF" in result.text
    assert result.pdf_classification == "scanned"
    assert any("local text layer only" in w for w in result.warnings)


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


def test_pdf_mixed_with_pymupdf_text_still_calls_firecrawl_in_auto_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"ocr": False}

    class _Resp:
        status = 200

        def read(self, n: int = -1) -> bytes:
            return json.dumps(
                {
                    "success": True,
                    "data": {
                        "markdown": "# OCR\n\nFull OCR body from Firecrawl for mixed PDF.",
                        "metadata": {"title": "OCR", "pagesParsed": 2},
                    },
                }
            ).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_ocr(*args, **kwargs):
        called["ocr"] = True
        return DocumentContent(
            title="OCR",
            text="Full OCR body from Firecrawl for mixed PDF.",
            format="pdf",
            method="firecrawl",
            page_count=2,
            ocr_used=True,
            ocr_provider="firecrawl",
        )

    _mixed_scanned_pymupdf_mocks(monkeypatch, classification="mixed")
    monkeypatch.setattr("supersocks_url_scraper.documents.provider.scrape_pdf_ocr", fake_ocr)
    provider = DocumentProvider(mode="auto", api_key="fc-test-key", firecrawl_opener=lambda req, timeout=60: _Resp())
    result = provider.extract_pdf(
        b"%PDF-1.4\n%",
        source_url="https://files.example/mixed.pdf",
        final_url="https://cdn.example/mixed.pdf",
    )
    assert called["ocr"] is True
    assert result.method == "firecrawl"
    assert result.ocr_used is True
    assert result.pdf_classification == "mixed"
    assert "Full OCR body" in result.text


def test_pdf_scanned_pymupdf_text_ocr_failure_returns_local_with_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    _mixed_scanned_pymupdf_mocks(monkeypatch, classification="scanned")

    def fail_ocr(*args, **kwargs):
        raise FirecrawlOcrError("firecrawl OCR quota exceeded (HTTP 429)", kind="quota")

    monkeypatch.setattr("supersocks_url_scraper.documents.provider.scrape_pdf_ocr", fail_ocr)
    provider = DocumentProvider(mode="auto", api_key="fc-test-key")
    result = provider.extract_pdf(
        b"%PDF-1.4\n%",
        source_url="https://files.example/scan.pdf",
    )
    assert result.method == "pymupdf"
    assert result.ocr_used is False
    assert result.pdf_classification == "scanned"
    assert "Partial PyMuPDF text layer" in result.text
    assert any("quota" in w for w in result.warnings)
    assert any("local text layer" in w.lower() for w in result.warnings)


def test_allow_ocr_true_cannot_bypass_explicit_local_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"ocr": False}

    def boom(*args, **kwargs):
        called["ocr"] = True
        raise AssertionError("must not call Firecrawl when mode is local")

    _mixed_scanned_pymupdf_mocks(monkeypatch, classification="scanned")
    monkeypatch.setattr("supersocks_url_scraper.documents.provider.scrape_pdf_ocr", boom)
    provider = DocumentProvider(mode="local", api_key="fc-should-not-matter")
    result = provider.extract_pdf(
        b"%PDF%",
        source_url="https://files.example/scan.pdf",
        allow_ocr=True,
    )
    assert called["ocr"] is False
    assert result.method == "pymupdf"
    assert result.ocr_used is False
    assert "Partial PyMuPDF text layer" in result.text
    assert result.pdf_classification == "scanned"


def test_pdf_scan_without_ocr_is_partial(monkeypatch: pytest.MonkeyPatch) -> None:
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
    result = read_url("https://files.example/scan.pdf", document_mode="local")
    assert result["status"] == "partial"
    assert result["content_type"] == "pdf"
    assert result["pdf_classification"] == "scanned"
    assert result["ocr_used"] is False
    assert any("ocr" in w.lower() or "scan" in w.lower() for w in result["warnings"])


def test_firecrawl_ocr_success_mocked(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        status = 200

        def read(self, n: int = -1) -> bytes:
            return json.dumps(
                {
                    "success": True,
                    "data": {
                        "markdown": "# OCR Title\n\nRecovered scanned page text with enough body for summary.",
                        "metadata": {"title": "OCR Title", "pagesParsed": 2},
                    },
                }
            ).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("supersocks_url_scraper.documents.provider.pdf_inspector_available", lambda: True)
    monkeypatch.setattr(
        "supersocks_url_scraper.documents.provider.classify_and_extract_pdf_inspector",
        lambda data, *, max_pages=None: DocumentContent(
            title=None, text="", format="pdf", method="pdf-inspector", page_count=2, pdf_classification="scanned"
        ),
    )
    monkeypatch.setattr(
        "supersocks_url_scraper.documents.provider.extract_pdf_pymupdf",
        lambda data, *, max_pages=None: DocumentContent(
            title=None, text="", format="pdf", method="pymupdf", page_count=2
        ),
    )
    provider = DocumentProvider(mode="auto", api_key="fc-test-key", firecrawl_opener=lambda req, timeout=60: _Resp())
    result = provider.extract_pdf(
        b"%PDF-1.4\n%",
        source_url="https://files.example/scan.pdf",
        final_url="https://cdn.example/scan.pdf",
    )
    assert result.ocr_used is True
    assert result.ocr_provider == "firecrawl"
    assert result.method == "firecrawl"
    assert "Recovered scanned page" in result.text
    assert result.pdf_classification == "scanned"


@pytest.mark.parametrize("kind", ["auth", "quota", "timeout", "network", "malformed"])
def test_firecrawl_ocr_error_kinds(kind: str) -> None:
    def opener(req, timeout=60):
        if kind == "auth":
            raise HTTPError(
                "https://api.firecrawl.dev/v2/scrape",
                401,
                "Unauthorized",
                hdrs=None,
                fp=io.BytesIO(b'{"error":"unauthorized"}'),
            )
        if kind == "quota":
            raise HTTPError(
                "https://api.firecrawl.dev/v2/scrape",
                429,
                "Too Many",
                hdrs=None,
                fp=io.BytesIO(b'{"error":"rate"}'),
            )
        if kind == "timeout":
            raise TimeoutError("slow")
        if kind == "network":
            raise URLError("down")

        class _Bad:
            status = 200

            def read(self, n: int = -1) -> bytes:
                return b"not-json"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        return _Bad()

    with pytest.raises(FirecrawlOcrError) as exc:
        scrape_pdf_ocr(
            "https://files.example/scan.pdf",
            api_key="fc-test-key",
            opener=opener,
        )
    assert exc.value.kind == kind
    assert "fc-test-key" not in str(exc.value)


def test_firecrawl_blocks_private_and_credentialed_urls() -> None:
    for bad in (
        "http://127.0.0.1/secret.pdf",
        "http://localhost/x.pdf",
        "http://10.0.0.5/x.pdf",
        "https://user:pass@files.example/x.pdf",
        "file:///tmp/x.pdf",
    ):
        with pytest.raises(FirecrawlOcrError) as exc:
            scrape_pdf_ocr(bad, api_key="fc-test-key", opener=lambda *a, **k: None)
        assert exc.value.kind == "blocked"


def test_firecrawl_blocks_unsafe_final_url_after_redirect() -> None:
    with pytest.raises(FirecrawlOcrError) as exc:
        scrape_pdf_ocr(
            "https://files.example/scan.pdf",
            api_key="fc-test-key",
            final_url="http://192.168.1.10/internal.pdf",
            opener=lambda *a, **k: None,
        )
    assert exc.value.kind == "blocked"


def test_key_present_but_mode_local_never_calls_firecrawl(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"ocr": False}

    def boom(*args, **kwargs):
        called["ocr"] = True
        raise AssertionError("must not call Firecrawl in local mode")

    monkeypatch.setattr("supersocks_url_scraper.documents.provider.pdf_inspector_available", lambda: True)
    monkeypatch.setattr(
        "supersocks_url_scraper.documents.provider.classify_and_extract_pdf_inspector",
        lambda data, *, max_pages=None: DocumentContent(
            title=None, text="", format="pdf", method="pdf-inspector", page_count=1, pdf_classification="scanned"
        ),
    )
    monkeypatch.setattr(
        "supersocks_url_scraper.documents.provider.extract_pdf_pymupdf",
        lambda data, *, max_pages=None: DocumentContent(title=None, text="", format="pdf", method="pymupdf", page_count=1),
    )
    monkeypatch.setattr("supersocks_url_scraper.documents.provider.scrape_pdf_ocr", boom)
    provider = DocumentProvider(mode="local", api_key="fc-should-not-matter")
    result = provider.extract_pdf(b"%PDF%", source_url="https://files.example/scan.pdf")
    assert called["ocr"] is False
    assert result.ocr_used is False
    assert not result.text.strip()


def test_pdf_missing_both_local_engines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("supersocks_url_scraper.documents.provider.pdf_inspector_available", lambda: False)

    def no_pymupdf(data: bytes, *, max_pages=None):
        raise DocumentDependencyError("no pymupdf")

    monkeypatch.setattr("supersocks_url_scraper.documents.provider.extract_pdf_pymupdf", no_pymupdf)
    with pytest.raises(DocumentDependencyError, match="pdf-inspector|PyMuPDF"):
        extract_pdf_with_fallback(b"%PDF-1.7\n", provider=DocumentProvider(mode="local"))


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
    assert health["documents"]["mode"] in {"local", "auto", "firecrawl"}
    assert "firecrawl_ocr_enabled" in health["documents"]
    assert health["documents"]["firecrawl_ocr_enabled"] is False or isinstance(
        health["documents"]["firecrawl_ocr_enabled"], bool
    )

    openapi = cli.openapi_payload()
    result_schema = openapi["paths"]["/summarize"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
    for field in ("document_format", "extraction_engine", "pdf_classification", "ocr_used", "ocr_provider"):
        assert field in result_schema["properties"]
    assert "document_mode" in openapi["paths"]["/summarize"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]


def test_base_install_imports_without_document_extras() -> None:
    # documents package must import even when anydoc/pdf_inspector/fitz are absent.
    from supersocks_url_scraper import documents
    from supersocks_url_scraper.documents import resolve_document_mode

    assert resolve_document_mode("local") == "local"
    assert documents.DOCUMENT_FORMATS


def test_detect_fields_api_without_resource() -> None:
    assert detect_document_format(content=b"%PDF-1.4\n") == "pdf"
    assert detect_document_format(content_type="text/csv", url="https://x.example/a.bin") == "csv"
