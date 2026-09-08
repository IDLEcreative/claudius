"""Conservative financial attachment selection, shared by both email collectors.

Selection is evidence gathering, not confirmation of entity or VAT treatment.
Sender identity alone never makes an attachment a financial document.
"""

import os
import re

FINANCIAL_WORDS = re.compile(
    r"\b(?:invoices?|receipts?|credit[ _-]?notes?|statements?|"
    r"payment|billing|purchase|order confirmation)\b", re.IGNORECASE
)
DECORATIVE_NAME = re.compile(
    r"^(?:logo|signature|icon|banner|social|facebook|linkedin|twitter)"
    r"(?:[\W_\d]|$)", re.IGNORECASE
)
EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"}


def has_financial_subject(subject: str) -> bool:
    """Require a document signal rather than a vendor-domain match."""
    return bool(FINANCIAL_WORDS.search(subject.replace("_", " ")))


def financial_attachments(message: dict) -> list[dict]:
    """Include root/nested financial files and exclude decorative inline images."""
    payload = message.get("payload", {})
    headers = {h["name"].lower(): h.get("value", "")
               for h in payload.get("headers", [])}
    financial_subject = has_financial_subject(headers.get("subject", ""))
    found = []
    pending = [payload]
    while pending:
        part = pending.pop()
        pending.extend(reversed(part.get("parts", [])))
        filename = part.get("filename", "")
        body = part.get("body", {})
        ext = os.path.splitext(filename.lower())[1]
        if ext not in EXTENSIONS or not body.get("attachmentId"):
            continue
        named_document = has_financial_subject(filename)
        if not financial_subject and not named_document:
            continue
        if ext != ".pdf":
            part_headers = {h["name"].lower(): h.get("value", "")
                            for h in part.get("headers", [])}
            inline = part_headers.get("content-disposition", "").lower().startswith("inline")
            inline = inline or "content-id" in part_headers
            if DECORATIVE_NAME.search(filename) or (inline and not named_document):
                continue
        found.append({"filename": filename, "attachment_id": body["attachmentId"],
                      "mime_type": part.get("mimeType", "application/octet-stream"),
                      "size": body.get("size", 0)})
    return found
