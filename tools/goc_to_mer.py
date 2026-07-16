"""Build the Global Occult Coalition emblem with native MER triangles/text.

The generic layered converter is deliberately not used for the circular words:
its silhouette backing puts light-blue geometry behind every dark glyph, while
the n-gon optimizer is allowed to overlap same-colour quads.  Both assumptions
are fine face-on, but they produce ugly seams and fill artefacts from an angle.

This specialised builder:
  * keeps only the five large dark connected components as pentagram artwork;
  * emits non-overlapping colour regions as BlockType 11 triangles;
  * replaces the 49 raster glyphs with 49 BlockType 8 TextToys; and
  * emits no primitive for the pale-blue/black background.

It targets the ProjectMER build in this repository, which supports BlockType 8
and BlockType 11.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy.ndimage import label
from shapely.geometry.polygon import orient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ts = _load("trace_svg")
p2m = _load("png_to_mer_schematic")

BACKGROUND = np.array([219, 229, 254], dtype=float)
LIGHT_BLUE = np.array([57, 154, 249], dtype=float)
DARK_BLUE = np.array([33, 81, 155], dtype=float)
PALETTE = np.stack([BACKGROUND, LIGHT_BLUE, DARK_BLUE])

LIGHT_COLOR = "#399AF9FF"
DARK_COLOR = "#21519BFF"
BLACK = "#000000FF"

WORDS = {
    "education": "EDUCATION",
    "survival": "SURVIVAL",
    "destruction": "DESTRUCTION",
    "concealment": "CONCEALMENT",
    "protection": "PROTECTION",
}


def vec3(x: float, y: float, z: float) -> dict[str, float]:
    return {"x": round(float(x), 5), "y": round(float(y), 5), "z": round(float(z), 5)}


def classify(image: np.ndarray) -> np.ndarray:
    result = np.empty(image.shape[:2], dtype=np.uint8)
    palette = PALETTE.astype(np.int16)
    for y in range(0, image.shape[0], 256):
        chunk = image[y : y + 256].astype(np.int16)
        delta = chunk[:, :, None, :] - palette[None, None, :, :]
        distance = np.square(delta.astype(np.int32)).sum(axis=3)
        result[y : y + len(chunk)] = distance.argmin(axis=2).astype(np.uint8)
    return result


def split_dark_components(dark_mask: np.ndarray) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """Return the five pentagram pieces and the 49 letter centroids."""
    components, count = label(dark_mask)
    sizes = np.bincount(components.ravel())
    ranked = sorted(range(1, count + 1), key=lambda i: int(sizes[i]), reverse=True)
    art_ids = set(ranked[:5])
    art = np.isin(components, list(art_ids))

    centroids: list[tuple[float, float]] = []
    for component_id in ranked[5:]:
        # Reject JPEG dust while retaining every real glyph.
        if sizes[component_id] < 500:
            continue
        ys, xs = np.where(components == component_id)
        centroids.append((float(xs.mean()), float(ys.mean())))

    if len(centroids) != 49:
        raise RuntimeError(f"Expected 49 text glyphs, found {len(centroids)}")
    return art, centroids


def fit_ring_center(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Least-squares circle centre for the raster glyph centroids."""
    pts = np.asarray(points, dtype=float)
    a = np.column_stack((2.0 * pts[:, 0], 2.0 * pts[:, 1], np.ones(len(pts))))
    b = np.square(pts[:, 0]) + np.square(pts[:, 1])
    cx, cy, _ = np.linalg.lstsq(a, b, rcond=None)[0]
    return float(cx), float(cy)


