"""Convert a MULTI-COLOUR emblem into one vanilla-ProjectMER schematic by
tracing each colour as a smooth layer and stacking the layers by Z.

Use this instead of `png_to_mer_schematic.py` when the emblem has stacked
colours (e.g. white figures on a green disc on a blue border): a single
silhouette can't represent that, and flat tracing staircases the edges.

Pipeline (see trace_svg.py for the per-layer detail):
  image -> classify pixels to a palette -> per-colour smooth sub-pixel contours
        -> feed each layer's polygons into the ngon quad decomposition
        -> merge all layers into one schematic (offset ObjectId/ParentId),
           each layer at its own Z so front layers occlude back ones.

The primitive-saving trick lives in the config: mark the colour that is mostly
thin detail as a solid "silhouette" backing at the back; the detail then shows
through holes/gaps in the layers on top at zero geometry cost.

Usage:
  python tools/layered_emblem_to_mer.py IMAGE [--config C.json]
      [--name NAME] [--output DIR] [--preview] [--quality 1-100]

Omit --config to auto-detect the palette and stack with k-means.
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageColor, ImageDraw
from scipy.ndimage import binary_fill_holes
from shapely.geometry.polygon import orient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))


def _load(mod_name):
    spec = importlib.util.spec_from_file_location(mod_name, ROOT / "tools" / f"{mod_name}.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = m  # register so dataclass lookups resolve
    spec.loader.exec_module(m)
    return m


ts = _load("trace_svg")
p2m = _load("png_to_mer_schematic")
ngon = _load("mer_ngon_decomposition")


def rasterize(polys, W, H):
    """Rasterize shapely polys (with holes) to a uint8 coverage mask."""
    img = Image.new("L", (W, H), 0)
    dr = ImageDraw.Draw(img)
    for p in polys:
        geoms = p.geoms if p.geom_type == "MultiPolygon" else [p]
        for g in geoms:
            dr.polygon(list(g.exterior.coords), fill=255)
            for interior in g.interiors:
                dr.polygon(list(interior.coords), fill=0)
    return np.array(img)


def polys_to_unity(polys, W, H, width_units):
    """shapely polys -> [[shell, *holes], ...] in Unity coords.
    Orient shell CCW / holes CW so earcut resolves holes correctly."""
    out = []
    for p in polys:
        geoms = p.geoms if p.geom_type == "MultiPolygon" else [p]
        for g in geoms:
            g = orient(g, sign=1.0)
            rings = [[p2m.image_point_to_unity((x, y), W, H, width_units)
                      for x, y in g.exterior.coords[:-1]]]
            for interior in g.interiors:
                rings.append([p2m.image_point_to_unity((x, y), W, H, width_units)
                              for x, y in interior.coords[:-1]])
            out.append(rings)
    return out


def layer_z(cfg, name, z_order, n_layers):
    """Z for a layer. Config may pin per-layer Z; else derive from z_order so
    higher z_order (front) gets a more-negative (front-facing) Z."""
    if "layer_z" in cfg and name in cfg["layer_z"]:
        return cfg["layer_z"][name]
    return round(-0.04 - 0.02 * z_order, 5)


def quality_scale(quality):
    """Map a friendly 1-100 quality value to a contour simplification scale.

    70 keeps the historical tolerance almost unchanged. Higher values preserve
    more contour points and therefore create more triangles/primitives.
    """
    quality = max(1, min(100, int(quality)))
    return 0.25 + 3.0 * ((100 - quality) / 75.0) ** 1.4


def layers_at_quality(layers, quality):
    if quality is None:
        return layers
    scale = quality_scale(quality)
    return {
        layer_name: [fill, z_order, max(0.12, float(tolerance) * scale), mode]
        for layer_name, (fill, z_order, tolerance, mode) in layers.items()
    }


def write_mesh_preview(path, layer_geometry, source_triangles, W, H, width_units):
    """Write the real pre-merge earcut triangulation as a compact SVG."""
    height_units = width_units * H / W

    def image_point(point):
        x, y = point
        return (
            ((x / width_units) + 0.5) * W,
            (0.5 - (y / height_units)) * H,
        )

    body = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
        f'<rect width="{W}" height="{H}" fill="#05080C"/>',
    ]
    for fill, polys in layer_geometry:
        for poly in polys:
            body.extend(ts.poly_to_svg(poly, fill))

    mesh_path = []
    for triangle in source_triangles:
        points = [image_point(point) for point in triangle]
        mesh_path.append(
            "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in points) + " Z"
        )
    path_data = " ".join(mesh_path)
    body.append(
        f'<path d="{path_data}" fill="none" stroke="#05080C" stroke-width="2.2" '
        'stroke-opacity="0.72" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>'
    )
    body.append(
        f'<path d="{path_data}" fill="none" stroke="#F4F8FB" stroke-width="0.72" '
        'stroke-opacity="0.92" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>'
    )
    body.append("</svg>")
    Path(path).write_text("\n".join(body), encoding="utf-8")

    raster = Image.new("RGB", (W, H), (5, 8, 12))
    draw = ImageDraw.Draw(raster)
    for fill, polys in layer_geometry:
        rgb = ImageColor.getrgb(fill[:7])
        for poly in polys:
            geometries = poly.geoms if poly.geom_type == "MultiPolygon" else [poly]
            for geometry in geometries:
                draw.polygon(list(geometry.exterior.coords), fill=rgb)
                for interior in geometry.interiors:
                    draw.polygon(list(interior.coords), fill=(5, 8, 12))
    for triangle in source_triangles:
        points = [image_point(point) for point in triangle]
        points.append(points[0])
        draw.line(points, fill=(5, 8, 12), width=3, joint="curve")
        draw.line(points, fill=(244, 248, 251), width=1, joint="curve")
    raster.save(Path(path).with_suffix(".png"))


def convert(image_path, config_path, name, out_dir, make_preview, quality=None,
            make_mesh_preview=False):
    im = np.array(Image.open(image_path).convert("RGB")).astype(float)
    H, W = im.shape[:2]
    cfg = json.loads(Path(config_path).read_text()) if config_path else ts.auto_config(im)
    width_units = cfg.get("width_units", 10.0)
    blur = cfg.get("blur", 1.2)
    centroids = {n: tuple(v) for n, v in cfg["centroids"].items()}
    background = cfg.get("background", "black")
    layers = layers_at_quality(cfg["layers"], quality)
    n_layers = len(layers)

    all_blocks = []
    next_id = 1
    summary = []
    layer_geometry = []
    source_triangles = []
    for lname, fill, z_order, polys in ts.build_layers(im, centroids, background, layers, blur):
        color = fill if len(fill) == 9 else fill + "FF"
        z = layer_z(cfg, lname, z_order, n_layers)
        region_mask = rasterize(polys, W, H)
        unity_polys = polys_to_unity(polys, W, H, width_units)
        blocks, next_id, stats, geometry = ngon.rings_to_parallelogram_blocks(
            unity_polys, region_mask, W, H, width_units, z, color,
            f"{name}-{lname}", start_id=next_id, parent_id=0,
        )
        all_blocks.extend(blocks)
        summary.append((lname, len(polys), len(blocks), stats.line()))
        layer_geometry.append((fill, polys))
        source_triangles.extend(geometry["source_triangles"])

    schematic = {"RootObjectId": 0, "Blocks": all_blocks}
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / f"{name}.json"
    out_json.write_text(json.dumps(schematic, separators=(",", ":")), encoding="utf-8")

    for lname, npoly, nblk, line in summary:
        print(f"  {lname}: {npoly} polys -> {nblk} blocks | {line}")
    print(f"Wrote {out_json}: {len(all_blocks)} total primitives")

    if make_preview:
        rp = _load("render_preview")
        rp.main(str(out_json), str(out_dir / name), 1000)
    if make_mesh_preview:
        mesh_path = out_dir / f"{name}.mesh.svg"
        write_mesh_preview(mesh_path, layer_geometry, source_triangles, W, H, width_units)
        print(f"Wrote {mesh_path}")
    print(f"Triangulation: {len(source_triangles)} source triangles")
    return out_json


def main():
    ap = argparse.ArgumentParser(description="Layered multi-colour emblem -> ProjectMER schematic.")
    ap.add_argument("image", help="Input emblem image (PNG/JPG/WEBP).")
    ap.add_argument("--config", help="Layer config JSON (see examples/*.layers.json). Omit to auto-detect.")
    ap.add_argument("--name", help="Schematic name. Defaults to the image stem.")
    ap.add_argument("--output", default=str(ROOT / "converted_mer"), help="Directory to receive the schematic folder.")
    ap.add_argument("--preview", action="store_true", help="Also write <name>.preview.png/.svg from the schematic.")
    ap.add_argument("--mesh-preview", action="store_true", help="Also write <name>.mesh.svg with the source triangulation.")
    ap.add_argument("--quality", type=int, choices=range(1, 101), metavar="1-100",
                    help="Contour quality. Higher keeps more detail and creates more geometry.")
    args = ap.parse_args()

    image_path = Path(args.image)
    if not image_path.exists():
        raise FileNotFoundError(image_path)
    name = p2m.clean_name(args.name or image_path.stem)
    out_dir = Path(args.output) / name
    convert(image_path, args.config, name, out_dir, args.preview, args.quality,
            args.mesh_preview)


if __name__ == "__main__":
    main()
