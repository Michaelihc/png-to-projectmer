#!/usr/bin/env python3
"""Small local server for the three-step PNG -> ProjectMER workflow."""
from __future__ import annotations

import argparse
import base64
import hashlib
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
CACHE_VERSION = 2


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
    image = np.array(Image.open(image_path).convert("RGBA"))
    height, width = image.shape[:2]
    config = trace_svg.auto_config(image, k=4)
    labels, names = trace_svg.classify(image, config["centroids"])
    _rgb, visible = trace_svg.image_rgb_and_visible(image)
    counts = np.bincount(labels[visible], minlength=len(names))
    visible_total = max(1, int(visible.sum()))

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
            "share": round(100 * int(counts[index]) / visible_total, 1),
        })
    return {
        "ok": True,
        "name": name,
        "width": width,
        "height": height,
        "layers": layers,
    }


def convert_layered(image_bytes: bytes, filename: str, params: dict) -> dict:
    name = clean_name(params.get("name") or Path(filename or "emblem").stem)
    raw_layer_qualities = params.get("layer_qualities") or {}
    if not isinstance(raw_layer_qualities, dict) or len(raw_layer_qualities) > 16:
        raise ValueError("layer_qualities must be an object with at most 16 layers")
    layer_qualities = {}
    for layer_name, value in raw_layer_qualities.items():
        layer_name = str(layer_name)
        if not layer_name or len(layer_name) > 80 or any(ord(char) < 32 for char in layer_name):
            raise ValueError("invalid layer name")
        layer_qualities[layer_name] = max(1, min(100, int(value)))
    if not layer_qualities:
        raise ValueError("No layers were supplied.")
    raw_active_layers = params.get("active_layers")
    if raw_active_layers is None:
        active_layers = list(layer_qualities)
    else:
        if not isinstance(raw_active_layers, list):
            raise ValueError("active_layers must be a list")
        active_set = {str(layer_name) for layer_name in raw_active_layers}
        unknown_active = active_set - set(layer_qualities)
        if unknown_active:
            raise ValueError(f"unknown active layer(s): {', '.join(sorted(unknown_active))}")
        active_layers = [layer_name for layer_name in layer_qualities if layer_name in active_set]
    if not active_layers:
        raise ValueError("Keep at least one layer included.")
    image_path = save_upload(image_bytes, filename, name)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    folder = OUTPUT_DIR / name
    cache_root = folder / "_layers"
    cache_root.mkdir(parents=True, exist_ok=True)
    source_hash = hashlib.sha256(image_bytes).hexdigest()
    rebuilt_layers = []

    def cache_paths(layer_name):
        safe_layer = clean_name(layer_name)
        cache_name = clean_name(f"{name}-cache-{safe_layer}")
        cache_folder = cache_root / cache_name
        return (
            cache_name,
            cache_folder,
            cache_folder / f"{cache_name}.json",
            cache_folder / f"{cache_name}.{safe_layer}.layer.png",
            cache_folder / f"{cache_name}.stats.json",
            cache_folder / "cache.json",
        )

    for layer_name, quality in layer_qualities.items():
        cache_name, cache_folder, cache_json, cache_preview, cache_stats, cache_meta = cache_paths(layer_name)
        expected_meta = {
            "version": CACHE_VERSION,
            "source": source_hash,
            "layer": layer_name,
            "quality": quality,
        }
        actual_meta = None
        if cache_meta.is_file():
            try:
                actual_meta = json.loads(cache_meta.read_text("utf-8"))
            except Exception:
                pass
        if (
            actual_meta == expected_meta
            and cache_json.is_file()
            and cache_preview.is_file()
            and cache_stats.is_file()
        ):
            continue
        argv = [
            sys.executable,
            str(CONVERTER),
            str(image_path),
            "--name", cache_name,
            "--output", str(cache_root),
            "--only-layer", layer_name,
            "--layer-quality", f"{layer_name}={quality}",
            "--layer-previews",
        ]
        proc = subprocess.run(argv, capture_output=True, text=True, cwd=str(ROOT))
        if proc.returncode != 0:
            message = (proc.stderr.strip() or proc.stdout.strip() or "conversion failed").splitlines()
            return {"ok": False, "error": "\n".join(message[-8:])}
        cache_meta.write_text(json.dumps(expected_meta, separators=(",", ":")), "utf-8")
        rebuilt_layers.append(layer_name)

    combined_blocks = []
    layer_stats = {}
    layer_previews = {}
    separate_exports = []
    next_object_id = 1
    for layer_name in layer_qualities:
        _cache_name, _cache_folder, cache_json, cache_preview, cache_stats, _cache_meta = cache_paths(layer_name)
        layer_blocks = json.loads(cache_json.read_text("utf-8")).get("Blocks", [])
        stored_stats = json.loads(cache_stats.read_text("utf-8")).get("layers", {}).get(layer_name, {})
        layer_stats[layer_name] = {
            "source_triangles": int(stored_stats.get("source_triangles", 0)),
            "quad_primitives": sum(block.get("BlockType") == 1 for block in layer_blocks),
            "objects": len(layer_blocks),
        }
        layer_previews[layer_name] = base64.b64encode(cache_preview.read_bytes()).decode("ascii")

    for layer_number, layer_name in enumerate(active_layers, 1):
        _cache_name, _cache_folder, cache_json, _cache_preview, _cache_stats, _cache_meta = cache_paths(layer_name)
        layer_blocks = json.loads(cache_json.read_text("utf-8")).get("Blocks", [])
        id_map = {}
        for block in layer_blocks:
            old_id = int(block.get("ObjectId", 0))
            if old_id != 0:
                id_map[old_id] = next_object_id
                next_object_id += 1
        for block in layer_blocks:
            merged_block = json.loads(json.dumps(block))
            old_id = int(merged_block.get("ObjectId", 0))
            old_parent = int(merged_block.get("ParentId", 0))
            if old_id != 0:
                merged_block["ObjectId"] = id_map[old_id]
            merged_block["ParentId"] = 0 if old_parent == 0 else id_map[old_parent]
            combined_blocks.append(merged_block)

        export_name = clean_name(f"{name}-layer-{layer_number}")
        export_folder = folder / export_name
        export_folder.mkdir(parents=True, exist_ok=True)
        export_path = export_folder / f"{export_name}.json"
        export_path.write_text(
            json.dumps({"RootObjectId": 0, "Blocks": layer_blocks}, separators=(",", ":")),
            encoding="utf-8",
        )
        separate_exports.append(str(export_path.relative_to(folder)))

    json_path = folder / f"{name}.json"
    json_path.write_text(
        json.dumps({"RootObjectId": 0, "Blocks": combined_blocks}, separators=(",", ":")),
        encoding="utf-8",
    )
    preview_path = folder / f"{name}.preview.png"
    render = subprocess.run(
        [sys.executable, str(TOOLS / "render_preview.py"), str(json_path), str(folder / name), "1000"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    if render.returncode != 0 or not preview_path.exists():
        message = (render.stderr.strip() or render.stdout.strip() or "preview failed").splitlines()
        return {"ok": False, "error": "\n".join(message[-8:])}
    (folder / f"{name}.exports.json").write_text(
        json.dumps({"separate": separate_exports}, separators=(",", ":")),
        encoding="utf-8",
    )
    return {
        "ok": True,
        "name": name,
        "layer_qualities": layer_qualities,
        "active_layers": active_layers,
        "layer_stats": layer_stats,
        "layer_previews": layer_previews,
        "rebuilt_layers": rebuilt_layers,
        "source_triangles": sum(layer_stats[layer_name]["source_triangles"] for layer_name in active_layers),
        "quad_primitives": sum(layer_stats[layer_name]["quad_primitives"] for layer_name in active_layers),
        "blocks": sum(layer_stats[layer_name]["objects"] for layer_name in active_layers),
        "preview": base64.b64encode(preview_path.read_bytes()).decode("ascii"),
        "download_combined": f"/download?name={name}&mode=combined",
        "download_separated": f"/download?name={name}&mode=separated",
    }


def make_zip(name: str, mode: str = "combined") -> bytes | None:
    folder = (OUTPUT_DIR / name).resolve()
    json_path = folder / f"{name}.json"
    if OUTPUT_DIR.resolve() not in folder.parents or not json_path.is_file():
        return None
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        if mode == "combined":
            archive.write(json_path, arcname=f"{name}/{name}.json")
        elif mode == "separated":
            manifest_path = folder / f"{name}.exports.json"
            if not manifest_path.is_file():
                return None
            manifest = json.loads(manifest_path.read_text("utf-8"))
            for relative_path in manifest.get("separate", []):
                export_path = (folder / relative_path).resolve()
                if folder not in export_path.parents or not export_path.is_file():
                    return None
                archive.write(export_path, arcname=str(Path(relative_path)).replace("\\", "/"))
        else:
            return None
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
    server_version = "ImageToPMer/1.0"

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
            query = parse_qs(urlparse(self.path).query)
            name = clean_name((query.get("name") or [""])[0])
            mode = (query.get("mode") or ["combined"])[0]
            data = make_zip(name, mode)
            if data is None:
                self.send_bytes(404, b"not found", "text/plain")
                return
            self.send_bytes(200, data, "application/zip", {
                "Content-Disposition": f'attachment; filename="{name}-{mode}.zip"'
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
    print(f"Image to PMer is running at {url}")
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