def group_letters(
    points: list[tuple[float, float]], center: tuple[float, float]
) -> dict[str, list[tuple[str, float, float, float]]]:
    """Assign connected components to the five known words by polar sector."""
    cx, cy = center
    polar = []
    for x, y in points:
        # Convert image-down Y to a conventional image-centred polar angle.
        angle = math.degrees(math.atan2(-(y - cy), x - cx))
        polar.append((x, y, angle))

    sectors = {
        "education": [item for item in polar if 90.0 < item[2] < 165.0],
        "survival": [item for item in polar if 20.0 < item[2] <= 90.0],
        "concealment": [item for item in polar if -55.0 < item[2] <= 20.0],
        "protection": [item for item in polar if -125.0 < item[2] <= -55.0],
    }
    used = {id(item) for values in sectors.values() for item in values}
    sectors["destruction"] = [item for item in polar if id(item) not in used]

    sectors["education"].sort(key=lambda item: item[2])
    sectors["survival"].sort(key=lambda item: -item[2])
    sectors["concealment"].sort(key=lambda item: -item[2])
    sectors["protection"].sort(key=lambda item: item[2])
    sectors["destruction"].sort(key=lambda item: item[2] if item[2] >= 0 else item[2] + 360.0)

    result: dict[str, list[tuple[str, float, float, float]]] = {}
    for name, word in WORDS.items():
        values = sectors[name]
        if len(values) != len(word):
            raise RuntimeError(f"{name}: expected {len(word)} glyphs, found {len(values)}")
        result[name] = [(char, x, y, angle) for char, (x, y, angle) in zip(word, values)]
    return result


def replacement_map_mask(
    map_path: Path,
    output_width: int,
    output_height: int,
    center: tuple[float, float],
    target_radius_px: float = 990.0,
) -> np.ndarray:
    """Extract the pale land from a supplied polar map and fit it to the globe."""
    source = np.asarray(Image.open(map_path).convert("RGB"))
    height, width = source.shape[:2]
    cx, cy = (width - 1) * 0.5, (height - 1) * 0.5
    yy, xx = np.ogrid[:height, :width]
    source_radius = min(width, height) * 0.47
    inside_disc = np.square(xx - cx) + np.square(yy - cy) <= source_radius**2

    # The supplied map uses near-white land over a blue disc. The radius gate
    # removes the white JPEG corners without touching coastal land.
    brightness = source.astype(np.float32).mean(axis=2)
    land = (brightness >= 180.0) & inside_disc
    land = cv2.morphologyEx(land.astype(np.uint8), cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))

    scale = target_radius_px / source_radius
    resized = cv2.resize(
        land,
        (round(width * scale), round(height * scale)),
        interpolation=cv2.INTER_LINEAR,
    )
    resized = resized >= 0.45
    output = np.zeros((output_height, output_width), dtype=bool)
    x0 = round(center[0] - resized.shape[1] * 0.5)
    y0 = round(center[1] - resized.shape[0] * 0.5)
    x1, y1 = x0 + resized.shape[1], y0 + resized.shape[0]
    src_x0, src_y0 = max(0, -x0), max(0, -y0)
    src_x1 = resized.shape[1] - max(0, x1 - output_width)
    src_y1 = resized.shape[0] - max(0, y1 - output_height)
    output[max(0, y0) : min(output_height, y1), max(0, x0) : min(output_width, x1)] = resized[
        src_y0:src_y1, src_x0:src_x1
    ]
    return output


def mask_triangles(
    mask: np.ndarray,
    image_width: int,
    image_height: int,
    width_units: float,
    simplify: float,
) -> list[tuple[tuple[float, float], tuple[float, float], tuple[float, float]]]:
    polygons = ts.layer_polys(mask, blur=1.5, simplify_tol=simplify)
    triangles = []
    for polygon in polygons:
        geoms = polygon.geoms if polygon.geom_type == "MultiPolygon" else [polygon]
        for geom in geoms:
            geom = orient(geom, sign=1.0)
            rings = [list(geom.exterior.coords)[:-1]]
            rings.extend(list(interior.coords)[:-1] for interior in geom.interiors)
            triangles.extend(
                p2m.triangulate_rings(
                    rings,
                    image_width,
                    image_height,
                    width_units,
                    min_triangle_area=1e-8,
                    flip_winding=False,
                )
            )
    return triangles


def triangle_blocks(
    triangles,
    start_id: int,
    prefix: str,
    color: str,
    z: float,
) -> tuple[list[dict], int]:
    blocks = []
    object_id = start_id
    for index, (a, b, c) in enumerate(triangles, start=1):
        blocks.append(
            {
                "Name": f"{prefix}-tri-{index:05d}",
                "ObjectId": object_id,
                "ParentId": 0,
                "Position": vec3(0, 0, z),
                "Rotation": vec3(0, 0, 0),
                "Scale": vec3(1, 1, 1),
                "BlockType": 11,
                "Properties": {
                    "PointA": vec3(a[0], a[1], 0),
                    "PointB": vec3(b[0], b[1], 0),
                    "PointC": vec3(c[0], c[1], 0),
                    "Color": color,
                    "Thickness": 0.01,
                    "Static": True,
                },
            }
        )
        object_id += 1
    return blocks, object_id


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


