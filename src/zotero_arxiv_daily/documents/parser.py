from __future__ import annotations

import gc
from collections.abc import Callable
from importlib.metadata import version
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import Field

from zotero_arxiv_daily.analysis.document_schemas import DocumentIssue
from zotero_arxiv_daily.analysis.schemas import StrictModel


DOCLING_VERSION = version("docling")


class ParsedBoundingBox(StrictModel):
    left: float
    top: float
    right: float
    bottom: float
    coordinate_origin: Literal["top_left", "bottom_left"] = "bottom_left"


class ParsedProvenance(StrictModel):
    pdf_page: int = Field(ge=1)
    bbox: ParsedBoundingBox | None


class ParsedItem(StrictModel):
    item_id: str
    label: str
    text: str
    parent_ref: str | None
    caption_refs: tuple[str, ...]
    level: int = Field(ge=0)
    reading_order: int = Field(ge=0)
    provenance: tuple[ParsedProvenance, ...]


class ParsedDocument(StrictModel):
    parser: Literal["docling"] = "docling"
    parser_version: str
    items: tuple[ParsedItem, ...]


class ParsedDocumentResult(StrictModel):
    status: Literal["success", "partial", "failed"]
    document: ParsedDocument | None
    issues: tuple[DocumentIssue, ...] = ()


class DocumentParser(Protocol):
    parser_version: str

    def parse(
        self, path: Path, *, max_pages: int, max_file_size: int
    ) -> ParsedDocumentResult: ...


class DoclingParserConfig(StrictModel):
    artifacts_path: Path
    do_ocr: Literal[False] = False
    do_table_structure: Literal[True] = True
    pipeline: Literal["standard"] = "standard"
    allow_remote_services: Literal[False] = False
    document_timeout_seconds: float = Field(default=300, gt=0)


class Converter(Protocol):
    def convert(self, path: Path, **kwargs: Any) -> Any: ...


class DoclingDocumentParser:
    def __init__(
        self,
        *,
        artifacts_path: Path,
        converter_factory: Callable[[DoclingParserConfig], Converter] | None = None,
        document_timeout_seconds: float = 300,
        garbage_collect: Callable[[], object] = gc.collect,
    ) -> None:
        self.config = DoclingParserConfig(
            artifacts_path=Path(artifacts_path),
            document_timeout_seconds=document_timeout_seconds,
        )
        self.parser_version = DOCLING_VERSION
        self.converter_factory = converter_factory or _build_docling_converter
        self._garbage_collect = garbage_collect

    def parse(
        self, path: Path, *, max_pages: int, max_file_size: int
    ) -> ParsedDocumentResult:
        local_path = Path(path)
        if not local_path.is_file():
            return _failure(
                "parser_input_not_local", "parser accepts only an existing local PDF file"
            )
        if not self.config.artifacts_path.is_dir():
            return _failure(
                "parser_model_unavailable",
                "Docling model artifacts must be prepared in the configured local directory",
            )
        try:
            return self._parse_local(
                local_path,
                max_pages=max_pages,
                max_file_size=max_file_size,
            )
        finally:
            self._garbage_collect()

    def _parse_local(
        self,
        local_path: Path,
        *,
        max_pages: int,
        max_file_size: int,
    ) -> ParsedDocumentResult:
        try:
            converter = self.converter_factory(self.config)
            result = converter.convert(
                local_path,
                raises_on_error=False,
                max_num_pages=max_pages,
                max_file_size=max_file_size,
            )
        except TimeoutError:
            return _failure("parser_timeout", "Docling conversion exceeded its configured timeout")
        except MemoryError:
            return _memory_failure()
        except (FileNotFoundError, OSError) as exc:
            return _failure(
                "parser_model_unavailable",
                f"Docling local artifacts are unavailable: {type(exc).__name__}",
            )
        except Exception as exc:
            if _contains_memory_exhaustion((exc,)):
                return _memory_failure()
            return _failure("parser_failed", f"Docling conversion failed: {type(exc).__name__}")

        status = _status_value(getattr(result, "status", "failure"))
        document = getattr(result, "document", None)
        errors = getattr(result, "errors", ()) or ()
        if status == "failure" or document is None:
            if _contains_memory_exhaustion(errors):
                return _memory_failure()
            if "timeout" in " ".join(str(error) for error in errors).lower():
                return _failure(
                    "parser_timeout", "Docling conversion exceeded its configured timeout"
                )
            return _failure("parser_failed", "Docling returned a failed conversion")
        try:
            parsed = _map_docling_document(document)
        except Exception as exc:
            return _failure("parser_failed", f"Docling mapping failed: {type(exc).__name__}")
        if status == "partial_success":
            issues = [
                DocumentIssue(
                    code="parser_partial",
                    severity="warning",
                    message="Docling reported a partial conversion",
                )
            ]
            if _contains_memory_exhaustion(errors):
                issues.append(_memory_issue())
            return ParsedDocumentResult(
                status="partial",
                document=parsed,
                issues=tuple(issues),
            )
        return ParsedDocumentResult(status="success", document=parsed)


