"""Docling adapter with an application-owned output representation."""

from __future__ import annotations

import importlib.metadata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.rag.ingestion.loaders.base import DocumentLoader
from app.rag.models.document import DocumentElement, NormalizedDocument


class DocumentLoadError(ValueError):
    """Base error for rejected or unsuccessfully parsed source documents."""


class UnsupportedDocumentError(DocumentLoadError):
    pass


class DocumentTooLargeError(DocumentLoadError):
    pass


class EmptyDocumentError(DocumentLoadError):
    pass


class DocumentParsingError(DocumentLoadError):
    pass


@dataclass(frozen=True, slots=True)
class DoclingLoaderConfig:
    max_file_bytes: int = 50 * 1024 * 1024
    max_archive_uncompressed_bytes: int = 250 * 1024 * 1024
    document_timeout: float = 120.0
    do_ocr: bool = True
    ocr_engine: str = "rapidocr"  # rapidocr, tesseract, or easyocr
    ocr_languages: tuple[str, ...] = ("en",)
    do_table_structure: bool = True
    force_full_page_ocr: bool = False


_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm", ".md", ".markdown", ".txt",
               ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
_ZIP_MARKERS = {
    ".docx": "word/document.xml", ".pptx": "ppt/presentation.xml", ".xlsx": "xl/workbook.xml",
}