def mathematical_star_blocks(
    center: tuple[float, float],
    image_width: int,
    image_height: int,
    width_units: float,
    start_id: int,
) -> tuple[list[dict], int]:
    """Five full bar quads plus exact miter joins at the five outer points."""
    cx, cy = center
    stroke_px = 150.0
    half_width = stroke_px * 0.5
    # The center-line vertex sits inside the visible tip.  For a regular
    # pentagram the tip angle is 36 degrees, so the miter extends by
    # half_width / sin(18 degrees).  Keeping the visible tip at 1280 px makes
    # this construction match the previous overall footprint exactly.
    tip_radius_px = 1280.0
    radius_px = tip_radius_px - half_width / math.sin(math.radians(18.0))
    vertices = [
        (cx + radius_px * math.cos(math.radians(angle)), cy - radius_px * math.sin(math.radians(angle)))
        for angle in (90.0, 18.0, -54.0, -126.0, 162.0)
    ]
    path = [vertices[index] for index in (0, 2, 4, 1, 3)]
    unit_per_pixel = width_units / image_width
    blocks = []
    object_id = start_id
    join_overlap_px = 0.0

    for index, (a, b) in enumerate(zip(path, path[1:] + path[:1])):
        dx, dy = b[0] - a[0], b[1] - a[1]
        pixel_length = math.hypot(dx, dy)
        ux, uy = dx / pixel_length, dy / pixel_length
        # Keep the quad endpoints on the mathematical center-line vertices;
        # the separate interior join triangles make these seams watertight.
        bar_start = (a[0] - ux * join_overlap_px, a[1] - uy * join_overlap_px)
        bar_end = (b[0] + ux * join_overlap_px, b[1] + uy * join_overlap_px)
        start_u = p2m.image_point_to_unity(bar_start, image_width, image_height, width_units)
        end_u = p2m.image_point_to_unity(bar_end, image_width, image_height, width_units)
        world_dx, world_dy = end_u[0] - start_u[0], end_u[1] - start_u[1]
        world_length = math.hypot(world_dx, world_dy)
        angle = math.degrees(math.atan2(world_dy, world_dx)) - 90.0
        blocks.append(
            primitive_block(
                f"goc-star-bar-{index}", object_id, 5,
                ((start_u[0] + end_u[0]) * 0.5, (start_u[1] + end_u[1]) * 0.5, -0.030),
                (0.0, 0.0, angle), (stroke_px * unit_per_pixel, world_length, 1.0), DARK_COLOR,
            )
        )
        object_id += 1

    def line_intersection(p, d, q, e):
        cross = d[0] * e[1] - d[1] * e[0]
        if abs(cross) < 1e-9:
            raise ValueError("parallel star edge lines")
        qmp = (q[0] - p[0], q[1] - p[1])
        t = (qmp[0] * e[1] - qmp[1] * e[0]) / cross
        return (p[0] + t * d[0], p[1] + t * d[1])

    cap_geometry = []
    for index, vertex in enumerate(path):
        previous = path[index - 1]
        following = path[(index + 1) % len(path)]
        incoming_length = math.hypot(vertex[0] - previous[0], vertex[1] - previous[1])
        outgoing_length = math.hypot(following[0] - vertex[0], following[1] - vertex[1])
        incoming = ((vertex[0] - previous[0]) / incoming_length, (vertex[1] - previous[1]) / incoming_length)
        outgoing = ((following[0] - vertex[0]) / outgoing_length, (following[1] - vertex[1]) / outgoing_length)
        incoming_perp = (-incoming[1], incoming[0])
        outgoing_perp = (-outgoing[1], outgoing[0])

        # Test both offset sides and retain the intersection farthest from the
        # emblem center.  This is the exterior miter.  Its adjoining sides are
        # continuations of the quad edges, so there is no shoulder or kink.
        candidates = []
        for incoming_sign in (-1.0, 1.0):
            for outgoing_sign in (-1.0, 1.0):
                incoming_corner = (
                    vertex[0] + incoming_perp[0] * half_width * incoming_sign,
                    vertex[1] + incoming_perp[1] * half_width * incoming_sign,
                )
                outgoing_corner = (
                    vertex[0] + outgoing_perp[0] * half_width * outgoing_sign,
                    vertex[1] + outgoing_perp[1] * half_width * outgoing_sign,
                )
                miter = line_intersection(incoming_corner, incoming, outgoing_corner, outgoing)
                distance_sq = (miter[0] - cx) ** 2 + (miter[1] - cy) ** 2
                candidates.append((distance_sq, incoming_corner, miter, outgoing_corner))
        _, incoming_corner, miter, outgoing_corner = max(candidates, key=lambda item: item[0])
        points = [
            p2m.image_point_to_unity(incoming_corner, image_width, image_height, width_units),
            p2m.image_point_to_unity(miter, image_width, image_height, width_units),
            p2m.image_point_to_unity(outgoing_corner, image_width, image_height, width_units),
        ]
        if p2m.signed_area(points) < 0:
            points[1], points[2] = points[2], points[1]
        cap_geometry.append(tuple(points))
        fill_points = [
            p2m.image_point_to_unity(incoming_corner, image_width, image_height, width_units),
            p2m.image_point_to_unity(outgoing_corner, image_width, image_height, width_units),
            p2m.image_point_to_unity(vertex, image_width, image_height, width_units),
        ]
        if p2m.signed_area(fill_points) < 0:
            fill_points[1], fill_points[2] = fill_points[2], fill_points[1]
        cap_geometry.append(tuple(fill_points))
    caps, object_id = triangle_blocks(cap_geometry, object_id, "goc-star-cap", DARK_COLOR, -0.030)
    blocks.extend(caps)
    return blocks, object_id


