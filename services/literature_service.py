from __future__ import annotations

import logging
import os
import tempfile
import uuid
from contextlib import contextmanager
from typing import Any, Dict, List, Tuple

from werkzeug.datastructures import FileStorage

import analysis_core
import db_manager


class LiteratureServiceError(Exception):
    """
    Base exception that carries an HTTP-friendly status code.
    """

    default_status = 400

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code or self.default_status


class AuthorizationError(LiteratureServiceError):
    default_status = 401


class InvalidUploadError(LiteratureServiceError):
    default_status = 400


class AnalysisFailure(LiteratureServiceError):
    default_status = 500


class NotFoundError(LiteratureServiceError):
    default_status = 404


class LiteratureService:
    """
    Encapsulates all business logic around PDF ingestion, analysis,
    persistence and metadata management so that route handlers remain slim.
    """

    ALLOWED_IMAGE_CATEGORIES = {"figure", "subfigure", "cover", "ignore"}

    def __init__(self, analyzer=analysis_core, repository=db_manager):
        self._log = logging.getLogger(self.__class__.__name__)
        self.analyzer = analyzer
        self.repository = repository

    # ------------------------------------------------------------------ #
    # Public API for routes
    # ------------------------------------------------------------------ #
    def parse_api_key(self, auth_header: str | None) -> str:
        if not auth_header or not auth_header.startswith("Bearer "):
            raise AuthorizationError("Missing Authorization Header")
        token = auth_header.split(" ", 1)[1].strip()
        if not token:
            raise AuthorizationError("API Key missing in Authorization header")
        return token

    def list_literature(self):
        return self.repository.get_all_literature_summaries()

    def get_literature(self, paper_id: str):
        data = self.repository.get_literature_by_id(paper_id)
        if not data:
            raise NotFoundError(f"Record {paper_id} not found")
        return data

    def delete_literature(self, paper_id: str):
        self.repository.delete_literature_by_id(paper_id)

    def list_tags(self):
        return self.repository.get_all_tags()

    def add_tag(self, paper_id: str, tag: str):
        try:
            return self.repository.add_tag_to_literature(paper_id, tag)
        except FileNotFoundError:
            raise NotFoundError(f"Record {paper_id} not found")

    def remove_tag(self, paper_id: str, tag: str):
        try:
            return self.repository.remove_tag_from_literature(paper_id, tag)
        except FileNotFoundError:
            raise NotFoundError(f"Record {paper_id} not found")

    def get_image_metadata(self, paper_id: str):
        record = self.get_literature(paper_id)
        metadata = record.get("image_metadata")
        if not metadata:
            metadata = self._default_image_metadata(
                record.get("image_files", []),
                existing_metadata=None,
            )
            self.repository.update_image_metadata(paper_id, metadata)
        return metadata

    def update_image_metadata(self, paper_id: str, metadata_payload):
        record = self.get_literature(paper_id)
        image_files = record.get("image_files", [])
        existing_metadata = record.get("image_metadata", [])
        normalized = self._normalize_image_metadata_payload(
            metadata_payload,
            image_files,
            existing_metadata,
        )
        self.repository.update_image_metadata(paper_id, normalized)
        return normalized

    def resolve_image_request(self, paper_id: str, filename: str) -> Tuple[str, str]:
        if not filename or ".." in filename or filename.startswith("/"):
            raise InvalidUploadError("Invalid filename")

        image_dir = self.repository.get_paper_dir(paper_id)
        image_path = os.path.join(image_dir, filename)
        if not os.path.exists(image_path):
            raise NotFoundError(f"Image {filename} not found for {paper_id}")

        return image_dir, filename

    def process_upload(self, file_storage: FileStorage | None, api_key: str) -> Dict[str, Any]:
        file_storage = self._validate_pdf(file_storage)

        with self._temporary_pdf(file_storage) as tmp_pdf_path:
            full_text = self.analyzer.extract_text_from_pdf(tmp_pdf_path)
            if not full_text:
                raise AnalysisFailure("Failed to extract text from PDF")

            analysis_result = self.analyzer.analyze_text_with_deepseek(full_text, api_key)
            if not analysis_result or "error" in analysis_result:
                message = analysis_result.get("error") if isinstance(analysis_result, dict) else None
                raise AnalysisFailure(message or "Analysis failed")

            paper_id = str(uuid.uuid4())
            paper_dir = self.repository.get_paper_dir(paper_id)
            image_files = self.analyzer.extract_images_from_pdf(tmp_pdf_path, paper_dir)

            analysis_payload = self._enrich_analysis_payload(
                analysis_result,
                paper_id,
                image_files,
            )

            self.repository.save_new_literature(paper_id, tmp_pdf_path, analysis_payload)

            return self._build_summary(
                analysis_payload,
                fallback_title=file_storage.filename or "未命名文献",
            )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _validate_pdf(self, file_storage: FileStorage | None) -> FileStorage:
        if file_storage is None:
            raise InvalidUploadError("No file provided")

        filename = (file_storage.filename or "").lower()
        if not filename.endswith(".pdf"):
            raise InvalidUploadError("Invalid file (must be a PDF)")
        return file_storage

    @contextmanager
    def _temporary_pdf(self, file_storage: FileStorage):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            file_storage.save(tmp.name)
            tmp_pdf_path = tmp.name

        try:
            yield tmp_pdf_path
        finally:
            if os.path.exists(tmp_pdf_path):
                os.remove(tmp_pdf_path)
                self._log.debug("Removed temp file %s", tmp_pdf_path)

    def _enrich_analysis_payload(
        self,
        analysis_data: Dict[str, Any],
        paper_id: str,
        image_files: list[str],
    ) -> Dict[str, Any]:
        analysis_data = dict(analysis_data)
        analysis_data["paper_id"] = paper_id
        analysis_data["image_files"] = image_files

        custom_tags = analysis_data.get("custom_tags")
        if not isinstance(custom_tags, list):
            custom_tags = []
        analysis_data["custom_tags"] = custom_tags

        metadata = analysis_data.get("image_metadata")
        if not metadata:
            metadata = self._default_image_metadata(image_files, existing_metadata=None)
        analysis_data["image_metadata"] = metadata
        return analysis_data

    def _build_summary(self, analysis_payload: Dict[str, Any], fallback_title: str) -> Dict[str, Any]:
        meta = analysis_payload.get("文献信息", {})
        return {
            "id": analysis_payload.get("paper_id"),
            "title": meta.get("标题") or fallback_title,
            "authors": meta.get("作者", []),
            "year": meta.get("年份", ""),
            "custom_tags": analysis_payload.get("custom_tags", []),
        }

    # ------------------------------------------------------------------ #
    # Image metadata helpers
    # ------------------------------------------------------------------ #
    def _default_image_metadata(
        self,
        image_files: List[str] | None,
        existing_metadata: List[Dict[str, Any]] | None,
    ) -> List[Dict[str, Any]]:
        if not image_files:
            return []

        existing_lookup = {
            item.get("filename"): item for item in (existing_metadata or []) if isinstance(item, dict)
        }

        return [
            self._make_metadata_entry(filename, idx, existing_lookup.get(filename))
            for idx, filename in enumerate(image_files)
        ]

    def _make_metadata_entry(
        self,
        filename: str,
        index: int,
        existing: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        figure_id = ""
        label = ""
        category = "figure"
        if existing:
            figure_id = str(existing.get("figure_id") or "").strip()
            label = str(existing.get("label") or "").strip()
            category = existing.get("category") or category

        if not figure_id:
            figure_id = str(index + 1)

        normalized_category = (
            category if category in self.ALLOWED_IMAGE_CATEGORIES else "figure"
        )

        # Cover/ignore types do not need figure ids.
        if normalized_category in {"cover", "ignore"}:
            figure_id = figure_id if normalized_category == "cover" else ""

        return {
            "filename": filename,
            "figure_id": figure_id,
            "label": label,
            "category": normalized_category,
        }

    def _normalize_image_metadata_payload(
        self,
        payload,
        image_files: List[str] | None,
        existing_metadata: List[Dict[str, Any]] | None,
    ) -> List[Dict[str, Any]]:
        if not image_files:
            return []

        payload = payload or []
        if not isinstance(payload, list):
            raise InvalidUploadError("Image metadata必须是列表")

        provided_map: Dict[str, Dict[str, Any]] = {}
        for item in payload:
            if not isinstance(item, dict):
                raise InvalidUploadError("每个图片元数据项都必须是对象")
            filename = item.get("filename")
            if not filename:
                raise InvalidUploadError("图片元数据缺少文件名")
            provided_map[filename] = {
                "filename": filename,
                "figure_id": str(item.get("figure_id") or "").strip(),
                "label": str(item.get("label") or "").strip(),
                "category": self._normalize_category(item.get("category")),
            }

        existing_lookup = {
            entry.get("filename"): entry for entry in (existing_metadata or []) if isinstance(entry, dict)
        }

        normalized: List[Dict[str, Any]] = []
        cover_seen = False

        for idx, filename in enumerate(image_files):
            base = provided_map.get(filename) or existing_lookup.get(filename)
            entry = self._make_metadata_entry(filename, idx, base)

            if entry["category"] == "cover":
                if cover_seen:
                    raise InvalidUploadError("仅允许设置一张封面图片")
                cover_seen = True
                entry["figure_id"] = ""
            elif entry["category"] in {"figure", "subfigure"} and not entry["figure_id"]:
                entry["figure_id"] = str(idx + 1)

            normalized.append(entry)

        return self._enforce_sequential_figure_ids(normalized)

    def _normalize_category(self, category_value) -> str:
        if not category_value:
            return "figure"
        value = str(category_value).strip().lower()
        if value not in self.ALLOWED_IMAGE_CATEGORIES:
            return "figure"
        return value

    def _enforce_sequential_figure_ids(self, metadata: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Guarantee that figure/subfigure entries have sequential ids while allowing
        subfigures to share their parent figure id.
        """
        next_index = 1
        current_group_id = ""

        for entry in metadata:
            category = entry.get("category") or "figure"
            if category in {"cover", "ignore"}:
                entry["figure_id"] = ""
                current_group_id = ""
                continue

            if category == "subfigure":
                if not current_group_id:
                    current_group_id = str(next_index)
                    next_index += 1
                entry["figure_id"] = current_group_id
                continue

            entry["figure_id"] = str(next_index)
            current_group_id = entry["figure_id"]
            next_index += 1

        return metadata
