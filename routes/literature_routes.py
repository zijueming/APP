import logging

from flask import Blueprint, jsonify, request, send_from_directory

from services.literature_service import LiteratureService, LiteratureServiceError

literature_bp = Blueprint("literature", __name__)
service = LiteratureService()
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
        logger.exception("Unexpected server error")
        return jsonify({"error": "Internal server error"}), 500


@literature_bp.route("/api/literature", methods=["GET"])
def list_literature():
    return _execute(lambda: service.list_literature())


@literature_bp.route("/api/literature/<paper_id>", methods=["GET"])
def get_literature(paper_id):
    return _execute(lambda: service.get_literature(paper_id))


@literature_bp.route("/api/literature/<paper_id>", methods=["DELETE"])
def delete_literature(paper_id):
    def operation():
        service.delete_literature(paper_id)
        return {"success": True, "message": f"Record {paper_id} deleted"}

    return _execute(operation)


@literature_bp.route("/api/upload", methods=["POST"])
def upload_pdf():
    def operation():
        api_key = service.parse_api_key(request.headers.get("Authorization"))
        summary = service.process_upload(request.files.get("file"), api_key)
        return summary, 201

    return _execute(operation, default_status=201)


@literature_bp.route("/api/tags", methods=["GET"])
def list_tags():
    return _execute(lambda: service.list_tags())


@literature_bp.route("/api/literature/<paper_id>/tags", methods=["POST"])
def add_tag(paper_id):
    tag_payload = request.json or {}
    if "tag" not in tag_payload:
        return jsonify({"error": "Missing 'tag' in request body"}), 400

    def operation():
        updated_tags = service.add_tag(paper_id, tag_payload["tag"])
        return {"success": True, "tags": updated_tags}

    return _execute(operation)


@literature_bp.route("/api/literature/<paper_id>/tags/<tag_name>", methods=["DELETE"])
def remove_tag(paper_id, tag_name):
    def operation():
        updated_tags = service.remove_tag(paper_id, tag_name)
        return {"success": True, "tags": updated_tags}

    return _execute(operation)


@literature_bp.route("/api/literature/<paper_id>/images/metadata", methods=["GET"])
def get_image_metadata(paper_id):
    return _execute(lambda: {"metadata": service.get_image_metadata(paper_id)})


@literature_bp.route("/api/literature/<paper_id>/images/metadata", methods=["PUT"])
def update_image_metadata(paper_id):
    payload = request.json or {}
    if "metadata" not in payload:
        return jsonify({"error": "Missing 'metadata' in request body"}), 400

    def operation():
        updated_metadata = service.update_image_metadata(paper_id, payload["metadata"])
        return {"success": True, "metadata": updated_metadata}

    return _execute(operation)


@literature_bp.route("/api/literature/<paper_id>/images/<filename>")
def serve_image(paper_id, filename):
    try:
        directory, safe_filename = service.resolve_image_request(paper_id, filename)
        return send_from_directory(directory, safe_filename)
    except LiteratureServiceError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    except Exception as exc:
        logger.exception("Failed to serve image")
        return jsonify({"error": "Failed to serve image"}), 500
