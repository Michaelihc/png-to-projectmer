"""Rebuild the Strike Team mark from exact mathematical MER geometry.

The source is deliberately not raster-traced.  Circles are Cylinder primitives;
the regular hexagon, compass needles, marker shields, and six-point star are a
small set of native Triangle blocks.  The only black geometry consists of two
disc cut-outs needed to turn filled cylinders into rings; there is no backdrop.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


p2m = _load("png_to_mer_schematic")

LIGHT = "#5F95E6FF"
DARK = "#22529BFF"
BLACK = "#000000FF"
SOURCE_PALETTE = np.array(((255, 255, 255), (95, 149, 230), (34, 82, 155)), dtype=np.int32)


def vec3(x: float, y: float, z: float) -> dict[str, float]:
    return {"x": round(float(x), 6), "y": round(float(y), 6), "z": round(float(z), 6)}


def primitive_block(
    name: str,
    object_id: int,
    primitive_type: int,
    position: tuple[float, float, float],
    rotation: tuple[float, float, float],
    scale: tuple[float, float, float],
    color: str,
) -> dict:
    return {
        "Name": name,
        "ObjectId": object_id,
        "ParentId": 0,
        "Position": vec3(*position),
        "Rotation": vec3(*rotation),
        "Scale": vec3(*scale),
        "BlockType": 1,
        "Properties": {
            "PrimitiveType": primitive_type,
            "PrimitiveFlags": 2,
            "Color": color,
            "Static": True,
        },
    }


def triangle_block(name: str, object_id: int, points, color: str, z: float) -> dict:
    unity = [p2m.image_point_to_unity(p, 300, 300, 10.0) for p in points]
    if p2m.signed_area(unity) < 0:
        unity[1], unity[2] = unity[2], unity[1]
    return {
        "Name": name,
        "ObjectId": object_id,
        "ParentId": 0,
        "Position": vec3(0, 0, z),
        "Rotation": vec3(0, 0, 0),
        "Scale": vec3(1, 1, 1),
        "BlockType": 11,
        "Properties": {
            "PointA": vec3(*unity[0], 0),
            "PointB": vec3(*unity[1], 0),
            "PointC": vec3(*unity[2], 0),
            "Color": color,
            "Thickness": 0.01,
            "Static": True,
        },
    }


def polygon_triangles(points) -> list[tuple[tuple[float, float], ...]]:
    """Convex polygon fan; all source polygons in this builder are convex."""
    return [(points[0], points[index], points[index + 1]) for index in range(1, len(points) - 1)]


def regular_vertices(center: tuple[float, float], radius: float, count: int, start_deg: float):
    cx, cy = center
    return [
        (
            cx + radius * math.cos(math.radians(start_deg + index * 360.0 / count)),
            cy + radius * math.sin(math.radians(start_deg + index * 360.0 / count)),
        )
        for index in range(count)
    ]


def build_blocks(width_units: float = 10.0, outer_cutout_radius: float = 111.5) -> list[dict]:
    center = (149.5, 149.5)
    center_u = p2m.image_point_to_unity(center, 300, 300, width_units)
    unit = width_units / 300.0
    blocks: list[dict] = []
    object_id = 1

    # The light circle is clipped at the exact apothem of the outer regular
    # hexagon.  The frame overlays the ring, leaving six smooth circular caps.
    # Slightly inset from the pale 122 px circle, matching the requested
    # breathing room while retaining the original 15 px radial frame width.
    outer_hex_radius = 116.0
    inner_hex_radius = 101.0
    outer_disc_radius = 122.0
    # A 6.6 px separator between the pale disc and the dark frame is present
    # in the source.  A circular cut-out preserves a perfectly smooth edge and
    # measured best against the full classified source diff at 111.5 px.
    for suffix, radius, color, z in (
        ("outer", outer_disc_radius, LIGHT, -0.001),
        ("cutout", outer_cutout_radius, BLACK, -0.003),
    ):
        diameter = radius * 2.0 * unit
        blocks.append(
            primitive_block(
                f"strike-outer-circle-{suffix}", object_id, 2,
                (center_u[0], center_u[1], z), (90, 0, 0),
                (diameter, 0.01, diameter), color,
            )
        )
        object_id += 1

    # Exact regular-hexagonal dark frame: six trapezoids, two triangles each.
    outer = regular_vertices(center, outer_hex_radius, 6, -90.0)
    inner = regular_vertices(center, inner_hex_radius, 6, -90.0)
    for side in range(6):
        nxt = (side + 1) % 6
        quad = (outer[side], outer[nxt], inner[nxt], inner[side])
        for part, tri in enumerate(((quad[0], quad[1], quad[2]), (quad[0], quad[2], quad[3]))):
            blocks.append(triangle_block(f"strike-hex-side-{side}-{part}", object_id, tri, DARK, -0.010))
            object_id += 1

    # Central annulus.  Exact cylinders keep both edges perfectly smooth.
    for suffix, radius, color, z in (
        ("outer", 69.0, DARK, -0.014),
        ("cutout", 55.0, BLACK, -0.016),
    ):
        diameter = radius * 2.0 * unit
        blocks.append(
            primitive_block(
                f"strike-center-ring-{suffix}", object_id, 2,
                (center_u[0], center_u[1], z), (90, 0, 0),
                (diameter, 0.01, diameter), color,
            )
        )
        object_id += 1

    # Three long compass needles cross the annulus with a narrow negative-space
    # border.  Three short black quads make those exact separations much more
    # efficiently than triangulating six broken circular arcs.
    for index, image_angle in enumerate((90.0, 210.0, 330.0)):
        radians = math.radians(image_angle)
        radius = 62.0
        point = (center[0] + radius * math.cos(radians), center[1] + radius * math.sin(radians))
        point_u = p2m.image_point_to_unity(point, 300, 300, width_units)
        blocks.append(
            primitive_block(
                f"strike-ring-separator-{index}", object_id, 5,
                (point_u[0], point_u[1], -0.020),
                (0, 0, -image_angle - 90.0),
                (12.0 * unit, 34.0 * unit, 1.0), BLACK,
            )
        )
        object_id += 1

    # Six-point star built as six efficient triangular points.  The shared
    # central bases overlap, so there are no seams when viewed obliquely.
    star_tips = (
        (176.0, 149.5), (163.0, 126.0), (136.0, 126.0),
        (123.0, 149.5), (136.0, 173.0), (163.0, 173.0),
    )
    star_center = np.array(center)
    half_base = 8.0
    for index, tip in enumerate(star_tips):
        direction = np.asarray(tip, dtype=float) - star_center
        direction /= np.linalg.norm(direction)
        normal = np.array((-direction[1], direction[0]))
        a = tuple(star_center + normal * half_base)
        b = tuple(star_center - normal * half_base)
        blocks.append(triangle_block(f"strike-star-point-{index}", object_id, (a, tip, b), DARK, -0.030))
        object_id += 1

    # Three congruent long inward needles.  The bottom needle is the canonical
    # geometry; the upper pair are exact +/-120-degree rotations of it.  Their
    # bases extend 1.5 px into the inset hex frame, eliminating the pinholes
    # that previously appeared around the bottom junction.
    rays = []
    long_tip_radius = 28.5
    long_base_radius = 102.5
    long_half_base = 7.5
    for image_angle in (90.0, 210.0, 330.0):
        radians = math.radians(image_angle)
        direction = np.array((math.cos(radians), math.sin(radians)))
        normal = np.array((-direction[1], direction[0]))
        tip = tuple(np.asarray(center) + direction * long_tip_radius)
        base_center = np.asarray(center) + direction * long_base_radius
        rays.append((tuple(base_center + normal * long_half_base), tuple(base_center - normal * long_half_base), tip))

    # The three short inward markers remain source-sized and alternate between
    # the long needles at the other 60-degree positions.
    rays.extend((
        ((147.0, 101.0), (152.0, 101.0), (149.5, 121.0)),
        ((108.0, 171.0), (108.0, 175.0), (121.0, 166.0)),
        ((191.0, 171.0), (191.0, 175.0), (178.0, 166.0)),
    ))
    for index, points in enumerate(rays):
        blocks.append(triangle_block(f"strike-compass-ray-{index}", object_id, points, DARK, -0.026))
        object_id += 1

    # Three five-sided team markers, copied as clean symmetric polygons.
    markers = (
        ((149.5, 51.0), (156.0, 56.0), (154.5, 73.0), (144.5, 73.0), (143.0, 56.0)),
        ((65.0, 190.0), (77.0, 183.5), (84.0, 192.0), (72.0, 201.0), (66.0, 198.0)),
        ((234.0, 190.0), (222.0, 183.5), (215.0, 192.0), (227.0, 201.0), (233.0, 198.0)),
    )
    for marker_index, marker in enumerate(markers):
        for part, tri in enumerate(polygon_triangles(marker)):
            blocks.append(triangle_block(f"strike-marker-{marker_index}-{part}", object_id, tri, DARK, -0.024))
            object_id += 1

    return blocks


def hex_color(value: str) -> tuple[int, int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4, 6))


def render_blocks(blocks: list[dict], size: int, comparison: bool = False) -> Image.Image:
    """Supersampled face-on renderer used by previews and numerical diffing."""
    supersample = 4
    canvas_size = size * supersample
    scale = canvas_size / 10.0
    bg = (255, 255, 255, 255) if comparison else (0, 0, 0, 255)
    canvas = Image.new("RGBA", (canvas_size, canvas_size), bg)
    draw = ImageDraw.Draw(canvas)

    def screen(x: float, y: float):
        return ((x + 5.0) * scale, (5.0 - y) * scale)

    for block in sorted(blocks, key=lambda item: -item["Position"]["z"]):
        props = block["Properties"]
        color = hex_color(props["Color"])
        if comparison and props["Color"] == BLACK:
            color = (255, 255, 255, 255)
        if block["BlockType"] == 11:
            points = []
            for key in ("PointA", "PointB", "PointC"):
                point = props[key]
                points.append(screen(point["x"] + block["Position"]["x"], point["y"] + block["Position"]["y"]))
            draw.polygon(points, fill=color)
        elif props["PrimitiveType"] == 2:
            cx, cy = screen(block["Position"]["x"], block["Position"]["y"])
            diameter = block["Scale"]["x"] * scale
            draw.ellipse((cx - diameter / 2, cy - diameter / 2, cx + diameter / 2, cy + diameter / 2), fill=color)
        elif props["PrimitiveType"] == 5:
            cx, cy = screen(block["Position"]["x"], block["Position"]["y"])
            sx = block["Scale"]["x"] * scale
            sy = block["Scale"]["y"] * scale
            angle = math.radians(block["Rotation"]["z"])
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            points = []
            for lx, ly in ((-sx / 2, -sy / 2), (sx / 2, -sy / 2), (sx / 2, sy / 2), (-sx / 2, sy / 2)):
                points.append((cx + lx * cos_a - ly * sin_a, cy - (lx * sin_a + ly * cos_a)))
            draw.polygon(points, fill=color)
    return canvas.resize((size, size), Image.Resampling.LANCZOS).convert("RGB")


def nearest_labels(rgb: np.ndarray) -> np.ndarray:
    values = rgb.astype(np.int32)
    error = values[:, :, None, :] - SOURCE_PALETTE[None, None, :, :]
    return np.square(error).sum(axis=3).argmin(axis=2)


def write_previews(blocks: list[dict], source_path: Path, target: Path, name: str) -> dict[str, float]:
    preview = render_blocks(blocks, 1200)
    preview.save(target / f"{name}.preview.png")
    preview.crop((160, 160, 1040, 1040)).resize((1320, 1320), Image.Resampling.LANCZOS).save(
        target / f"{name}.zoom.png"
    )
    preview.crop((115, 250, 1085, 805)).resize((1455, 833), Image.Resampling.LANCZOS).save(
        target / f"{name}.needles-zoom.png"
    )
    preview.crop((440, 825, 760, 1125)).resize((1280, 1200), Image.Resampling.LANCZOS).save(
        target / f"{name}.bottom-junction-zoom.png"
    )

    source = Image.open(source_path).convert("RGB")
    rendered = render_blocks(blocks, 300, comparison=True)
    source_np = np.asarray(source)
    rendered_np = np.asarray(rendered)
    source_labels = nearest_labels(source_np)
    rendered_labels = nearest_labels(rendered_np)
    mismatch = source_labels != rendered_labels

    highlighted = (source_np.astype(np.float32) * 0.32 + 255.0 * 0.68).astype(np.uint8)
    highlighted[mismatch] = np.array((255, 28, 28), dtype=np.uint8)
    Image.fromarray(highlighted).resize((1200, 1200), Image.Resampling.NEAREST).save(
        target / f"{name}.diff-highlight.png"
    )
    comparison = Image.new("RGB", (900, 300), "white")
    comparison.paste(source, (0, 0))
    comparison.paste(rendered, (300, 0))
    comparison.paste(Image.fromarray(highlighted), (600, 0))
    comparison.resize((1800, 600), Image.Resampling.NEAREST).save(target / f"{name}.comparison.png")

    metrics: dict[str, float] = {"different_pixel_percent": float(mismatch.mean() * 100.0)}
    for label, label_name in ((1, "light"), (2, "dark")):
        a, b = source_labels == label, rendered_labels == label
        union = np.logical_or(a, b).sum()
        metrics[f"{label_name}_iou"] = float(np.logical_and(a, b).sum() / union) if union else 1.0
    (target / f"{name}.diff-metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics


def convert(source_path: Path, output_dir: Path, name: str, width_units: float) -> Path:
    # Geometry is authored against the 300x300 source, then uniformly mapped to
    # the requested world width.  Ten units is the tuned/default MER scale.
    if abs(width_units - 10.0) > 1e-8:
        raise ValueError("This exact reconstruction currently requires --width 10")
    blocks = build_blocks(width_units)
    target = output_dir / name
    target.mkdir(parents=True, exist_ok=True)
    output = target / f"{name}.json"
    output.write_text(json.dumps({"RootObjectId": 0, "Blocks": blocks}, separators=(",", ":")), encoding="utf-8")
    metrics = write_previews(blocks, source_path, target, name)

    ids = [block["ObjectId"] for block in blocks]
    assert len(ids) == len(set(ids))
    assert all(block["ParentId"] == 0 for block in blocks)
    primitives = sum(block["BlockType"] == 1 for block in blocks)
    cylinders = sum(
        block["BlockType"] == 1 and block["Properties"]["PrimitiveType"] == 2
        for block in blocks
    )
    quads = primitives - cylinders
    triangles = sum(block["BlockType"] == 11 for block in blocks)
    print(f"wrote {output}")
    print(f"{len(blocks)} schematic blocks: {cylinders} exact cylinders + {quads} separator quads + "
          f"{triangles} native triangles")
    print(f"estimated runtime toys: {primitives + triangles * 6}")
    print(f"programmatic diff: {metrics['different_pixel_percent']:.2f}% pixels; "
          f"light IoU {metrics['light_iou']:.4f}; dark IoU {metrics['dark_iou']:.4f}")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Strike Team emblem -> mathematical ProjectMER schematic")
    parser.add_argument("image", type=Path, nargs="?", default=ROOT / "Strike-Team.png")
    parser.add_argument("--output", type=Path, default=ROOT / "converted_mer")
    parser.add_argument("--name", default="strike-team")
    parser.add_argument("--width", type=float, default=10.0)
    args = parser.parse_args()
    convert(args.image, args.output, p2m.clean_name(args.name), args.width)


if __name__ == "__main__":
    main()