def circle_blocks(
    center_unity: tuple[float, float], width_units: float, image_width: int, start_id: int
) -> tuple[list[dict], int]:
    """Three exact concentric rings, each made from two thin cylinders."""
    blocks = []
    object_id = start_id
    unit_per_pixel = width_units / image_width
    for index, (radius_px, z) in enumerate(((1010.0, -0.010), (820.0, -0.014), (620.0, -0.018))):
        radius = radius_px * unit_per_pixel
        half_stroke = 18.0 * unit_per_pixel
        for suffix, diameter, color, layer_z in (
            ("outer", 2.0 * (radius + half_stroke), LIGHT_COLOR, z),
            ("cutout", 2.0 * (radius - half_stroke), BLACK, z - 0.002),
        ):
            blocks.append(
                primitive_block(
                    f"goc-ring-{index}-{suffix}",
                    object_id,
                    2,
                    (center_unity[0], center_unity[1], layer_z),
                    (90.0, 0.0, 0.0),
                    (diameter, 0.01, diameter),
                    color,
                )
            )
            object_id += 1
    return blocks, object_id


def meridian_blocks(
    center: tuple[float, float], image_width: int, image_height: int,
    width_units: float, start_id: int,
) -> tuple[list[dict], int]:
    """Four efficient diameter strips forming eight UN-style globe spokes."""
    cx, cy = center
    unit_per_pixel = width_units / image_width
    stroke_px = 36.0
    half_stroke = stroke_px * 0.5
    # Meet the inner edge of the outer 36 px ring without protruding through it.
    globe_radius_px = 1010.0 - 18.0
    half_length_px = math.sqrt(globe_radius_px**2 - half_stroke**2)
    center_unity = p2m.image_point_to_unity(center, image_width, image_height, width_units)
    blocks = []
    object_id = start_id
    for index, angle_degrees in enumerate((0.0, 45.0, 90.0, 135.0)):
        radians = math.radians(angle_degrees)
        dx = math.cos(radians) * half_length_px
        dy = math.sin(radians) * half_length_px
        start = (cx - dx, cy - dy)
        end = (cx + dx, cy + dy)
        start_u = p2m.image_point_to_unity(start, image_width, image_height, width_units)
        end_u = p2m.image_point_to_unity(end, image_width, image_height, width_units)
        world_dx, world_dy = end_u[0] - start_u[0], end_u[1] - start_u[1]
        world_length = math.hypot(world_dx, world_dy)
        rotation_z = math.degrees(math.atan2(world_dy, world_dx)) - 90.0
        blocks.append(
            primitive_block(
                f"goc-globe-meridian-{index}", object_id, 5,
                (center_unity[0], center_unity[1], -0.021),
                (0.0, 0.0, rotation_z),
                (stroke_px * unit_per_pixel, world_length, 1.0),
                LIGHT_COLOR,
            )
        )
        object_id += 1
    return blocks, object_id


