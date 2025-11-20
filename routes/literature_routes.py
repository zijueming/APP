import logging
import os

from flask import Blueprint, jsonify, request, send_from_directory

from services.literature_service import LiteratureService, LiteratureServiceError
from db_manager import LiteratureRepository
from analysis_core import AnalysisService

literature_bp = Blueprint("literature", __name__)

# Dependency Injection Wiring
repository = LiteratureRepository()
analyzer = AnalysisService()
service = LiteratureService(analyzer=analyzer, repository=repository)

logger = logging.getLogger(__name__)


def _execute(operation, default_status=200):
    try:
        result = operation()
        if isinstance(result, tuple) and len(result) == 2:
            payload, status_code = result
        else:
            payload, status_code = result, default_status
        return jsonify(payload), status_code
    except LiteratureServiceError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    except Exception as exc:
        logger.exception("Unexpected error")
        return jsonify({"error": "Internal Server Error"}), 500


@literature_bp.route("/api/literature", methods=["GET"])
def list_literature():
    return _execute(lambda: service.list_literature())


@literature_bp.route("/api/literature/<paper_id>", methods=["GET"])
def get_literature(paper_id):
    return _execute(lambda: service.get_literature(paper_id))


@literature_bp.route("/api/literature/<paper_id>", methods=["DELETE"])
def delete_literature(paper_id):
    return _execute(lambda: (service.delete_literature(paper_id), 204))


@literature_bp.route("/api/upload", methods=["POST"])
def upload_literature():
    api_key = service.parse_api_key(request.headers.get("Authorization"))
    file = request.files.get("file")
    return _execute(lambda: (service.process_upload(file, api_key), 201))


@literature_bp.route("/api/literature/<paper_id>/tags", methods=["POST"])
def add_tag(paper_id):
    tag = request.json.get("tag")
    return _execute(lambda: service.add_tag(paper_id, tag))


@literature_bp.route("/api/literature/<paper_id>/tags/<tag>", methods=["DELETE"])
def remove_tag(paper_id, tag):
    return _execute(lambda: service.remove_tag(paper_id, tag))


@literature_bp.route("/api/tags", methods=["GET"])
def list_tags():
    return _execute(lambda: service.list_tags())


@literature_bp.route("/api/tags/stats", methods=["GET"])
def list_tag_stats():
    return _execute(lambda: service.list_tag_stats())


@literature_bp.route("/api/tags/rename", methods=["PUT"])
def rename_tag():
    payload = request.json or {}
    old_tag = payload.get("old_tag")
    new_tag = payload.get("new_tag")
    return _execute(lambda: service.rename_tag(old_tag, new_tag))


@literature_bp.route("/api/tags/<tag>", methods=["DELETE"])
def delete_tag(tag):
    return _execute(lambda: service.delete_tag(tag))


@literature_bp.route("/api/literature/<paper_id>/images/metadata", methods=["GET"])
def get_image_metadata(paper_id):
    return _execute(lambda: {"metadata": service.get_image_metadata(paper_id)})


@literature_bp.route("/api/literature/<paper_id>/images/metadata", methods=["PUT"])
def update_image_metadata(paper_id):
    payload = request.json.get("metadata")
    return _execute(lambda: {"metadata": service.update_image_metadata(paper_id, payload)})


@literature_bp.route("/api/literature/<paper_id>/reading_time", methods=["POST"])
def update_reading_time(paper_id):
    reading_time = request.json.get("reading_time")
    return _execute(lambda: service.update_reading_time(paper_id, reading_time))


@literature_bp.route("/api/literature/<paper_id>/images/<filename>", methods=["GET"])
def serve_image(paper_id, filename):
    try:
        directory, safe_filename = service.resolve_image_request(paper_id, filename)
        return send_from_directory(directory, safe_filename)
    except LiteratureServiceError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    except Exception as exc:
        logger.exception("Failed to serve image")
        return jsonify({"error": "Failed to serve image"}), 500


@literature_bp.route("/api/literature/<paper_id>/pdf", methods=["GET"])
def serve_pdf(paper_id):
    try:
        pdf_path = service.get_pdf_path(paper_id)
        directory = os.path.dirname(pdf_path)
        filename = os.path.basename(pdf_path)
        return send_from_directory(directory, filename)
    except LiteratureServiceError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    except Exception as exc:
        logger.exception("Failed to serve PDF")
        return jsonify({"error": "Failed to serve PDF"}), 500

@literature_bp.route("/api/literature/<paper_id>/metadata", methods=["PUT"])
def update_basic_metadata(paper_id):
    metadata = request.json
    return _execute(lambda: service.update_basic_metadata(paper_id, metadata))
