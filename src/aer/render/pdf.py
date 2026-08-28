"""The immutable PDF: WeasyPrint over the approved HTML, finished by pikepdf.

**Rendered from the stored preview HTML, at approval.** The bytes a reader downloads are
derived from exactly the HTML the operator approved at Gate 2 — one assembly, one more
serialisation — and both are content-addressed artefacts, so "is this the approved
document?" is a hash comparison, not an act of trust.

**Tamper-evident, not tamper-proof — and the cipher choice is part of that honesty.**
The pikepdf pass sets owner-password permissions (no modification, no form filling),
strips any form, embeds XMP carrying the report id and content hash, and stamps every
date from ``approved_at``. Two deliberate, load-bearing choices follow from wanting the
*same rows to produce the same bytes*, verified empirically against this qpdf:

* **RC4 (R4) rather than AES, with the metadata left unencrypted.** AES salts every
  stream with a random IV, so an AES-encrypted save can never be byte-stable; and qpdf
  refuses its content-derived ``deterministic_id`` for encrypted output entirely. RC4's
  weakness is irrelevant here because nothing is confidential — the user password is
  empty and anyone may open the file. The encryption exists only to carry the permission
  bits, which any capable tool can strip regardless of cipher; what cannot be forged is
  the artefact store's SHA-256 of these bytes, and byte-stability is what makes that
  digest re-checkable by re-rendering.
* **A static ``/ID``.** The PDF's own ID is a constant rather than content-derived,
  because qpdf cannot derive one for encrypted output. The document's real identity is
  the ``dc:identifier`` and content hash in the XMP, and the artefact digest.
* **Full fonts, no subsetting.** WeasyPrint's font subsetter emits differently ordered
  tables from one render to the next within a single process, which was measured here as
  five renders producing five byte streams. Embedding the complete faces costs around two
  megabytes and buys reproducibility; a research note is not a web page.

The owner password is derived from the report's content hash. It is not a secret and is
not treated as one; it is the mechanism the format requires for permission bits.
"""

from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO

import pikepdf
from weasyprint import HTML

__all__ = ["PDF_PRODUCER", "finish_pdf", "render_pdf"]

PDF_PRODUCER = "Tracework Invest"


def render_pdf(html: str, *, report_id: str, content_hash: str, approved_at: datetime) -> bytes:
    """The approved HTML as a finished, permission-flagged, byte-stable PDF.

    Args:
        html: The stored preview HTML — the exact bytes Gate 2 approved, never a
            re-render from rows that may since have changed.
        report_id: Embedded in the XMP as ``dc:identifier``.
        content_hash: The report's ``content_hash``, embedded in the XMP and used to
            derive the owner password.
        approved_at: Stamped as every creation and modification date. The moment a human
            approved is the only date this document honestly has.
    """
    raw = HTML(string=html).write_pdf(full_fonts=True)
    assert raw is not None  # write_pdf with no target returns bytes
    return finish_pdf(
        raw,
        report_id=report_id,
        content_hash=content_hash,
        approved_at=approved_at,
    )


def finish_pdf(raw: bytes, *, report_id: str, content_hash: str, approved_at: datetime) -> bytes:
    """The pikepdf pass, separated so a test can hold its properties on any input PDF."""
    stamp = approved_at.astimezone(UTC)
    xmp_date = stamp.strftime("%Y-%m-%dT%H:%M:%SZ")

    with pikepdf.open(BytesIO(raw)) as pdf:
        # No form survives: a research note is not a form, and a fillable field would be
        # a writable surface on a frozen document.
        if "/AcroForm" in pdf.Root:
            del pdf.Root.AcroForm

        # The document info dictionary is written by the metadata sync on context
        # exit, from the XMP values below — one source of truth for both blocks.
        with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
            meta["dc:identifier"] = report_id
            meta["dc:description"] = (
                f"Tracework Invest report {report_id}; "
                f"content sha256 {content_hash}. Personal research tool output, "
                "not regulated investment advice."
            )
            meta["xmp:CreateDate"] = xmp_date
            meta["xmp:ModifyDate"] = xmp_date
            meta["xmp:MetadataDate"] = xmp_date
            meta["pdf:Producer"] = PDF_PRODUCER

        out = BytesIO()
        pdf.save(
            out,
            static_id=True,
            encryption=pikepdf.Encryption(
                owner=content_hash,
                user="",
                R=4,
                aes=False,
                metadata=False,
                allow=pikepdf.Permissions(
                    modify_annotation=False,
                    modify_assembly=False,
                    modify_form=False,
                    modify_other=False,
                ),
            ),
        )
    return out.getvalue()
