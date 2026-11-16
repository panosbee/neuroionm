from __future__ import annotations

import io
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import fitz
import pytesseract
from PIL import Image
from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.app.core.config import settings
from backend.app.services.rag import JinaEmbeddingError, index_knowledge_embeddings
from backend.app.services.tree import to_public_document


MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MIN_TEXT_CHARS_FOR_PAGE = 80
CHUNK_SIZE = 2600
CHUNK_OVERLAP = 320


class KnowledgeIngestionError(RuntimeError):
    pass


async def ensure_admin_indexes(database: AsyncIOMotorDatabase) -> None:
    await database.admin_users.create_index("user_id", unique=True)
    await database.admin_users.create_index("email", unique=True)
    await database.admin_users.create_index("username_normalized", unique=True, sparse=True)
    await database.admin_knowledge_documents.create_index("document_id", unique=True)
    await database.admin_knowledge_documents.create_index([("created_at", -1)])
    await database.admin_knowledge_chunks.create_index("chunk_id", unique=True)
    await database.admin_knowledge_chunks.create_index([("document_id", 1), ("chunk_index", 1)])


async def ingest_pdf_document(
    database: AsyncIOMotorDatabase,
    *,
    file_bytes: bytes,
    original_filename: str,
    title: str,
    domain: str,
    tags: list[str],
    uploaded_by: str,
) -> dict[str, Any]:
    if not original_filename.lower().endswith(".pdf"):
        raise KnowledgeIngestionError("Only PDF files are accepted.")
    if not file_bytes:
        raise KnowledgeIngestionError("The uploaded PDF is empty.")
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise KnowledgeIngestionError("The uploaded PDF exceeds the 40 MB limit.")

    await ensure_admin_indexes(database)
    document_id = uuid.uuid4().hex
    upload_path = settings.admin_upload_path
    upload_path.mkdir(parents=True, exist_ok=True)
    stored_filename = f"{document_id}.pdf"
    stored_path = upload_path / stored_filename
    stored_path.write_bytes(file_bytes)

    created_at = datetime.now(UTC)
    base_document = {
        "document_id": document_id,
        "title": title.strip(),
        "domain": domain.strip() or "general",
        "tags": tags,
        "original_filename": Path(original_filename).name,
        "stored_filename": stored_filename,
        "stored_path": str(stored_path),
        "uploaded_by": uploaded_by,
        "created_at": created_at,
        "updated_at": created_at,
        "status": "processing",
        "source": "admin_pdf_upload",
    }
    await database.admin_knowledge_documents.replace_one({"document_id": document_id}, base_document, upsert=True)

    try:
        extraction = extract_pdf_text(file_bytes)
        chunks = build_document_chunks(document_id, title, domain, tags, extraction["text"], original_filename)
        if not chunks:
            raise KnowledgeIngestionError("No usable text was extracted from the PDF.")
    except Exception as error:
        await database.admin_knowledge_documents.update_one(
            {"document_id": document_id},
            {"$set": {"status": "ocr_failed", "error": str(error), "updated_at": datetime.now(UTC)}},
        )
        raise KnowledgeIngestionError(str(error)) from error

    await database.admin_knowledge_chunks.delete_many({"document_id": document_id})
    await database.admin_knowledge_chunks.insert_many(chunks)

    document_update = {
        **base_document,
        "status": "ocr_complete",
        "page_count": extraction["page_count"],
        "text_extraction_page_count": extraction["text_extraction_page_count"],
        "ocr_page_count": extraction["ocr_page_count"],
        "character_count": len(extraction["text"]),
        "chunk_count": len(chunks),
        "updated_at": datetime.now(UTC),
    }
    await database.admin_knowledge_documents.replace_one({"document_id": document_id}, document_update, upsert=True)

    index_result: dict[str, Any] | None = None
    try:
        index_result = await index_knowledge_embeddings(database)
        await database.admin_knowledge_documents.update_one(
            {"document_id": document_id},
            {"$set": {"status": "indexed", "index_result": index_result, "updated_at": datetime.now(UTC)}},
        )
        document_update["status"] = "indexed"
        document_update["index_result"] = index_result
    except JinaEmbeddingError as error:
        await database.admin_knowledge_documents.update_one(
            {"document_id": document_id},
            {"$set": {"status": "ocr_complete_embedding_failed", "embedding_error": str(error), "updated_at": datetime.now(UTC)}},
        )
        document_update["status"] = "ocr_complete_embedding_failed"
        document_update["embedding_error"] = str(error)

    return {"document": public_document(document_update), "index_result": index_result}


def extract_pdf_text(file_bytes: bytes) -> dict[str, Any]:
    if settings.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd
    if settings.tessdata_prefix:
        os.environ["TESSDATA_PREFIX"] = settings.tessdata_prefix

    try:
        pdf = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception as error:
        raise KnowledgeIngestionError("The uploaded file could not be opened as a PDF.") from error

    pages: list[str] = []
    ocr_page_count = 0
    text_extraction_page_count = 0
    try:
        for page_index, page in enumerate(pdf, start=1):
            text = page.get_text("text").strip()
            if len(text) >= MIN_TEXT_CHARS_FOR_PAGE:
                text_extraction_page_count += 1
            else:
                text = ocr_pdf_page(page).strip()
                ocr_page_count += 1
            if text:
                pages.append(f"Page {page_index}\n{text}")
    finally:
        page_count = pdf.page_count
        pdf.close()

    return {
        "text": "\n\n".join(pages).strip(),
        "page_count": page_count,
        "ocr_page_count": ocr_page_count,
        "text_extraction_page_count": text_extraction_page_count,
    }


def ocr_pdf_page(page: fitz.Page) -> str:
    try:
        pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        image = Image.open(io.BytesIO(pixmap.tobytes("png")))
        return pytesseract.image_to_string(image, lang=settings.tesseract_languages)
    except pytesseract.TesseractNotFoundError as error:
        raise KnowledgeIngestionError("Tesseract OCR is not available. Install Tesseract and set TESSERACT_CMD if it is not on PATH.") from error
    except pytesseract.TesseractError as error:
        raise KnowledgeIngestionError(f"Tesseract OCR failed: {error}") from error


def build_document_chunks(
    document_id: str,
    title: str,
    domain: str,
    tags: list[str],
    text: str,
    original_filename: str,
) -> list[dict[str, Any]]:
    clean_text = normalize_text(text)
    chunks = []
    start = 0
    chunk_index = 1
    while start < len(clean_text):
        end = min(len(clean_text), start + CHUNK_SIZE)
        if end < len(clean_text):
            boundary = clean_text.rfind("\n", start, end)
            if boundary > start + 800:
                end = boundary
        chunk_text = clean_text[start:end].strip()
        if chunk_text:
            chunk_id = f"{document_id}:{chunk_index:04d}"
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "document_id": document_id,
                    "chunk_index": chunk_index,
                    "title": title.strip(),
                    "domain": domain.strip() or "general",
                    "tags": tags,
                    "text": chunk_text,
                    "source": Path(original_filename).name,
                    "status": "active",
                    "created_at": datetime.now(UTC),
                }
            )
            chunk_index += 1
        if end >= len(clean_text):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return chunks


def normalize_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def parse_tags(raw_tags: str | None) -> list[str]:
    if not raw_tags:
        return []
    tags = []
    for item in raw_tags.split(","):
        tag = item.strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tags[:12]


def public_document(document: dict[str, Any]) -> dict[str, Any]:
    clean = to_public_document(document)
    clean.pop("stored_path", None)
    return clean
