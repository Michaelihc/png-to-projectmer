#!/usr/bin/env python3
"""Local web UI for the PNG -> vanilla ProjectMER schematic converter.

Standard-library only (no Flask). Start it with run-webapp.bat, or:

    py webapp/server.py [--port 8731] [--no-browser]

It serves a single page that uploads an image, runs
tools/png_to_mer_schematic.py, and shows the preview + object count. Output
schematics are written under webapp/_output/<name>/ and can be downloaded as a
zip ready to drop into LabAPI-beta/configs/ProjectMER/Schematics/.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import re
import shlex
import subprocess
import sys
import threading
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
CONVERTER = TOOLS / "png_to_mer_schematic.py"
WEBAPP = ROOT / "webapp"
INDEX = WEBAPP / "index.html"
OUTPUT_DIR = WEBAPP / "_output"
INPUT_DIR = WEBAPP / "_input"

MAX_UPLOAD_BYTES = 40 * 1024 * 1024  # 40 MB

# param key -> (cli flag, kind). kind: "str" | "num" | "int" | "flag"
PARAM_FLAGS: dict[str, tuple[str, str]] = {
    "color": ("--color", "str"),
    "color_source": ("--color-source", "str"),
    "foreground": ("--foreground", "str"),
    "threshold": ("--threshold", "int"),
    "fill_mode": ("--fill-mode", "str"),
    "simplify": ("--simplify", "num"),
    "min_area": ("--min-area", "num"),
    "width": ("--width", "num"),
    "preview_bg": ("--preview-bg", "str"),
    # advanced
    "min_triangle_area": ("--min-triangle-area", "num"),
    "triangle_winding": ("--triangle-winding", "str"),
    "trace_mode": ("--trace-mode", "str"),
    "trace_source": ("--trace-source", "str"),
    "trace_width_px": ("--trace-width-px", "num"),
    "trace_z": ("--trace-z", "num"),
    "thickness": ("--thickness", "num"),
    "border_circle": ("--border-circle", "str"),
    "border_inner_color": ("--border-inner-color", "str"),
    "border_outer_color": ("--border-outer-color", "str"),
    "border_z": ("--border-z", "num"),
    "border_gap": ("--border-gap", "num"),
    "border_mask_margin": ("--border-mask-margin", "num"),
}
STR_CHOICES = {
    "foreground": {"light", "dark"},
    "color_source": {"flat", "image"},
    "fill_mode": {"ngon", "triangle"},
    "triangle_winding": {"positive", "negative"},
    "trace_mode": {"polygon", "rectangle-first"},
    "trace_source": {"boundary", "centerline"},
}
HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")
NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def clean_name(raw: str) -> str:
    name = NAME_RE.sub("-", (raw or "").strip()).strip("-")
    return name or "converted-logo"


def build_argv(image_path: Path, name: str, params: dict) -> list[str]:
    argv = [sys.executable, str(CONVERTER), str(image_path), "--name", name,
            "--output", str(OUTPUT_DIR), "--preview", "--preview-size", "1024"]

    for key, (flag, kind) in PARAM_FLAGS.items():
        if key not in params:
            continue
        value = params[key]
        if value is None or value == "":
            continue
        if kind in ("str",):
            value = str(value)
            if key in STR_CHOICES and value not in STR_CHOICES[key]:
                raise ValueError(f"invalid value for {key}: {value!r}")
            if key.endswith("color") or key == "preview_bg":
                if not HEX_RE.match(value):
                    raise ValueError(f"{key} must be a #RRGGBB(AA) hex color")
            argv += [flag, value]
        elif kind == "int":
            argv += [flag, str(int(value))]
        elif kind == "num":
            argv += [flag, repr(float(value))]

    if params.get("border_cylinders"):
        argv.append("--border-cylinders")

    extra = params.get("extra_args") or ""
    if extra.strip():
        # advanced escape hatch; split like a shell line
        argv += shlex.split(extra)

    return argv


def run_conversion(image_bytes: bytes, filename: str, params: dict) -> dict:
    name = clean_name(params.get("name") or Path(filename or "logo").stem)
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    suffix = Path(filename or "").suffix.lower()
    if suffix not in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
        suffix = ".png"
    image_path = INPUT_DIR / f"{name}{suffix}"
    image_path.write_bytes(image_bytes)

    argv = build_argv(image_path, name, params)
    proc = subprocess.run(argv, capture_output=True, text=True, cwd=str(ROOT))
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""

    if proc.returncode != 0:
        # surface the useful tail of the traceback / argparse message
        message = (stderr.strip() or stdout.strip() or "conversion failed").splitlines()
        return {"ok": False, "error": "\n".join(message[-6:]), "cmd": " ".join(argv[2:])}

    out_folder = OUTPUT_DIR / name
    json_path = out_folder / f"{name}.json"
    preview_path = out_folder / f"{name}.preview.png"

    toys = None
    m = re.search(r"Runtime primitive toys: about (\d+)", stdout)
    if m:
        toys = int(m.group(1))
    ngon_line = None
    m2 = re.search(r"NGon decomposition: (.+)", stdout)
    if m2:
        ngon_line = m2.group(1).strip()

    block_count = None
    if json_path.exists():
        try:
            block_count = len(json.loads(json_path.read_text("utf-8")).get("Blocks", []))
        except Exception:
            pass

    preview_b64 = None
    if preview_path.exists():
        preview_b64 = base64.b64encode(preview_path.read_bytes()).decode("ascii")

    return {
        "ok": True,
        "name": name,
        "toys": toys if toys is not None else block_count,
        "blocks": block_count,
        "ngon": ngon_line,
        "preview": preview_b64,
        "stdout": stdout.strip(),
        "download": f"/download?name={name}",
    }


def make_zip(name: str) -> bytes | None:
    folder = (OUTPUT_DIR / name).resolve()
    if OUTPUT_DIR.resolve() not in folder.parents or not folder.is_dir():
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(folder.iterdir()):
            if file.is_file():
                zf.write(file, arcname=f"{name}/{file.name}")
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    server_version = "MerConverter/1.0"

    def log_message(self, fmt, *args):  # quieter console
        sys.stderr.write("  %s\n" % (fmt % args))

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, obj: dict):
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            if INDEX.exists():
                self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(500, b"index.html missing", "text/plain")
            return
        if self.path.startswith("/download"):
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            name = clean_name((qs.get("name") or [""])[0])
            data = make_zip(name)
            if data is None:
                self._send(404, b"not found", "text/plain")
                return
            self._send(200, data, "application/zip",
                       {"Content-Disposition": f'attachment; filename="{name}.zip"'})
            return
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path != "/convert":
            self._send(404, b"not found", "text/plain")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_UPLOAD_BYTES:
                self._send_json(400, {"ok": False, "error": "empty or oversized request"})
                return
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            image_b64 = payload.get("image_b64") or ""
            if "," in image_b64:  # strip data URL prefix if present
                image_b64 = image_b64.split(",", 1)[1]
            image_bytes = base64.b64decode(image_b64)
            if not image_bytes:
                self._send_json(400, {"ok": False, "error": "no image data"})
                return
            result = run_conversion(image_bytes, payload.get("filename", ""), payload.get("params", {}))
            self._send_json(200 if result.get("ok") else 400, result)
        except Exception as exc:  # never take the server down on one bad request
            self._send_json(400, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})


def main() -> None:
    parser = argparse.ArgumentParser(description="Local web UI for the MER schematic converter.")
    parser.add_argument("--port", type=int, default=8731)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    if not CONVERTER.exists():
        sys.exit(f"Converter not found: {CONVERTER}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"MER schematic converter running at {url}")
    print("Press Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
        httpd.shutdown()


if __name__ == "__main__":
    main()