class DoclingDocumentLoader(DocumentLoader):
    """Load PDF, Office, web, plain text, and image documents with Docling."""

    def __init__(self, config: DoclingLoaderConfig | None = None, *, converter: Any = None) -> None:
        self.config = config or DoclingLoaderConfig()
        self._converter = converter

    @staticmethod
    def installed_version() -> str | None:
        try:
            return importlib.metadata.version("docling")
        except importlib.metadata.PackageNotFoundError:
            return None

    def _get_converter(self) -> Any:
        if self._converter is not None:
            return self._converter
        try:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption
            from docling.datamodel.pipeline_options import EasyOcrOptions, RapidOcrOptions, TesseractOcrOptions
        except ImportError as exc:
            raise DocumentParsingError("Docling is required to parse documents; install the project dependencies") from exc

        pdf_options = PdfPipelineOptions()
        pdf_options.do_ocr = self.config.do_ocr
        pdf_options.do_table_structure = self.config.do_table_structure
        pdf_options.document_timeout = self.config.document_timeout
        pdf_options.force_full_page_ocr = self.config.force_full_page_ocr
        if self.config.ocr_engine.lower() == "tesseract":
            pdf_options.ocr_options = TesseractOcrOptions(lang=list(self.config.ocr_languages))
        elif self.config.ocr_engine.lower() == "rapidocr":
            pdf_options.ocr_options = RapidOcrOptions(lang=list(self.config.ocr_languages))
        elif self.config.ocr_engine.lower() == "easyocr":
            pdf_options.ocr_options = EasyOcrOptions(lang=list(self.config.ocr_languages))
        else:
            raise ValueError("ocr_engine must be rapidocr, tesseract, or easyocr")

        format_options = {
            InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options),
        }
        # ImageFormatOption takes the same PDF pipeline options in current Docling releases.
        format_options[InputFormat.IMAGE] = ImageFormatOption(pipeline_options=pdf_options)
        self._converter = DocumentConverter(allowed_formats=[
            InputFormat.PDF, InputFormat.DOCX, InputFormat.PPTX, InputFormat.XLSX,
            InputFormat.HTML, InputFormat.MD, InputFormat.IMAGE,
        ], format_options=format_options)
        return self._converter

    def load(self, source: str | Path) -> NormalizedDocument:
        path = Path(source)
        if ".." in path.parts:
            raise UnsupportedDocumentError("Path traversal is not allowed")
        try:
            path = path.resolve(strict=True)
            if not path.is_file():
                raise UnsupportedDocumentError("Document source must be a regular file")
            size = path.stat().st_size
            if size <= 0:
                raise EmptyDocumentError("Document is empty")
            if size > self.config.max_file_bytes:
                raise DocumentTooLargeError("Document exceeds the configured size limit")
            kind = self._validate_content(path)
            result = self._get_converter().convert(
                path, raises_on_error=False, max_file_size=self.config.max_file_bytes
            )
        except DocumentLoadError:
            raise
        except TimeoutError as exc:
            raise DocumentParsingError("Document parsing timed out") from exc
        except (MemoryError, OSError) as exc:
            raise DocumentParsingError("Document parsing failed due to an I/O or resource error") from exc
        except Exception as exc:
            raise DocumentParsingError(f"Could not parse {path.name}: {exc}") from exc

        doc = getattr(result, "document", None)
        status = str(getattr(result, "status", "")).lower()
        if doc is None or "failure" in status or "error" in status or "partial" in status:
            details = getattr(result, "errors", None)
            raise DocumentParsingError(f"Docling failed to parse the document: {details or status or 'no output'}")
        try:
            normalized = self._normalize(doc, path, kind)
        except (MemoryError, OSError) as exc:
            raise DocumentParsingError("Document normalization failed due to an I/O or resource error") from exc
        except Exception as exc:
            raise DocumentParsingError(f"Could not normalize {path.name}: {exc}") from exc
        if not normalized.text.strip() and not normalized.elements:
            raise EmptyDocumentError("No readable text or structural content was found")
        return normalized

    def _validate_content(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix not in _EXTENSIONS:
            raise UnsupportedDocumentError(f"Unsupported file extension: {suffix or '(none)'}")
        with path.open("rb") as stream:
            prefix = stream.read(8192)
        if suffix == ".pdf":
            if not prefix.startswith(b"%PDF-"):
                raise UnsupportedDocumentError("File content is not a PDF")
            return "pdf"
        if suffix in _ZIP_MARKERS:
            try:
                with zipfile.ZipFile(path) as archive:
                    if _ZIP_MARKERS[suffix] not in archive.namelist():
                        raise UnsupportedDocumentError("Office file content does not match its extension")
                    if sum(item.file_size for item in archive.infolist()) > self.config.max_archive_uncompressed_bytes:
                        raise DocumentTooLargeError("Expanded Office document exceeds the configured size limit")
                    marker = archive.getinfo(_ZIP_MARKERS[suffix])
                    if marker.file_size > self.config.max_archive_uncompressed_bytes:
                        raise DocumentTooLargeError("Office document component exceeds the configured size limit")
                    with archive.open(marker) as member:
                        member.read(1024 * 1024 + 1)
            except zipfile.BadZipFile as exc:
                raise UnsupportedDocumentError("Office document is not a valid ZIP-based file") from exc
            return suffix[1:]
        if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}:
            signatures = {".png": b"\x89PNG\r\n\x1a\n", ".jpg": b"\xff\xd8\xff", ".jpeg": b"\xff\xd8\xff", ".tif": (b"II*\x00", b"MM\x00*"), ".tiff": (b"II*\x00", b"MM\x00*"), ".bmp": b"BM", ".webp": b"RIFF"}
            sig = signatures[suffix]
            if not (prefix.startswith(sig) if isinstance(sig, bytes) else any(prefix.startswith(s) for s in sig)):
                raise UnsupportedDocumentError("Image content does not match its extension")
            if suffix == ".webp" and prefix[8:12] != b"WEBP":
                raise UnsupportedDocumentError("Image content does not match its extension")
            return "image"
        if suffix in {".html", ".htm"}:
            lowered = prefix.lstrip().lower()
            if lowered and not any(token in lowered for token in (b"<html", b"<!doctype html", b"<body", b"<head", b"<title", b"<h1", b"<p")):
                raise UnsupportedDocumentError("File content does not look like HTML")
            return "html"
        if suffix in {".md", ".markdown"}:
            return "markdown"
        # Plain text must be valid UTF-8; reject NUL-containing binary payloads.
        try:
            prefix.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UnsupportedDocumentError("Text file is not valid UTF-8") from exc
        if b"\x00" in prefix:
            raise UnsupportedDocumentError("Text file contains binary data")
        return "text"

    def _normalize(self, doc: Any, path: Path, kind: str) -> NormalizedDocument:
        elements: list[DocumentElement] = []
        iterator = getattr(doc, "iterate_items", None)
        if callable(iterator):
            for pair in iterator(with_groups=True):
                item = pair[0] if isinstance(pair, tuple) else pair
                label = getattr(getattr(item, "label", None), "value", getattr(item, "label", None))
                label = str(label or type(item).__name__).lower()
                text = str(getattr(item, "text", "") or "")
                prov = getattr(item, "prov", None) or []
                page_no = getattr(prov[0], "page_no", None) if prov else None
                level = getattr(item, "level", None)
                caption = getattr(item, "caption", None)
                caption_exporter = getattr(item, "caption_text", None)
                if not caption and callable(caption_exporter):
                    try:
                        caption = caption_exporter(doc)
                    except Exception:
                        caption = None
                image_ref = getattr(item, "image_ref", None)
                if not image_ref and label in {"picture", "image"}:
                    image_ref = getattr(item, "self_ref", None)
                if not text and label not in {"picture", "image"}:
                    exporter = getattr(item, "export_to_markdown", None)
                    if callable(exporter):
                        try:
                            text = str(exporter(doc) or "")
                        except Exception:
                            text = ""
                elements.append(DocumentElement(
                    kind=label, text=text, page_number=page_no, level=level,
                    caption=str(caption) if caption else None,
                    image_ref=str(image_ref) if image_ref else None,
                    metadata={"source_ref": str(getattr(item, "self_ref", ""))} if getattr(item, "self_ref", None) else {},
                ))
        exported_text = getattr(doc, "export_to_text", None)
        text = str(exported_text() if callable(exported_text) else "")
        if not text:
            text = "\n".join(element.text for element in elements if element.text)
        pages = sorted({element.page_number for element in elements if element.page_number is not None})
        return NormalizedDocument(text=text, elements=tuple(elements), metadata={
            "source_name": path.name,
            "source_format": kind,
            "source_size_bytes": path.stat().st_size,
            "page_numbers": pages,
            "parser": "docling",
            "parser_version": self.installed_version(),
            "docling_metadata": getattr(doc, "origin", None).__dict__ if getattr(doc, "origin", None) and hasattr(getattr(doc, "origin"), "__dict__") else {},
        })
