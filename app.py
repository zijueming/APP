import logging
import os
import threading
import time
import webbrowser

from flask import Flask, send_from_directory
from flask_cors import CORS

import db_manager
from routes.literature_routes import literature_bp


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def create_app() -> Flask:
    app = Flask(__name__)
    CORS(app)
    register_routes(app)
    return app


def register_routes(app: Flask):
    app.register_blueprint(literature_bp)

    @app.route("/")
    def serve_index():
        logging.info("Serving index.html")
        return send_from_directory(".", "index.html")

    @app.route("/assets/<path:filename>")
    def serve_assets(filename: str):
        """
        Serve static assets (e.g., zhanshi.jpg) from the project root.
        """
        return send_from_directory(".", filename)


def open_browser():
    time.sleep(1)
    logging.info("Opening browser to http://localhost:5000")
    webbrowser.open_new_tab("http://localhost:5000")


app = create_app()


if __name__ == "__main__":
    db_manager.setup_database()
    logging.info("Starting Flask server...")

    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        threading.Timer(1, open_browser).start()

    app.run(host="0.0.0.0", port=5000, debug=True)
