"""Trace a multi-colour emblem into smooth, layered vector geometry.

This is the *layered* front-end for stacked-colour emblems (e.g. an MTF badge:
blue border, green globe, white figures). A single silhouette can't represent
stacked colours, so we split the image into one region per colour and stack
them back-to-front.

Pipeline per colour layer:
  1. classify every pixel to its nearest palette centroid -> hard binary mask
  2. lightly blur the mask so its 0/1 boundary becomes a smooth ramp
  3. marching-squares contour at level 0.5 -> sub-pixel polylines (no grid
     staircase, unlike tracing the raw pixel edges)
  4. rebuild hole nesting (find_contours returns loops with no nesting info)
  5. simplify with shapely (Douglas-Peucker) to drop redundant vertices
  6. emit as filled SVG polygons, back-to-front.

Two layer modes drive a primitive-saving trick:
  - "silhouette": fill the WHOLE emblem union, solid, no holes. Use this for a
    colour that mostly shows as thin detail (figures, linework). Placed at the
    BACK it becomes free backing: the detail then appears wherever the layers
    on top have holes/gaps, at zero geometry cost, and no seam can reveal the
    background because there is always backing behind it.
  - "region": trace the colour's natural mask with holes preserved. Use for
    the large solid areas drawn on top of the backing.

Config: `python trace_svg.py IMAGE OUT.svg [CONFIG.json]`. See
`examples/nu22.layers.json` for the format, or omit CONFIG to auto-detect a
palette with k-means and guess a stack (largest solid area = backing).
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import binary_fill_holes, gaussian_filter
from skimage import measure
from shapely.geometry import Polygon

ROOT = Path(__file__).resolve().parent.parent


def classify(im, centroids):
    """centroids: dict name -> (r,g,b). Return (label_grid, names)."""
    names = list(centroids)
    C = np.array([centroids[n] for n in names], float)
    d = ((im.reshape(-1, 1, 3) - C.reshape(1, -1, 3)) ** 2).sum(2)
    return d.argmin(1).reshape(im.shape[:2]), names


def layer_polys(mask, blur, simplify_tol, min_area=6.0):
    """mask: bool. Return simplified shapely polygons WITH holes.

    marching_squares returns outer and inner boundaries as separate closed
    loops with no nesting info, so we reconstruct containment: a ring nested
    at odd depth is a hole, even depth is solid fill (even-odd rule).
    """
    soft = gaussian_filter(mask.astype(float), blur)
    contours = measure.find_contours(soft, 0.5)
    rings = []
    for c in contours:
        # find_contours yields (row, col) = (y, x); flip to (x, y)
        ring = [(x, y) for y, x in c]
        if len(ring) < 4:
            continue
        p = Polygon(ring)
        if not p.is_valid:
            p = p.buffer(0)
        if p.is_empty or p.area < 1.0:
            continue
        rings.append(p)
    # Build a containment tree by area (largest first). Each ring's parent is
    # the smallest already-placed ring that contains it. Depth parity then tells
    # solid (even) from hole (odd) unambiguously, even for holes-in-holes.
    order = sorted(range(len(rings)), key=lambda i: -rings[i].area)
    reps = {i: rings[i].representative_point() for i in range(len(rings))}
    parent = {}
    depth = {}
    placed = []  # indices already assigned, largest-first
    for i in order:
        par = None
        for j in placed:  # nearest (smallest) container placed so far
            if rings[j].contains(reps[i]):
                par = j
        parent[i] = par
        depth[i] = 0 if par is None else depth[par] + 1
        placed.append(i)
    result = []
    for i in order:
        if depth[i] % 2 != 0:
            continue  # a hole; consumed by its parent solid
        my_holes = [list(rings[h].exterior.coords)
                    for h in range(len(rings))
                    if parent.get(h) == i]
        p = Polygon(list(rings[i].exterior.coords), my_holes)
        if not p.is_valid:
            p = p.buffer(0)
        p = p.simplify(simplify_tol, preserve_topology=True)
        if not p.is_empty and p.area >= min_area:
            result.append(p)
    return result


def poly_to_svg(p, fill, holes_as_black=True):
    """Emit one polygon (with holes via even-odd) as SVG path(s)."""
    def ring_d(coords):
        pts = list(coords)
        d = f"M {pts[0][0]:.2f},{pts[0][1]:.2f} "
        d += " ".join(f"L {x:.2f},{y:.2f}" for x, y in pts[1:])
        return d + " Z"
    geoms = p.geoms if p.geom_type == "MultiPolygon" else [p]
    out = []
    for g in geoms:
        d = ring_d(g.exterior.coords)
        for interior in g.interiors:
            d += " " + ring_d(interior.coords)
        out.append(f'<path d="{d}" fill="{fill}" fill-rule="evenodd"/>')
    return out


def auto_config(im, k=4):
    """k-means an emblem into a palette + a guessed layer stack.

    The darkest cluster is treated as background. Of the remaining colours the
    one with the largest area becomes the solid backing (mode silhouette, z 0);
    the rest are regions stacked smallest-area-in-front so fine detail wins."""
    from numpy.random import default_rng
    flat = im.reshape(-1, 3)
    rng = default_rng(0)
    C = flat[rng.choice(len(flat), k, replace=False)].astype(float)
    for _ in range(40):
        lab = ((flat[:, None] - C[None]) ** 2).sum(2).argmin(1)
        for j in range(k):
            if (lab == j).any():
                C[j] = flat[lab == j].mean(0)
    counts = np.bincount(lab, minlength=k)
    bg = int(C.sum(1).argmin())  # darkest = background
    centroids = {"background": tuple(int(v) for v in C[bg])}
    fg = [j for j in range(k) if j != bg]
    fg.sort(key=lambda j: -counts[j])
    layers = {}
    for rank, j in enumerate(fg):
        r, g, b = (int(v) for v in C[j])
        name = f"c{rank}"
        centroids[name] = (r, g, b)
        fill = f"#{r:02X}{g:02X}{b:02X}"
        if rank == 0:  # largest area = backing
            layers[name] = [fill, 0, 1.2, "silhouette"]
        else:  # smaller regions in front (higher z, finer simplify)
            layers[name] = [fill, rank, 1.0, "region"]
    return {"centroids": centroids, "background": "background", "layers": layers}


def load_config(im, config_path=None):
    """Return (centroids dict, background name, layers dict). layers value =
    [fill, z_order, simplify_tol, mode]."""
    if config_path:
        cfg = json.loads(Path(config_path).read_text())
    else:
        cfg = auto_config(im)
    centroids = {n: tuple(v) for n, v in cfg["centroids"].items()}
    return centroids, cfg.get("background", "black"), cfg["layers"]


def build_layers(im, centroids, background, layers, blur=1.2):
    """Yield (name, fill, z_order, polys) back-to-front."""
    label, names = classify(im, centroids)
    idx = {n: i for i, n in enumerate(names)}
    emblem = label != idx[background]
    for name in sorted(layers, key=lambda n: layers[n][1]):  # by z_order back->front
        fill, z_order, tol, mode = layers[name]
        if mode == "silhouette":
            mask = binary_fill_holes(emblem)
        else:  # region: colour's natural mask, holes preserved
            mask = label == idx[name]
        yield name, fill, z_order, layer_polys(mask, blur, tol)


def main(src_path, out_svg, config_path=None, blur=1.2):
    im = np.array(Image.open(src_path).convert("RGB")).astype(float)
    H, W = im.shape[:2]
    centroids, background, layers = load_config(im, config_path)

    body = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}">',
        f'<rect width="{W}" height="{H}" fill="#000000"/>',
    ]
    counts = {}
    for name, fill, _z, polys in build_layers(im, centroids, background, layers, blur):
        counts[name] = (len(polys), sum(len(g.exterior.coords) - 1
                        for p in polys
                        for g in (p.geoms if p.geom_type == "MultiPolygon" else [p])))
        for p in polys:
            body += poly_to_svg(p, fill)
    body.append("</svg>")
    Path(out_svg).write_text("\n".join(body))
    total_v = sum(v for _, v in counts.values())
    print(f"wrote {out_svg}  ({W}x{H})")
    for n, (np_, nv) in counts.items():
        print(f"  {n}: {np_} paths, {nv} vertices")
    print(f"  total exterior vertices: {total_v}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: python trace_svg.py IMAGE OUT.svg [CONFIG.json] [blur]")
        sys.exit(1)
    src, out = sys.argv[1], sys.argv[2]
    config = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3].endswith(".json") else None
    blur = float(sys.argv[4]) if len(sys.argv) > 4 else 1.2
    main(src, out, config, blur)
