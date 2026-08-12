#!/usr/bin/env python3
"""Small local server for the three-step PNG -> ProjectMER workflow."""
from __future__ import annotations

import argparse
import base64
import io
import json
import re
import subprocess
import sys
import threading
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
CONVERTER = TOOLS / "layered_emblem_to_mer.py"
INDEX = ROOT / "webapp" / "index.html"
OUTPUT_DIR = ROOT / "webapp" / "_output"
INPUT_DIR = ROOT / "webapp" / "_input"
MAX_UPLOAD_BYTES = 40 * 1024 * 1024
NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def clean_name(raw: str) -> str:
    return NAME_RE.sub("-", (raw or "").strip()).strip("-") or "emblem"


def save_upload(image_bytes: bytes, filename: str, name: str) -> Path:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename or "").suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        suffix = ".png"
    path = INPUT_DIR / f"{name}{suffix}"
    path.write_bytes(image_bytes)
    return path


def analyze_layers(image_bytes: bytes, filename: str) -> dict:
    # Imports stay local so `--help` and server startup remain lightweight.
    import numpy as np
    from PIL import Image

    sys.path.insert(0, str(TOOLS))
    import trace_svg

    name = clean_name(Path(filename or "emblem").stem)
    image_path = save_upload(image_bytes, filename, name)
    image = np.array(Image.open(image_path).convert("RGB")).astype(float)
    height, width = image.shape[:2]
    config = trace_svg.auto_config(image, k=4)
    labels, names = trace_svg.classify(image, config["centroids"])
    counts = np.bincount(labels.ravel(), minlength=len(names))
    background = config["background"]
    foreground_total = max(1, sum(int(counts[i]) for i, key in enumerate(names) if key != background))

    layers = []
    for layer_name, (color, order, _tolerance, mode) in sorted(
        config["layers"].items(), key=lambda item: item[1][1]
    ):
        index = names.index(layer_name)
        layers.append({
            "name": layer_name,
            "color": color,
            "order": order,
            "mode": mode,
            "share": round(100 * int(counts[index]) / foreground_total, 1),
        })
    return {
        "ok": True,
        "name": name,
        "width": width,
        "height": height,
        "background": "#%02X%02X%02X" % tuple(config["centroids"][background]),
        "layers": layers,
    }


def convert_layered(image_bytes: bytes, filename: str, params: dict) -> dict:
    name = clean_name(params.get("name") or Path(filename or "emblem").stem)
    quality = max(1, min(100, int(params.get("quality", 70))))
    image_path = save_upload(image_bytes, filename, name)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    argv = [
        sys.executable,
        str(CONVERTER),
        str(image_path),
        "--name", name,
        "--output", str(OUTPUT_DIR),
        "--mesh-preview",
        "--quality", str(quality),
    ]
    proc = subprocess.run(argv, capture_output=True, text=True, cwd=str(ROOT))
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    if proc.returncode != 0:
        lines = (stderr.strip() or stdout.strip() or "conversion failed").splitlines()
        return {"ok": False, "error": "\n".join(lines[-8:])}

    folder = OUTPUT_DIR / name
    json_path = folder / f"{name}.json"
    mesh_path = folder / f"{name}.mesh.svg"
    if not json_path.exists() or not mesh_path.exists():
        return {"ok": False, "error": "Converter did not create the expected output files."}

    blocks = json.loads(json_path.read_text("utf-8")).get("Blocks", [])
    primitive_count = sum(block.get("BlockType") == 1 for block in blocks)
    match = re.search(r"Triangulation: (\d+) source triangles", stdout)
    triangles = int(match.group(1)) if match else None
    return {
        "ok": True,
        "name": name,
        "quality": quality,
        "blocks": primitive_count,
        "triangles": triangles,
        "mesh": base64.b64encode(mesh_path.read_bytes()).decode("ascii"),
        "download": f"/download?name={name}",
    }


def make_zip(name: str) -> bytes | None:
    folder = (OUTPUT_DIR / name).resolve()
    json_path = folder / f"{name}.json"
    if OUTPUT_DIR.resolve() not in folder.parents or not json_path.is_file():
        return None
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(json_path, arcname=f"{name}/{name}.json")
    return buffer.getvalue()


def decode_upload(payload: dict) -> tuple[bytes, str]:
    encoded = payload.get("image_b64") or ""
    if "," in encoded:
        encoded = encoded.split(",", 1)[1]
    image_bytes = base64.b64decode(encoded)
    if not image_bytes:
        raise ValueError("Choose an image first.")
    return image_bytes, payload.get("filename", "")


class Handler(BaseHTTPRequestHandler):
    server_version = "Meshmark/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))

    def send_bytes(self, code: int, body: bytes, content_type: str, headers=None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, code: int, value: dict):
        self.send_bytes(code, json.dumps(value).encode("utf-8"), "application/json")

    def do_GET(self):
        from urllib.parse import parse_qs, urlparse

        request_path = urlparse(self.path).path
        if request_path in {"/", "/index.html"}:
            self.send_bytes(200, INDEX.read_bytes(), "text/html; charset=utf-8")
            return
        if request_path == "/sample":
            self.send_bytes(200, (ROOT / "nu22.png").read_bytes(), "image/png")
            return
        if request_path == "/health":
            self.send_json(200, {"ok": True})
            return
        if request_path == "/download":
            name = clean_name((parse_qs(urlparse(self.path).query).get("name") or [""])[0])
            data = make_zip(name)
            if data is None:
                self.send_bytes(404, b"not found", "text/plain")
                return
            self.send_bytes(200, data, "application/zip", {
                "Content-Disposition": f'attachment; filename="{name}.zip"'
            })
            return
        self.send_bytes(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path not in {"/analyze", "/convert"}:
            self.send_bytes(404, b"not found", "text/plain")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_UPLOAD_BYTES:
                raise ValueError("The upload is empty or larger than 40 MB.")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            image_bytes, filename = decode_upload(payload)
            result = (
                analyze_layers(image_bytes, filename)
                if self.path == "/analyze"
                else convert_layered(image_bytes, filename, payload.get("params", {}))
            )
            self.send_json(200 if result.get("ok") else 400, result)
        except Exception as error:
            self.send_json(400, {"ok": False, "error": f"{type(error).__name__}: {error}"})


def main() -> None:
    parser = argparse.ArgumentParser(description="Local PNG to ProjectMER web tool.")
    parser.add_argument("--port", type=int, default=8731)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Meshmark is running at {url}")
    print("Press Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
        server.shutdown()


if __name__ == "__main__":
    main()