def text_blocks(
    groups: dict[str, list[tuple[str, float, float, float]]],
    center: tuple[float, float],
    image_width: int,
    image_height: int,
    width_units: float,
    start_id: int,
) -> tuple[list[dict], int]:
    blocks = []
    object_id = start_id
    cx, cy = center

    for word_name, letters in groups.items():
        source_angles = [letter[3] for letter in letters]
        unwrapped = [source_angles[0]]
        for angle in source_angles[1:]:
            while angle - unwrapped[-1] > 180.0:
                angle -= 360.0
            while angle - unwrapped[-1] < -180.0:
                angle += 360.0
            unwrapped.append(angle)
        exact_angles = np.linspace(unwrapped[0], unwrapped[-1], len(letters))

        # Every transform lies on the same exact circle; only its polar angle
        # changes.  The radius includes the empirically required TextToy anchor
        # clearance, so glyphs do not clip the outer ring.
        text_radius_px = 1144.0
        for index, ((char, _x, _y, _source_angle), angle) in enumerate(zip(letters, exact_angles)):
            radians = math.radians(float(angle))
            x = cx + text_radius_px * math.cos(radians)
            y = cy - text_radius_px * math.sin(radians)
            ux, uy = p2m.image_point_to_unity((x, y), image_width, image_height, width_units)

            rotation_z = float(angle) + 90.0
            while rotation_z > 180.0:
                rotation_z -= 360.0
            while rotation_z <= -180.0:
                rotation_z += 360.0

            blocks.append(
                {
                    "Name": f"goc-text-{word_name}-{index:02d}-{char}",
                    "ObjectId": object_id,
                    "ParentId": 0,
                    "Position": vec3(ux, uy, -0.04),
                    "Rotation": vec3(0, 0, rotation_z),
                    "Scale": vec3(0.08, 0.08, 0.08),
                    "BlockType": 8,
                    "Properties": {
                        "Text": f"<align=center><size=20><b><color=#21519B>{char}</color></b></size></align>",
                        # SchematicBlockData multiplies this by 20 at runtime.
                        "DisplaySize": {"x": 1.5, "y": 0.4},
                        "Static": True,
                    },
                }
            )
            object_id += 1
    return blocks, object_id


