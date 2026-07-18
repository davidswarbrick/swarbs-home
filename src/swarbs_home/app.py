"""Flask application: landing page + recorder UI + JSON API."""

from __future__ import annotations

from pathlib import Path

from flask import (
    Flask,
    abort,
    jsonify,
    render_template,
    request,
    send_from_directory,
)

from . import player
from .config import Config, load_config
from .recorder import Recorder, RecorderError


_FONT_FORMATS = {".woff2": "woff2", ".woff": "woff", ".ttf": "truetype", ".otf": "opentype"}


def create_app(config: Config | None = None) -> Flask:
    cfg = config or load_config()
    app = Flask(__name__)
    app.config["SWARBS"] = cfg
    recorder = Recorder(cfg.recorder)

    def font_ctx():
        """Custom-font info for the templates, or None to use the system stack."""
        family = (cfg.server.get("font_family") or "").strip()
        raw = (cfg.server.get("font_file") or "").strip()
        if not (family and raw):
            return None
        path = Path(raw).expanduser()
        if not path.is_file():
            return None
        return {"family": family, "url": "/font",
                "format": _FONT_FORMATS.get(path.suffix.lower())}

    @app.route("/")
    def index():
        return render_template("index.html", title=cfg.server["title"],
                               cards=cfg.cards, font=font_ctx())

    @app.route("/recorder")
    def recorder_page():
        return render_template("recorder.html", title=cfg.server["title"],
                               font=font_ctx())

    @app.route("/font")
    def font():
        raw = (cfg.server.get("font_file") or "").strip()
        if not raw:
            abort(404)
        path = Path(raw).expanduser()
        if not path.is_file():
            abort(404)
        return send_from_directory(path.parent, path.name)

    @app.route("/api/status")
    def api_status():
        status = recorder.status()
        status["mpd"] = player.available()
        return jsonify(status)

    @app.route("/api/play", methods=["POST"])
    def api_play():
        data = request.get_json(silent=True) or request.form
        name = (data.get("name") or "").strip()
        if not name.endswith(".flac") or "/" in name:
            return jsonify(ok=False, error="invalid file"), 400
        path = Path(recorder.mixes_dir) / name
        if not path.is_file():
            return jsonify(ok=False, error="file not found"), 404
        try:
            player.play_file(str(path))
        except player.PlayerError as exc:
            return jsonify(ok=False, error=str(exc)), 400
        return jsonify(ok=True)

    @app.route("/api/start", methods=["POST"])
    def api_start():
        data = request.get_json(silent=True) or request.form
        label = (data.get("label") or "").strip() or None
        try:
            name = recorder.start(label)
        except RecorderError as exc:
            return jsonify(ok=False, error=str(exc)), 400
        return jsonify(ok=True, filename=name)

    @app.route("/api/stop", methods=["POST"])
    def api_stop():
        try:
            recorder.stop()
        except RecorderError as exc:
            return jsonify(ok=False, error=str(exc)), 400
        return jsonify(ok=True)

    @app.route("/media/<path:filename>")
    def media(filename):
        if not filename.endswith(".flac"):
            abort(404)
        directory = Path(recorder.mixes_dir)
        if not (directory / filename).is_file():
            abort(404)
        return send_from_directory(directory, filename, as_attachment=True)

    return app
