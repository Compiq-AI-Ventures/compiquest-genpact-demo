"""CD&A report generator — package entry point.

Public surface: ``await build_cda_report(content)`` → ``.docx`` bytes.

Pipeline (fully deterministic — the LLM authors nothing):

    uploaded bytes (.xlsx / .docx / .pdf, a per-NEO comp table)
        → parser.parse_upload      → list[NEO]      (numbers, deterministic)
        → CDADataset                                (validated payload)
        → template.body_blocks                      (fixed template wording)
        → docx_builder.build_docx  (python-docx)    → .docx bytes

The uploaded file is the sole source of every per-executive figure. The
surrounding narrative is the fixed Genpact CD&A template text; only the
numbers in the compensation tables change from one upload to the next.

(A parallel ``pdf_builder`` renders the same blocks to PDF; the report is now
delivered as .docx, so it is retained but unused.)
"""

from __future__ import annotations

from . import docx_builder, parser, template
from .parser import CDAParseError
from .schema import CDADataset

__all__ = ["CDAParseError", "build_cda_report"]

# MIME type for the generated Word document (a convenience for the router).
DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


async def build_cda_report(content: bytes, *, filename: str = "") -> bytes:
    """Parse the uploaded file and render the CD&A report to ``.docx`` bytes.

    ``content`` may be an .xlsx/.xlsm workbook, a .docx document, or a .pdf
    containing the per-executive compensation table; ``filename`` is used only
    as a fallback hint for format detection (the parser detects by magic bytes
    first).

    Raises :class:`CDAParseError` if the file yields no compensation rows;
    otherwise always returns valid .docx bytes. Async so the interface stays a
    stable awaitable (the render itself is synchronous python-docx).
    """
    neos = parser.parse_upload(content, filename)
    ds = CDADataset(neos=neos)
    blocks = template.body_blocks(ds)
    return docx_builder.build_docx(ds, blocks)