def write_preview(
    blocks: list[dict], target: Path, name: str, image_width: int, image_height: int, width_units: float
) -> None:
    """Write a lightweight face-on preview, including approximate TextToy glyphs."""
    preview_width = 1000
    preview_height = round(preview_width * image_height / image_width)
    px_per_unit = preview_width / width_units
    height_units = width_units * image_height / image_width

    def screen(point: dict[str, float]) -> tuple[float, float]:
        return (
            (point["x"] + width_units * 0.5) * px_per_unit,
            (height_units * 0.5 - point["y"]) * px_per_unit,
        )

    canvas = Image.new("RGBA", (preview_width, preview_height), (0, 0, 0, 255))
    draw = ImageDraw.Draw(canvas)
    drawables = [block for block in blocks if block["BlockType"] in (1, 11)]
    drawables.sort(key=lambda block: -block["Position"]["z"])
    for block in drawables:
        properties = block["Properties"]
        color = properties["Color"]
        rgba = tuple(int(color[i : i + 2], 16) for i in (1, 3, 5)) + (255,)
        if block["BlockType"] == 11:
            points = []
            for key in ("PointA", "PointB", "PointC"):
                point = dict(properties[key])
                point["x"] += block["Position"]["x"]
                point["y"] += block["Position"]["y"]
                points.append(screen(point))
            draw.polygon(points, fill=rgba)
            continue

        primitive_type = properties["PrimitiveType"]
        cx, cy = screen(block["Position"])
        sx, sy, sz = (block["Scale"][key] * px_per_unit for key in ("x", "y", "z"))
        if primitive_type == 2:  # Cylinder face after the 90-degree X rotation.
            draw.ellipse((cx - sx / 2, cy - sz / 2, cx + sx / 2, cy + sz / 2), fill=rgba)
        elif primitive_type == 0:  # Flattened sphere used as one-toy laurel leaf.
            tile = Image.new("RGBA", (max(4, round(sx) + 8), max(4, round(sy) + 8)), (0, 0, 0, 0))
            td = ImageDraw.Draw(tile)
            td.ellipse((4, 4, tile.width - 4, tile.height - 4), fill=rgba)
            rotated = tile.rotate(-block["Rotation"]["z"], resample=Image.Resampling.BICUBIC, expand=True)
            canvas.alpha_composite(rotated, (round(cx - rotated.width / 2), round(cy - rotated.height / 2)))
        elif primitive_type == 5:  # Quad stem/ribbon.
            angle = math.radians(block["Rotation"]["z"])
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            points = []
            for lx, ly in ((-sx / 2, -sy / 2), (sx / 2, -sy / 2), (sx / 2, sy / 2), (-sx / 2, sy / 2)):
                points.append((cx + lx * cos_a - ly * sin_a, cy - (lx * sin_a + ly * cos_a)))
            draw.polygon(points, fill=rgba)

    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 21)
    except OSError:
        font = ImageFont.load_default()

    for block in (block for block in blocks if block["BlockType"] == 8):
        char = block["Name"].rsplit("-", 1)[-1]
        tile = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
        tile_draw = ImageDraw.Draw(tile)
        tile_draw.text((40, 40), char, font=font, fill=(33, 81, 155, 255), anchor="mm", stroke_width=0)
        rotated = tile.rotate(-block["Rotation"]["z"], resample=Image.Resampling.BICUBIC, expand=True)
        x, y = screen(block["Position"])
        canvas.alpha_composite(rotated, (round(x - rotated.width / 2), round(y - rotated.height / 2)))

    png_path = target / f"{name}.preview.png"
    canvas.convert("RGB").save(png_path)

    # Dedicated inspection crops catch cap/bar discontinuities and undersized
    # landmasses that are easy to miss in the full-emblem preview.
    star_crop = canvas.crop((270, 55, 730, 515)).resize((920, 920), Image.Resampling.LANCZOS)
    star_crop.convert("RGB").save(target / f"{name}.star-zoom.png")
    point_crop = canvas.crop((390, 65, 610, 365)).resize((880, 1200), Image.Resampling.LANCZOS)
    point_crop.convert("RGB").save(target / f"{name}.star-point-zoom.png")
    map_crop = canvas.crop((365, 165, 635, 435)).resize((810, 810), Image.Resampling.LANCZOS)
    map_crop.convert("RGB").save(target / f"{name}.map-zoom.png")

    min_x, min_y = -width_units * 0.5, -height_units * 0.5
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{min_x} {min_y} {width_units} {height_units}">',
        f'<rect x="{min_x}" y="{min_y}" width="{width_units}" height="{height_units}" fill="#000"/>',
    ]
    for block in drawables:
        properties = block["Properties"]
        fill = properties["Color"][:7]
        x, y = block["Position"]["x"], -block["Position"]["y"]
        if block["BlockType"] == 11:
            points = " ".join(
                f'{properties[key]["x"] + block["Position"]["x"]:.5f},'
                f'{-(properties[key]["y"] + block["Position"]["y"]):.5f}'
                for key in ("PointA", "PointB", "PointC")
            )
            svg.append(f'<polygon points="{points}" fill="{fill}"/>')
            continue
        primitive_type = properties["PrimitiveType"]
        sx, sy, sz = (block["Scale"][key] for key in ("x", "y", "z"))
        rotation = -block["Rotation"]["z"]
        if primitive_type == 2:
            svg.append(f'<ellipse cx="{x}" cy="{y}" rx="{sx/2}" ry="{sz/2}" fill="{fill}"/>')
        elif primitive_type == 0:
            svg.append(
                f'<ellipse cx="{x}" cy="{y}" rx="{sx/2}" ry="{sy/2}" '
                f'transform="rotate({rotation} {x} {y})" fill="{fill}"/>'
            )
        elif primitive_type == 5:
            svg.append(
                f'<rect x="{x-sx/2}" y="{y-sy/2}" width="{sx}" height="{sy}" '
                f'transform="rotate({rotation} {x} {y})" fill="{fill}"/>'
            )
    for block in (block for block in blocks if block["BlockType"] == 8):
        char = block["Name"].rsplit("-", 1)[-1]
        x, y = block["Position"]["x"], -block["Position"]["y"]
        rotation = -block["Rotation"]["z"]
        svg.append(
            f'<text x="{x}" y="{y}" transform="rotate({rotation:.5f} {x} {y})" '
            f'fill="#21519B" font-family="Arial,sans-serif" font-size="0.21" font-weight="bold" '
            f'text-anchor="middle" dominant-baseline="central">{char}</text>'
        )
    svg.append("</svg>")
    (target / f"{name}.preview.svg").write_text("\n".join(svg), encoding="utf-8")


