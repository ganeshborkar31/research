"""Adapter-level format, normalization, and input validation tests."""

from types import SimpleNamespace
import zipfile

import pytest

from app.rag.ingestion.loaders.docling import (
    DoclingDocumentLoader,
    DoclingLoaderConfig,
    DocumentTooLargeError,
    UnsupportedDocumentError,
)


class _Doc:
    origin = None

    def iterate_items(self, with_groups=True):
        yield SimpleNamespace(
            label=SimpleNamespace(value="section_header"), text="Heading", level=1,
            prov=[SimpleNamespace(page_no=1)], self_ref="#/texts/0", caption=None,
        ), 0
        yield SimpleNamespace(
            label=SimpleNamespace(value="table"), text="A | B\n--|--\n1 | 2", level=None,
            prov=[SimpleNamespace(page_no=2)], self_ref="#/tables/0", caption="Results",
        ), 0

    def export_to_text(self):
        return "Heading\nA | B\n--|--\n1 | 2"


class _Converter:
    def convert(self, path, **kwargs):
        return SimpleNamespace(document=_Doc(), status="success", errors=[])


def _write_fixture(path, suffix):
    if suffix == ".pdf":
        path.write_bytes(b"%PDF-1.7\nfixture")
    elif suffix in {".docx", ".pptx", ".xlsx"}:
        marker = {".docx": "word/document.xml", ".pptx": "ppt/presentation.xml", ".xlsx": "xl/workbook.xml"}[suffix]
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(marker, "<root/>")
    elif suffix == ".png":
        path.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    elif suffix == ".html":
        path.write_text("<html><body><h1>Heading</h1></body></html>", encoding="utf-8")
    else:
        path.write_text("# Heading\nSome source content", encoding="utf-8")


@pytest.mark.parametrize("suffix", [".pdf", ".docx", ".pptx", ".xlsx", ".html", ".md", ".txt", ".png"])
def test_supported_formats_produce_normalized_document(tmp_path, suffix):
    source = tmp_path / f"sample{suffix}"
    _write_fixture(source, suffix)
    document = DoclingDocumentLoader(converter=_Converter()).load(source)

    assert document.metadata["source_name"] == source.name
    assert document.metadata["source_format"]
    assert document.elements[0].kind == "section_header"
    assert document.elements[0].level == 1
    assert document.elements[1].page_number == 2
    assert document.elements[1].caption == "Results"
    assert "Heading" in document.text


def test_rejects_extension_content_mismatch(tmp_path):
    source = tmp_path / "fake.pdf"
    source.write_text("not a pdf", encoding="utf-8")
    with pytest.raises(UnsupportedDocumentError):
        DoclingDocumentLoader(converter=_Converter()).load(source)


def test_rejects_oversized_input(tmp_path):
    source = tmp_path / "large.txt"
    source.write_text("content", encoding="utf-8")
    loader = DoclingDocumentLoader(config=DoclingLoaderConfig(max_file_bytes=2), converter=_Converter())
    with pytest.raises(DocumentTooLargeError):
        loader.load(source)
