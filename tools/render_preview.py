"""Rasterize a merged schematic to a PNG and SVG for visual QA.

Draws every BlockType 1 (Quad) primitive as a filled polygon, resolving world
position through its parent TRS (shear parents give children local coords).
Back-to-front by Z so front layers occlude, matching the runtime.

Usage: python tools/render_preview.py <schematic.json> [out_stem] [width_px]
Writes <out_stem>.png and <out_stem>.svg (default out_stem = schematic path
without .json).
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


def vec(d):
    return np.array([d["x"], d["y"], d["z"]], float)


def rot_z(deg):
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], float)


def trs_point(local, pos, rot, scale):
    p = np.array(local, float) * scale
    p = rot_z(rot[2]) @ p
    return p + pos


def hexcol(s):
    """Return (r, g, b, a) from #RRGGBB or #RRGGBBAA."""
    s = s.lstrip("#")
    r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    a = int(s[6:8], 16) if len(s) >= 8 else 255
    return (r, g, b, a)


def collect_quads(path):
    """Return (quads, bounds). Each quad is (z, (r,g,b,a), [(x,y)*4])."""
    data = json.loads(Path(path).read_text())
    blocks = data["Blocks"]
    by_id = {b["ObjectId"]: b for b in blocks}
    quads = []
    unit = np.array([[-0.5, -0.5, 0], [0.5, -0.5, 0], [0.5, 0.5, 0], [-0.5, 0.5, 0]], float)
    for b in blocks:
        if b.get("BlockType") != 1:
            continue
        color = (b.get("Properties") or {}).get("Color", "#FFFFFFFF")
        pos, rot, scale = vec(b["Position"]), vec(b["Rotation"]), vec(b["Scale"])
        parent = by_id.get(b["ParentId"])
        world = []
        z_acc = pos[2]
        for corner in unit:
            p = rot_z(rot[2]) @ (corner * scale) + pos
            if parent is not None and parent["ObjectId"] != 0:
                p = trs_point(p, vec(parent["Position"]), vec(parent["Rotation"]), vec(parent["Scale"]))
                z_acc = pos[2] + parent["Position"]["z"]
            world.append((p[0], p[1]))
        quads.append((z_acc, hexcol(color), world))
    xs = [p[0] for _, _, w in quads for p in w]
    ys = [p[1] for _, _, w in quads for p in w]
    return quads, (min(xs), max(xs), min(ys), max(ys))


def write_png(quads, bounds, out, W):
    minx, maxx, miny, maxy = bounds
    scale = W / (maxx - minx)
    H = int(round((maxy - miny) * scale))
    img = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    dr = ImageDraw.Draw(img, "RGBA")
    for z, col, w in sorted(quads, key=lambda q: -q[0]):  # back (least -z) first
        pts = [((p[0] - minx) * scale, H - (p[1] - miny) * scale) for p in w]
        dr.polygon(pts, fill=col)
    img.convert("RGB").save(out)
    return img.size


def write_svg(quads, bounds, out, W):
    minx, maxx, miny, maxy = bounds
    scale = W / (maxx - minx)
    H = (maxy - miny) * scale
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H:.2f}" '
        f'viewBox="0 0 {W} {H:.2f}">',
        f'<rect width="{W}" height="{H:.2f}" fill="#000000"/>',
    ]
    for z, col, w in sorted(quads, key=lambda q: -q[0]):
        pts = " ".join(
            f"{(p[0]-minx)*scale:.2f},{H-(p[1]-miny)*scale:.2f}" for p in w
        )
        r, g, b, a = col
        fill = f"#{r:02X}{g:02X}{b:02X}"
        opacity = "" if a == 255 else f' fill-opacity="{a/255:.3f}"'
        parts.append(f'<polygon points="{pts}" fill="{fill}"{opacity}/>')
    parts.append("</svg>")
    Path(out).write_text("\n".join(parts))
    return (W, H)


def main(path, stem=None, W=800):
    quads, bounds = collect_quads(path)
    if stem is None:
        stem = str(Path(path).with_suffix(""))
    png_out, svg_out = f"{stem}.preview.png", f"{stem}.preview.svg"
    size = write_png(quads, bounds, png_out, W)
    write_svg(quads, bounds, svg_out, W)
    print(f"wrote {png_out} {size} and {svg_out} ({len(quads)} quads)")


if __name__ == "__main__":
    path = sys.argv[1]
    stem = sys.argv[2] if len(sys.argv) > 2 else None
    W = int(sys.argv[3]) if len(sys.argv) > 3 else 800
    main(path, stem, W)