def convert(
    image_path: Path,
    map_image_path: Path,
    output_dir: Path,
    name: str,
    width_units: float,
    simplify: float = 4.0,
) -> Path:
    image = np.asarray(Image.open(image_path).convert("RGB"))
    height, width = image.shape[:2]
    labels = classify(image)
    light = labels == 1
    _dark_art, letter_points = split_dark_components(labels == 2)

    center = fit_ring_center(letter_points)
    groups = group_letters(letter_points, center)

    # The outer laurel stays sourced from the original emblem. The inner map is
    # replaced wholesale from the user-supplied polar map image.
    yy, xx = np.ogrid[:height, :width]
    radius_sq = np.square(xx - center[0]) + np.square(yy - center[1])
    map_mask = replacement_map_mask(map_image_path, width, height, center)
    laurel_mask = light & (radius_sq > 1080.0**2)
    map_triangles = mask_triangles(map_mask, width, height, width_units, simplify=simplify)
    # Laurel pieces are much larger than map coastlines, so a 6px tolerance is
    # visually equivalent while avoiding hundreds of runtime toys.
    laurel_triangles = mask_triangles(
        laurel_mask, width, height, width_units, simplify=max(6.0, simplify)
    )

    center_unity = p2m.image_point_to_unity(center, width, height, width_units)
    blocks, next_id = circle_blocks(center_unity, width_units, width, 1)
    globe_meridians, next_id = meridian_blocks(center, width, height, width_units, next_id)
    blocks.extend(globe_meridians)
    map_blocks, next_id = triangle_blocks(map_triangles, next_id, f"{name}-map", LIGHT_COLOR, -0.022)
    blocks.extend(map_blocks)
    laurel_blocks, next_id = triangle_blocks(laurel_triangles, next_id, f"{name}-laurel", LIGHT_COLOR, -0.022)
    blocks.extend(laurel_blocks)
    star_blocks, next_id = mathematical_star_blocks(center, width, height, width_units, next_id)
    blocks.extend(star_blocks)
    labels_out, next_id = text_blocks(groups, center, width, height, width_units, next_id)
    blocks.extend(labels_out)

    target = output_dir / name
    target.mkdir(parents=True, exist_ok=True)
    output = target / f"{name}.json"
    output.write_text(json.dumps({"RootObjectId": 0, "Blocks": blocks}, separators=(",", ":")), encoding="utf-8")
    write_preview(blocks, target, name, width, height, width_units)

    primitive_count = sum(block["BlockType"] == 1 for block in blocks)
    triangle_count = sum(block["BlockType"] == 11 for block in blocks)
    star_cap_count = sum(block["Name"].startswith("goc-star-cap") for block in blocks)
    print(f"replacement map ({map_image_path.name}): {len(map_triangles)} native triangles")
    print(f"traced laurel: {len(laurel_triangles)} native triangles")
    print(f"mathematical pentagram: 5 quad bars + {star_cap_count} fitted cap triangles")
    print(f"mathematical rings/star bars: {primitive_count} primitives")
    print(f"curved labels: {len(labels_out)} TextToys")
    print(f"wrote {output}: {len(blocks)} schematic blocks, ~{triangle_count * 6 + primitive_count + len(labels_out)} runtime toys")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="GOC emblem -> native ProjectMER triangles + curved TextToys")
    parser.add_argument("image", type=Path)
    parser.add_argument("--map-image", type=Path, default=ROOT / "goc-map-source.jpg")
    parser.add_argument("--name", default="global-occult-coalition")
    parser.add_argument("--output", type=Path, default=ROOT / "converted_mer")
    parser.add_argument("--width", type=float, default=10.0)
    parser.add_argument("--simplify", type=float, default=4.0, help="High-detail map-only trace tolerance in source pixels")
    args = parser.parse_args()
    convert(args.image, args.map_image, args.output, p2m.clean_name(args.name), args.width, args.simplify)


if __name__ == "__main__":
    main()
