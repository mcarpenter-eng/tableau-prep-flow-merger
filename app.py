"""
Web UI for merging Tableau Prep flow actions.

Local dev:
    python3.13 app.py
    # then open http://localhost:5050

Production (Heroku, etc.) is served by gunicorn — see Procfile.
"""
import io
import json
import os
from pathlib import Path

from flask import Flask, render_template, request, send_file, jsonify

from merge_flow import merge_actions, read_flow_bytes, write_flow_bytes


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB per request


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/merge", methods=["POST"])
def merge():
    src_file = request.files.get("source")
    dst_file = request.files.get("destination")
    if not src_file or not dst_file:
        return jsonify({"error": "Both a source and destination file are required."}), 400

    try:
        source, _, _ = read_flow_bytes(src_file.read())
        destination, dst_archive, dst_entry = read_flow_bytes(dst_file.read())
    except (json.JSONDecodeError, ValueError) as e:
        return jsonify({"error": f"Could not parse flow file: {e}"}), 400

    merged, log = merge_actions(source, destination)
    out_bytes = write_flow_bytes(merged, dst_archive, dst_entry)

    is_tfl = dst_archive is not None
    dst_name = Path(dst_file.filename or "destination").stem
    suffix = "tfl" if is_tfl else "json"
    download_name = f"{dst_name}.merged.{suffix}"
    mimetype = "application/octet-stream" if is_tfl else "application/json"

    response = send_file(
        io.BytesIO(out_bytes),
        mimetype=mimetype,
        as_attachment=True,
        download_name=download_name,
    )
    # Surface the merge log so the UI can show it next to the download.
    response.headers["X-Merge-Log"] = json.dumps(log)
    return response


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=False)