def _build_docling_converter(config: DoclingParserConfig) -> Converter:
    # Delayed import avoids the expensive Docling/model import for unselected papers and dry runs.
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions()
    options.do_ocr = config.do_ocr
    options.do_table_structure = config.do_table_structure
    options.generate_page_images = False
    options.generate_picture_images = False
    options.artifacts_path = config.artifacts_path
    options.document_timeout = config.document_timeout_seconds
    options.enable_remote_services = config.allow_remote_services
    options.allow_external_plugins = False
    return DocumentConverter(
        allowed_formats=[InputFormat.PDF],
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)},
    )


def _map_docling_document(document: Any) -> ParsedDocument:
    items: list[ParsedItem] = []
    for reading_order, (item, level) in enumerate(
        document.iterate_items(with_groups=True, traverse_pictures=True)
    ):
        item_id = str(getattr(item, "self_ref", f"generated:{reading_order}"))
        label_value = getattr(getattr(item, "label", "other"), "value", None)
        label = str(label_value if label_value is not None else getattr(item, "label", "other"))
        parent = getattr(item, "parent", None)
        parent_ref = getattr(parent, "cref", None)
        caption_refs = tuple(
            str(reference.cref)
            for reference in (getattr(item, "captions", None) or ())
            if getattr(reference, "cref", None) is not None
        )
        provenance = tuple(_map_provenance(value) for value in (getattr(item, "prov", None) or ()))
        items.append(
            ParsedItem(
                item_id=item_id,
                label=label,
                text=str(getattr(item, "text", "") or ""),
                parent_ref=str(parent_ref) if parent_ref is not None else None,
                caption_refs=caption_refs,
                level=int(level),
                reading_order=reading_order,
                provenance=provenance,
            )
        )
    return ParsedDocument(parser_version=DOCLING_VERSION, items=tuple(items))


def _map_provenance(value: Any) -> ParsedProvenance:
    bbox_value = getattr(value, "bbox", None)
    bbox = None
    if bbox_value is not None:
        origin_value = getattr(getattr(bbox_value, "coord_origin", "bottom_left"), "value", None)
        origin = str(origin_value or getattr(bbox_value, "coord_origin", "bottom_left")).lower()
        if origin not in {"top_left", "bottom_left"}:
            origin = "bottom_left"
        bbox = ParsedBoundingBox(
            left=float(bbox_value.l),
            top=float(bbox_value.t),
            right=float(bbox_value.r),
            bottom=float(bbox_value.b),
            coordinate_origin=origin,
        )
    return ParsedProvenance(pdf_page=int(value.page_no), bbox=bbox)


def _status_value(status: Any) -> str:
    value = getattr(status, "value", status)
    return str(value).lower().replace("conversionstatus.", "")


def _failure(code: str, message: str) -> ParsedDocumentResult:
    return ParsedDocumentResult(
        status="failed",
        document=None,
        issues=(DocumentIssue(code=code, severity="error", message=message),),
    )


def _memory_failure() -> ParsedDocumentResult:
    return ParsedDocumentResult(
        status="failed",
        document=None,
        issues=(_memory_issue(),),
    )


def _memory_issue() -> DocumentIssue:
    return DocumentIssue(
        code="parser_out_of_memory",
        severity="error",
        message="Docling conversion exceeded the available local memory",
    )


def _contains_memory_exhaustion(errors: Any) -> bool:
    markers = ("bad_alloc", "out of memory", "cannot allocate memory")
    return any(
        marker in str(error).casefold()
        for error in errors
        for marker in markers
    )
