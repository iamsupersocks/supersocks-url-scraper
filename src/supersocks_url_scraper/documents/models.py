"""Document extraction models and errors."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DocumentContent:
    title: str | None
    text: str
    format: str
    method: str
    page_count: int | None = None
    pdf_classification: str | None = None
    ocr_used: bool = False
    ocr_provider: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def extraction_engine(self) -> str:
        return self.method


class DocumentDependencyError(RuntimeError):
    pass


class DocumentParseError(RuntimeError):
    pass


class FirecrawlOcrError(RuntimeError):
    """Cloud OCR failed; caller may fall back to partial local result."""

    def __init__(self, message: str, *, kind: str = "error") -> None:
        super().__init__(message)
        self.kind = kind  # auth | quota | network | timeout | malformed | blocked | error
