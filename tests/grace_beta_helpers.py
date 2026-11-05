"""Loopback-only beta fixtures: actual artifacts, never invented live receipts."""
import hashlib
import json
from pathlib import Path
from test_webhook import Receiver
from benchlink_adapter.results import post_callback


class ArtifactReceiver(Receiver):
    def __init__(self, directory, **kwargs):
        super().__init__(**kwargs)
        self.directory = Path(directory)
        self.directory.mkdir(exist_ok=True)
        self.artifacts = []
        self.drive = None
        outer = self
        class Handler(self.srv.RequestHandlerClass):
            def do_GET(self):
                found = next((a for a in outer.artifacts if a["path"] == self.path), None)
                data = Path(found["file"]).read_bytes() if found else b"not found"
                self.send_response(200 if found else 404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers(); self.wfile.write(data)
        self.srv.RequestHandlerClass = Handler

    def be_bot(self, dispatch):
        task = dispatch["task"]
        data = ("LOCAL SYNTHETIC artifact\n" + task["task_id"] + "\n" + task["session_id"] + "\n" + dispatch["request_id"] + "\n").encode()
        path = "/artifacts/" + task["task_id"] + ".txt"
        file = self.directory / (task["task_id"] + ".txt")
        file.write_bytes(data)
        url = self.url.rsplit("/", 1)[0] + path
        self.artifacts.append({"path": path, "file": str(file), "url": url, "sha256": hashlib.sha256(data).hexdigest()})
        result = dict(dispatch["result_template"], status="succeeded", summary="Local synthetic artifact created",
                      artifact_links=[{"title": "Local fixture receipt", "url": url}], error_class=None,
                      created_at="2026-11-05T20:00:00Z")
        delivery = dispatch["result_delivery"]
        if delivery["mode"] == "http_callback":
            post_callback(delivery, result)
        elif delivery["mode"] == "drive_folder":
            self.drive.upload(delivery["drive_folder_id"], delivery["file_name"], json.dumps(result).encode())
        else:
            (Path(delivery["folder"]) / delivery["file_name"]).write_text(json.dumps(result))
