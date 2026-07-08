"""Rasterize the merged nu22 schematic at source resolution and score colour
agreement against nu22.png. Emits an overall + per-colour match and writes a
full-res render for eyeballing."""
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
CENTS = np.array([(0, 0, 0), (246, 247, 247), (24, 55, 111), (110, 154, 87)], float)
NAMES = ["black", "white", "blue", "green"]


def vec(d):
    return np.array([d["x"], d["y"], d["z"]], float)


def rotz(deg):
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], float)


def hexcol(s):
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def classify(a):
    d = ((a.reshape(-1, 1, 3).astype(float) - CENTS.reshape(1, -1, 3)) ** 2).sum(2)
    return d.argmin(1).reshape(a.shape[:2])


def render(json_path, W, H):
    data = json.loads(Path(json_path).read_text())
    blocks = data["Blocks"]
    by_id = {b["ObjectId"]: b for b in blocks}
    unit = np.array([[-0.5, -0.5, 0], [0.5, -0.5, 0], [0.5, 0.5, 0], [-0.5, 0.5, 0]], float)
    quads = []
    for b in blocks:
        if b.get("BlockType") != 1:
            continue
        col = (b.get("Properties") or {}).get("Color", "#FFFFFFFF")
        pos, rot, scale = vec(b["Position"]), vec(b["Rotation"]), vec(b["Scale"])
        parent = by_id.get(b["ParentId"])
        z = pos[2]
        world = []
        for cn in unit:
            p = rotz(rot[2]) @ (cn * scale) + pos
            if parent is not None and parent["ObjectId"] != 0:
                p = rotz(vec(parent["Rotation"])[2]) @ (p * vec(parent["Scale"])) + vec(parent["Position"])
                z = pos[2] + parent["Position"]["z"]
            world.append(p[:2])
        quads.append((z, hexcol(col), world))
    xs = [p[0] for _, _, w in quads for p in w]
    ys = [p[1] for _, _, w in quads for p in w]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    sc = min(W / (maxx - minx), H / (maxy - miny)) * 0.98
    ox = (W - (maxx - minx) * sc) / 2
    oy = (H - (maxy - miny) * sc) / 2
    img = Image.new("RGB", (W, H), (0, 0, 0))
    dr = ImageDraw.Draw(img)
    for z, col, w in sorted(quads, key=lambda q: -q[0]):
        pts = [(ox + (p[0] - minx) * sc, H - oy - (p[1] - miny) * sc) for p in w]
        dr.polygon(pts, fill=col)
    return np.array(img)


def main(json_path):
    src = np.array(Image.open(ROOT / "nu22.png").convert("RGB"))
    H, W = src.shape[:2]
    ren = render(json_path, W, H)
    Image.fromarray(ren).save(ROOT / "_render_full.png")
    cs, cr = classify(src), classify(ren)
    fg = cs != 0
    agree = ((cs == cr) & fg).sum() / fg.sum()
    print(f"foreground colour agreement: {agree*100:.1f}%")
    for i, n in enumerate(NAMES):
        m = cs == i
        if m.sum():
            print(f"  {n}: src {m.sum():7d}  matched {100*((cr==i)&m).sum()/m.sum():5.1f}%")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ROOT / "converted_mer/nu22-opt/nu22-opt.json")
