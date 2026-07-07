#!/usr/bin/env python3
"""Convert a high-contrast PNG into a ProjectMER schematic JSON.

The output is a schematic folder shaped like:

    converted_mer/<name>/<name>.json

Copy that folder into:

    LabAPI-beta/configs/ProjectMER/Schematics/

Filled polygons are triangulated (or merged into convex n-gons with --fill-mode
ngon) and expanded into standard MER quad primitives. The output uses only
vanilla BlockType 0 (Empty) and 1 (Primitive), so it loads on stock ProjectMER.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import mapbox_earcut as earcut
import numpy as np
from skimage.morphology import skeletonize

from mer_triangle_primitives import triangle_points_to_primitive_blocks, transform_point


@dataclass(frozen=True)
class BorderCylinderPair:
    center: tuple[float, float]
    outer_diameter: float
    inner_diameter: float
    center_px: tuple[float, float]
    outer_diameter_px: float
    inner_diameter_px: float


@dataclass(frozen=True)
class BridgeRect:
    center: tuple[float, float]
    size: tuple[float, float]
    angle: float
    color: str


@dataclass(frozen=True)
class TraceRectangle:
    center: tuple[float, float]
    size: tuple[float, float]
    angle: float
    color: str


@dataclass(frozen=True)
class TraceParallelogram:
    origin: tuple[float, float]
    edge_x: tuple[float, float]
    edge_y: tuple[float, float]
    color: str


@dataclass(frozen=True)
class ShearTransform2D:
    center: tuple[float, float]
    parent_angle: float
    parent_scale: tuple[float, float]
    child_angle: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Trace white pixels from a PNG and emit a ProjectMER schematic."
    )
    parser.add_argument("image", type=Path, help="Input PNG/JPG image.")
    parser.add_argument(
        "--name",
        default=None,
        help="Schematic name. Defaults to the input file stem.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("converted_mer"),
        help="Directory that will receive the schematic folder.",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=128,
        help="Grayscale threshold for foreground pixels, 0-255.",
    )
    parser.add_argument(
        "--foreground",
        choices=("light", "dark"),
        default="light",
        help="light traces pixels above --threshold; dark traces pixels below --threshold.",
    )
    parser.add_argument(
        "--width",
        type=float,
        default=10.0,
        help="Final schematic width in Unity units.",
    )
    parser.add_argument(
        "--simplify",
        type=float,
        default=3.0,
        help="Contour simplification tolerance in source pixels.",
    )
    parser.add_argument(
        "--min-area",
        type=float,
        default=20.0,
        help="Ignore source contours below this pixel area.",
    )
    parser.add_argument(
        "--min-triangle-area",
        type=float,
        default=0.000002,
        help="Ignore generated triangles below this Unity-unit area.",
    )
    parser.add_argument(
        "--flip-triangle-winding",
        action="store_true",
        help="Alias for --triangle-winding negative.",
    )
    parser.add_argument(
        "--triangle-winding",
        choices=("negative", "positive"),
        default="positive",
        help="Triangle face direction. positive is the conventional model winding; the MER runtime adapts Quad facing.",
    )
    parser.add_argument(
        "--fill-mode",
        choices=("triangle", "ngon"),
        default="triangle",
        help="triangle keeps per-triangle expansion; ngon merges triangles into convex pieces and covers them with far fewer parallelograms (TriangleScpSl NGonDecomposition port).",
    )
    parser.add_argument(
        "--thickness",
        type=float,
        default=0.02,
        help="Depth (Y scale) of the border cylinder primitives in Unity units.",
    )
    parser.add_argument(
        "--color",
        default="#FFFFFFFF",
        help="Flat MER color string for foreground fill (used when --color-source flat).",
    )
    parser.add_argument(
        "--color-source",
        choices=("flat", "image"),
        default="flat",
        help="flat fills every primitive with --color; image samples each primitive's colour from the source image so the original colours are preserved.",
    )
    parser.add_argument(
        "--color-sample-radius",
        type=int,
        default=2,
        help="Half-size (px) of the neighbourhood median sampled per primitive when --color-source image.",
    )
    parser.add_argument(
        "--trace-mode",
        choices=("polygon", "rectangle-first"),
        default="polygon",
        help="polygon keeps the existing filled-contour triangulation; rectangle-first strokes contour rings with edge rectangles.",
    )
    parser.add_argument(
        "--trace-source",
        choices=("boundary", "centerline"),
        default="boundary",
        help="Rectangle-first only: boundary traces region outlines; centerline traces skeletonized stroke centers.",
    )
    parser.add_argument(
        "--trace-width",
        type=float,
        default=None,
        help="2D stroke width in Unity units for --trace-mode rectangle-first.",
    )
    parser.add_argument(
        "--trace-width-px",
        type=float,
        default=None,
        help="2D stroke width in source pixels for --trace-mode rectangle-first.",
    )
    parser.add_argument(
        "--trace-miter-limit",
        type=float,
        default=4.0,
        help="Use a corner parallelogram when d_i exceeds this many trace half-widths.",
    )
    parser.add_argument(
        "--trace-z",
        type=float,
        default=-0.08,
        help="Z position for traced foreground geometry. Negative Z is front-facing for these generated logos.",
    )
    parser.add_argument(
        "--trace-merge-angle",
        type=float,
        default=0.0,
        help="Rectangle-first only: merge vertices whose local turn is within this many degrees.",
    )
    parser.add_argument(
        "--trace-merge-distance-px",
        type=float,
        default=0.0,
        help="Rectangle-first only: max source-pixel deviation allowed when merging roughly straight segments.",
    )
    parser.add_argument(
        "--trace-centerline-min-length-px",
        type=float,
        default=8.0,
        help="Rectangle-first centerline only: ignore skeleton paths shorter than this many source pixels.",
    )
    parser.add_argument(
        "--border-cylinders",
        action="store_true",
        help="Convert the largest circular border into two overlapping cylinder primitives.",
    )
    parser.add_argument(
        "--border-circle",
        default=None,
        help="Manual border circle as center_x,center_y,outer_diameter,inner_diameter in source pixels.",
    )
    parser.add_argument(
        "--border-remove-circle",
        default=None,
        help="Optional removal-only circle as center_x,center_y,outer_diameter,inner_diameter in source pixels.",
    )
    parser.add_argument(
        "--border-inner-color",
        default="#000000FF",
        help="MER color string for the inner border cylinder.",
    )
    parser.add_argument(
        "--border-outer-color",
        default=None,
        help="Optional MER color string for the outer border cylinder. Defaults to --color.",
    )
    parser.add_argument(
        "--border-z",
        type=float,
        default=0.0,
        help="Z position for the rear outer border cylinder.",
    )
    parser.add_argument(
        "--border-gap",
        type=float,
        default=-0.02,
        help="Z offset from outer to inner border cylinder. Must be negative so the inner cutout renders in front.",
    )
    parser.add_argument(
        "--border-mask-margin",
        type=float,
        default=10.0,
        help="Extra source pixels to subtract around the detected cylinder border.",
    )
    parser.add_argument(
        "--border-preserve-overlap",
        type=float,
        default=0.0,
        help="Source pixels of foreground to preserve into the border band near attached shapes.",
    )
    parser.add_argument(
        "--bridge-rect",
        action="append",
        default=[],
        help="Add a white cube bridge as x,y,width,height,angle_degrees[,color] in Unity units.",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Also write SVG and PNG previews beside the JSON.",
    )
    parser.add_argument(
        "--preview-size",
        type=int,
        default=2048,
        help="PNG preview width in pixels.",
    )
    parser.add_argument(
        "--preview-bg",
        default="#000000FF",
        help="Preview background color as #RRGGBB or #RRGGBBAA.",
    )
    return parser.parse_args()


def clean_name(raw: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw.strip())
    return name.strip("-") or "converted-logo"


def signed_area(points: list[tuple[float, float]]) -> float:
    total = 0.0
    for i, point in enumerate(points):
        nxt = points[(i + 1) % len(points)]
        total += (point[0] * nxt[1]) - (nxt[0] * point[1])
    return total * 0.5


def remove_duplicate_points(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    deduped: list[tuple[float, float]] = []
    for point in points:
        if not deduped or point != deduped[-1]:
            deduped.append(point)

    if len(deduped) > 1 and deduped[0] == deduped[-1]:
        deduped.pop()

    return deduped


def contour_to_points(contour: np.ndarray, simplify_px: float) -> list[tuple[float, float]]:
    approx = cv2.approxPolyDP(contour, simplify_px, True)
    points = [(float(p[0][0]), float(p[0][1])) for p in approx]
    points = remove_duplicate_points(points)
    if len(points) < 3 or abs(signed_area(points)) < 0.5:
        return []
    return points


def image_point_to_unity(
    point: tuple[float, float],
    image_width: int,
    image_height: int,
    width_units: float,
) -> tuple[float, float]:
    scale = width_units / image_width
    x = (point[0] - (image_width * 0.5)) * scale
    y = ((image_height * 0.5) - point[1]) * scale
    return (x, y)


def round_vec(x: float, y: float, z: float = 0.0) -> dict[str, float]:
    return {"x": round(x, 5), "y": round(y, 5), "z": round(z, 5)}


def css_color(value: str) -> str:
    if value.startswith("#") and len(value) == 9:
        return value[:7]
    return value


def color_to_bgr(value: str) -> tuple[int, int, int]:
    raw = value.strip()
    if raw.startswith("#"):
        raw = raw[1:]
    if len(raw) not in (6, 8):
        raise ValueError(f"Expected #RRGGBB or #RRGGBBAA color, got: {value}")
    red = int(raw[0:2], 16)
    green = int(raw[2:4], 16)
    blue = int(raw[4:6], 16)
    return (blue, green, red)


def parse_bridge_rects(values: list[str], default_color: str) -> list[BridgeRect]:
    bridges = []
    for value in values:
        parts = [part.strip() for part in value.split(",")]
        if len(parts) not in (5, 6):
            raise ValueError("--bridge-rect must be x,y,width,height,angle_degrees[,color]")

        x, y, width, height, angle = (float(part) for part in parts[:5])
        color = parts[5] if len(parts) == 6 else default_color
        bridges.append(BridgeRect((x, y), (width, height), angle, color))

    return bridges


def triangle_area(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
) -> float:
    return abs(((b[0] - a[0]) * (c[1] - a[1])) - ((c[0] - a[0]) * (b[1] - a[1]))) * 0.5


def add2(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    return (a[0] + b[0], a[1] + b[1])


def sub2(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    return (a[0] - b[0], a[1] - b[1])


def mul2(a: tuple[float, float], scalar: float) -> tuple[float, float]:
    return (a[0] * scalar, a[1] * scalar)


def dot2(a: tuple[float, float], b: tuple[float, float]) -> float:
    return (a[0] * b[0]) + (a[1] * b[1])


def cross2(a: tuple[float, float], b: tuple[float, float]) -> float:
    return (a[0] * b[1]) - (a[1] * b[0])


def length2(a: tuple[float, float]) -> float:
    return math.hypot(a[0], a[1])


def normalize2(a: tuple[float, float]) -> tuple[float, float]:
    length = length2(a)
    if length <= 0.0:
        return (0.0, 0.0)
    return (a[0] / length, a[1] / length)


def left_normal(a: tuple[float, float]) -> tuple[float, float]:
    return (-a[1], a[0])


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def point_line_distance(
    point: tuple[float, float],
    line_a: tuple[float, float],
    line_b: tuple[float, float],
) -> float:
    line = sub2(line_b, line_a)
    length = length2(line)
    if length <= 0.000001:
        return length2(sub2(point, line_a))

    return abs(cross2(line, sub2(point, line_a))) / length


def remove_near_duplicate_points(
    points: list[tuple[float, float]],
    epsilon: float = 0.000001,
    closed: bool = True,
) -> list[tuple[float, float]]:
    deduped: list[tuple[float, float]] = []
    for point in points:
        if not deduped or length2(sub2(point, deduped[-1])) > epsilon:
            deduped.append(point)

    if closed and len(deduped) > 1 and length2(sub2(deduped[0], deduped[-1])) <= epsilon:
        deduped.pop()

    return deduped


def merge_roughly_straight_segments(
    points: list[tuple[float, float]],
    max_turn_degrees: float,
    max_deviation: float,
) -> list[tuple[float, float]]:
    if len(points) <= 3 or max_turn_degrees <= 0.0 or max_deviation <= 0.0:
        return points

    max_turn = math.radians(max_turn_degrees)
    merged = points[:]
    changed = True
    while changed and len(merged) > 3:
        changed = False
        for index in range(len(merged)):
            prev_point = merged[index - 1]
            point = merged[index]
            next_point = merged[(index + 1) % len(merged)]
            incoming = normalize2(sub2(point, prev_point))
            outgoing = normalize2(sub2(next_point, point))
            if incoming == (0.0, 0.0) or outgoing == (0.0, 0.0):
                continue

            turn = abs(math.atan2(cross2(incoming, outgoing), clamp(dot2(incoming, outgoing), -1.0, 1.0)))
            if turn > max_turn:
                continue

            if point_line_distance(point, prev_point, next_point) > max_deviation:
                continue

            del merged[index]
            changed = True
            break

    return merged


def get_svd_2x2(
    a: float,
    b: float,
    c: float,
    d: float,
) -> tuple[float, float, float, float] | None:
    b00 = (a * a) + (c * c)
    b01 = (a * b) + (c * d)
    b11 = (b * b) + (d * d)

    trace = b00 + b11
    diff = b00 - b11
    root = math.sqrt((diff * diff) + (4.0 * b01 * b01))
    lambda_x = max((trace + root) * 0.5, 0.0)
    lambda_y = max((trace - root) * 0.5, 0.0)

    singular_x = math.sqrt(lambda_x)
    singular_y = math.sqrt(lambda_y)
    if singular_x < 0.000001 or singular_y < 0.000001:
        return None

    if abs(b01) > 0.000001:
        v1 = normalize2((lambda_x - b11, b01))
    else:
        v1 = (1.0, 0.0) if b00 >= b11 else (0.0, 1.0)

    av1 = ((a * v1[0]) + (b * v1[1]), (c * v1[0]) + (d * v1[1]))
    u1 = (av1[0] / singular_x, av1[1] / singular_x)

    v_angle = math.atan2(v1[1], v1[0])
    u_angle = math.atan2(u1[1], u1[0])

    return (u_angle, singular_x, singular_y, v_angle)


def get_shear_transform_2d(
    origin: tuple[float, float],
    edge_x: tuple[float, float],
    edge_y: tuple[float, float],
) -> ShearTransform2D | None:
    if length2(edge_x) < 0.000001 or length2(edge_y) < 0.000001:
        return None

    if abs(cross2(edge_x, edge_y)) < 0.000001:
        return None

    if cross2(edge_x, edge_y) < 0.0:
        edge_x, edge_y = edge_y, edge_x

    center = add2(origin, mul2(add2(edge_x, edge_y), 0.5))
    basis_x = normalize2(edge_x)
    basis_y = left_normal(basis_x)

    m00 = length2(edge_x)
    m01 = dot2(edge_y, basis_x)
    m11 = dot2(edge_y, basis_y)

    svd = get_svd_2x2(m00, m01, 0.0, m11)
    if svd is None:
        return None

    u_angle, singular_x, singular_y, v_angle = svd
    basis_angle = math.atan2(basis_x[1], basis_x[0])

    return ShearTransform2D(
        center,
        math.degrees(basis_angle + u_angle),
        (singular_x, singular_y),
        math.degrees(-v_angle),
    )


def trace_path_rectangle_first(
    path_px: list[tuple[float, float]],
    closed: bool,
    image_width: int,
    image_height: int,
    width_units: float,
    trace_width: float,
    miter_limit: float,
    merge_angle_degrees: float,
    merge_distance: float,
    color: str,
) -> tuple[list[TraceRectangle], list[TraceParallelogram]]:
    points = remove_near_duplicate_points(
        [image_point_to_unity(point, image_width, image_height, width_units) for point in path_px],
        closed=closed,
    )
    points = merge_roughly_straight_segments(points, merge_angle_degrees, merge_distance)
    if len(points) < (3 if closed else 2):
        return [], []

    half_width = trace_width * 0.5
    tangents: list[tuple[float, float]] = []
    lengths: list[float] = []
    segment_count = len(points) if closed else len(points) - 1
    for index in range(segment_count):
        point = points[index]
        nxt = points[(index + 1) % len(points)]
        edge = sub2(nxt, point)
        length = length2(edge)
        if length < 0.000001:
            return [], []

        tangents.append((edge[0] / length, edge[1] / length))
        lengths.append(length)

    extensions: list[float] = [0.0] * len(points)
    use_corner_parallelogram: list[bool] = [False] * len(points)

    first_join = 0 if closed else 1
    last_join = len(points) if closed else len(points) - 1
    for index in range(first_join, last_join):
        incoming = tangents[index - 1]
        outgoing = tangents[index % len(tangents)]
        turn = math.atan2(cross2(incoming, outgoing), clamp(dot2(incoming, outgoing), -1.0, 1.0))
        abs_turn = abs(turn)
        if abs_turn < 0.000001:
            continue

        extension = half_width * abs(math.tan(abs_turn * 0.5))
        if extension > (miter_limit * half_width):
            use_corner_parallelogram[index] = True
        else:
            extensions[index] = extension

    rectangles: list[TraceRectangle] = []
    parallelograms: list[TraceParallelogram] = []
    for index in range(segment_count):
        point = points[index]
        nxt = points[(index + 1) % len(points)]
        tangent = tangents[index]
        end_index = (index + 1) % len(points)
        start_extension = extensions[index] if closed or index > 0 else half_width
        end_extension = extensions[end_index] if closed or end_index < len(points) - 1 else half_width
        start = sub2(point, mul2(tangent, start_extension))
        end = add2(nxt, mul2(tangent, end_extension))
        center = mul2(add2(start, end), 0.5)
        length = lengths[index] + start_extension + end_extension
        if length >= 0.000001:
            rectangles.append(
                TraceRectangle(
                    center,
                    (length, trace_width),
                    math.degrees(math.atan2(tangent[1], tangent[0])),
                    color,
                )
            )

    for index in range(first_join, last_join):
        if use_corner_parallelogram[index]:
            prev_normal = left_normal(tangents[index - 1])
            next_normal = left_normal(tangents[index % len(tangents)])
            vertex = points[index]
            a = add2(vertex, mul2(prev_normal, half_width))
            b = add2(vertex, mul2(next_normal, half_width))
            d = sub2(vertex, mul2(next_normal, half_width))
            parallelograms.append(
                TraceParallelogram(
                    a,
                    sub2(b, a),
                    sub2(d, a),
                    color,
                )
            )

    return rectangles, parallelograms


def trace_ring_rectangle_first(
    ring_px: list[tuple[float, float]],
    image_width: int,
    image_height: int,
    width_units: float,
    trace_width: float,
    miter_limit: float,
    merge_angle_degrees: float,
    merge_distance: float,
    color: str,
) -> tuple[list[TraceRectangle], list[TraceParallelogram]]:
    return trace_path_rectangle_first(
        ring_px,
        True,
        image_width,
        image_height,
        width_units,
        trace_width,
        miter_limit,
        merge_angle_degrees,
        merge_distance,
        color,
    )


def trace_polygons_rectangle_first(
    polygons: list[list[list[tuple[float, float]]]],
    image_width: int,
    image_height: int,
    width_units: float,
    trace_width: float,
    miter_limit: float,
    merge_angle_degrees: float,
    merge_distance: float,
    color: str,
) -> tuple[list[TraceRectangle], list[TraceParallelogram]]:
    rectangles: list[TraceRectangle] = []
    parallelograms: list[TraceParallelogram] = []
    for rings in polygons:
        for ring in rings:
            ring_rectangles, ring_parallelograms = trace_ring_rectangle_first(
                ring,
                image_width,
                image_height,
                width_units,
                trace_width,
                miter_limit,
                merge_angle_degrees,
                merge_distance,
                color,
            )
            rectangles.extend(ring_rectangles)
            parallelograms.extend(ring_parallelograms)

    return rectangles, parallelograms


SKELETON_NEIGHBORS = (
    (-1, -1),
    (0, -1),
    (1, -1),
    (-1, 0),
    (1, 0),
    (-1, 1),
    (0, 1),
    (1, 1),
)


def skeleton_neighbors(
    point: tuple[int, int],
    pixels: set[tuple[int, int]],
) -> list[tuple[int, int]]:
    x, y = point
    return [
        (x + dx, y + dy)
        for dx, dy in SKELETON_NEIGHBORS
        if (x + dx, y + dy) in pixels
    ]


def skeleton_edge_key(
    a: tuple[int, int],
    b: tuple[int, int],
) -> tuple[tuple[int, int], tuple[int, int]]:
    return (a, b) if a <= b else (b, a)


def pixel_path_length(path: list[tuple[int, int]]) -> float:
    total = 0.0
    for index in range(len(path) - 1):
        total += math.hypot(path[index + 1][0] - path[index][0], path[index + 1][1] - path[index][1])
    return total


def simplify_pixel_path(
    path: list[tuple[int, int]],
    simplify_px: float,
    closed: bool,
) -> list[tuple[float, float]]:
    if len(path) < (3 if closed else 2):
        return []

    points = np.array(path, dtype=np.float32).reshape((-1, 1, 2))
    approx = cv2.approxPolyDP(points, simplify_px, closed)
    simplified = [(float(point[0][0]), float(point[0][1])) for point in approx]
    return remove_near_duplicate_points(simplified, epsilon=0.001, closed=closed)


def extract_skeleton_paths(
    mask: np.ndarray,
    min_length_px: float,
) -> list[tuple[list[tuple[int, int]], bool]]:
    skeleton = skeletonize(mask > 0)
    y_values, x_values = np.nonzero(skeleton)
    pixels = {(int(x), int(y)) for x, y in zip(x_values, y_values)}
    if not pixels:
        return []

    neighbor_cache = {point: skeleton_neighbors(point, pixels) for point in pixels}
    nodes = {point for point, neighbors in neighbor_cache.items() if len(neighbors) != 2}
    visited_edges: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    paths: list[tuple[list[tuple[int, int]], bool]] = []

    def add_path(path: list[tuple[int, int]], closed: bool) -> None:
        if len(path) < (3 if closed else 2):
            return
        if pixel_path_length(path + ([path[0]] if closed else [])) < min_length_px:
            return
        paths.append((path, closed))

    for node in sorted(nodes):
        for neighbor in sorted(neighbor_cache[node]):
            edge = skeleton_edge_key(node, neighbor)
            if edge in visited_edges:
                continue

            visited_edges.add(edge)
            path = [node, neighbor]
            previous = node
            current = neighbor

            while current not in nodes:
                candidates = [candidate for candidate in neighbor_cache[current] if candidate != previous]
                if not candidates:
                    break

                unvisited = [
                    candidate
                    for candidate in candidates
                    if skeleton_edge_key(current, candidate) not in visited_edges
                ]
                next_point = sorted(unvisited or candidates)[0]
                visited_edges.add(skeleton_edge_key(current, next_point))
                path.append(next_point)
                previous, current = current, next_point

            add_path(path, False)

    for start in sorted(pixels):
        for neighbor in sorted(neighbor_cache[start]):
            edge = skeleton_edge_key(start, neighbor)
            if edge in visited_edges:
                continue

            visited_edges.add(edge)
            path = [start, neighbor]
            previous = start
            current = neighbor
            closed = False

            while True:
                candidates = [candidate for candidate in neighbor_cache[current] if candidate != previous]
                if not candidates:
                    break

                if start in candidates:
                    visited_edges.add(skeleton_edge_key(current, start))
                    closed = True
                    break

                unvisited = [
                    candidate
                    for candidate in candidates
                    if skeleton_edge_key(current, candidate) not in visited_edges
                ]
                if not unvisited:
                    break

                next_point = sorted(unvisited)[0]
                visited_edges.add(skeleton_edge_key(current, next_point))
                path.append(next_point)
                previous, current = current, next_point

            add_path(path, closed)

    return paths


def trace_centerlines_rectangle_first(
    mask: np.ndarray,
    image_width: int,
    image_height: int,
    width_units: float,
    trace_width: float,
    miter_limit: float,
    simplify_px: float,
    merge_angle_degrees: float,
    merge_distance: float,
    min_length_px: float,
    color: str,
) -> tuple[list[TraceRectangle], list[TraceParallelogram]]:
    rectangles: list[TraceRectangle] = []
    parallelograms: list[TraceParallelogram] = []
    for path, closed in extract_skeleton_paths(mask, min_length_px):
        simplified = simplify_pixel_path(path, simplify_px, closed)
        path_rectangles, path_parallelograms = trace_path_rectangle_first(
            simplified,
            closed,
            image_width,
            image_height,
            width_units,
            trace_width,
            miter_limit,
            merge_angle_degrees,
            merge_distance,
            color,
        )
        rectangles.extend(path_rectangles)
        parallelograms.extend(path_parallelograms)

    return rectangles, parallelograms


def get_child_indices(hierarchy: np.ndarray, parent_index: int) -> list[int]:
    indices = []
    child = hierarchy[parent_index][2]
    while child != -1:
        indices.append(int(child))
        child = hierarchy[child][0]
    return indices


def detect_border_index(
    contours: tuple[np.ndarray, ...],
    hierarchy: np.ndarray,
    image_width: int,
    image_height: int,
) -> int | None:
    candidates: list[tuple[float, int]] = []
    for index, contour in enumerate(contours):
        if hierarchy[index][3] != -1:
            continue
        if hierarchy[index][2] == -1:
            continue

        x, y, w, h = cv2.boundingRect(contour)
        if w < image_width * 0.5 or h < image_height * 0.5:
            continue

        area = abs(cv2.contourArea(contour))
        candidates.append((area, index))

    if not candidates:
        return None

    return max(candidates)[1]


def get_border_cylinders(
    contour: np.ndarray,
    child_contours: list[np.ndarray],
    image_width: int,
    image_height: int,
    width_units: float,
) -> BorderCylinderPair | None:
	if not child_contours:
		return None

	inner = max(child_contours, key=lambda child: abs(cv2.contourArea(child)))
	outer_center_raw, outer_radius_px = cv2.minEnclosingCircle(contour)
	inner_center_raw, inner_radius_px = cv2.minEnclosingCircle(inner)
	outer_center_px = (float(outer_center_raw[0]), float(outer_center_raw[1]))
	inner_center_px = (float(inner_center_raw[0]), float(inner_center_raw[1]))
	center_px = (
		(outer_center_px[0] + inner_center_px[0]) * 0.5,
		(outer_center_px[1] + inner_center_px[1]) * 0.5,
	)
	center = image_point_to_unity(center_px, image_width, image_height, width_units)
	scale = width_units / image_width
	outer_diameter_px = float(outer_radius_px) * 2
	inner_diameter_px = float(inner_radius_px) * 2
	outer_diameter = outer_diameter_px * scale
	inner_diameter = inner_diameter_px * scale

	if outer_diameter <= 0 or inner_diameter <= 0 or inner_diameter >= outer_diameter:
		return None

	return BorderCylinderPair(
		center,
		outer_diameter,
		inner_diameter,
		center_px,
		outer_diameter_px,
		inner_diameter_px,
	)


def parse_border_circle(
    value: str,
    image_width: int,
    image_height: int,
    width_units: float,
) -> BorderCylinderPair:
    parts = [float(part.strip()) for part in value.split(",")]
    if len(parts) != 4:
        raise ValueError("--border-circle must be center_x,center_y,outer_diameter,inner_diameter")

    center_px = (parts[0], parts[1])
    outer_diameter_px = parts[2]
    inner_diameter_px = parts[3]
    if outer_diameter_px <= 0 or inner_diameter_px <= 0 or inner_diameter_px >= outer_diameter_px:
        raise ValueError("--border-circle diameters must be positive and inner must be smaller than outer")

    center = image_point_to_unity(center_px, image_width, image_height, width_units)
    scale = width_units / image_width
    return BorderCylinderPair(
        center,
        outer_diameter_px * scale,
        inner_diameter_px * scale,
        center_px,
        outer_diameter_px,
        inner_diameter_px,
    )


def subtract_border_from_mask(
    mask: np.ndarray,
    border: BorderCylinderPair,
    margin_px: float,
    preserve_overlap_px: float,
) -> np.ndarray:
    ring_mask = np.zeros_like(mask)
    center = (round(border.center_px[0]), round(border.center_px[1]))
    outer_radius = max(1, round((border.outer_diameter_px * 0.5) + margin_px))
    inner_radius = max(1, round((border.inner_diameter_px * 0.5) - margin_px))

    cv2.circle(ring_mask, center, outer_radius, 255, thickness=-1, lineType=cv2.LINE_AA)
    cv2.circle(ring_mask, center, inner_radius, 0, thickness=-1, lineType=cv2.LINE_AA)

    without_ring = cv2.bitwise_and(mask, cv2.bitwise_not(ring_mask))
    if preserve_overlap_px <= 0:
        return without_ring

    kernel_size = (round(preserve_overlap_px) * 2) + 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    near_foreground = cv2.dilate(without_ring, kernel)
    preserved_overlap = cv2.bitwise_and(mask, cv2.bitwise_and(ring_mask, near_foreground))

    return cv2.bitwise_or(without_ring, preserved_overlap)


def expand_removal_border(mask: np.ndarray, border: BorderCylinderPair, margin_px: float) -> BorderCylinderPair:
    """Use the manual border center, but expand the delete band to cover raster border leftovers."""
    y_values, x_values = np.nonzero(mask)
    if len(x_values) == 0:
        return border

    distances = np.sqrt((x_values - border.center_px[0]) ** 2 + (y_values - border.center_px[1]) ** 2)
    final_outer_radius = border.outer_diameter_px * 0.5
    search = distances[
        (distances >= final_outer_radius - max(10.0, margin_px))
        & (distances <= final_outer_radius + max(90.0, margin_px * 3.0))
    ]
    if len(search) < 100:
        return border

    expanded_outer_radius = max(final_outer_radius, float(np.quantile(search, 0.995)))
    expanded_outer_diameter_px = expanded_outer_radius * 2.0
    scale = border.outer_diameter / border.outer_diameter_px

    return BorderCylinderPair(
        border.center,
        expanded_outer_diameter_px * scale,
        border.inner_diameter,
        border.center_px,
        expanded_outer_diameter_px,
        border.inner_diameter_px,
    )


def extract_rings(
    args: argparse.Namespace,
) -> tuple[list[list[list[tuple[float, float]]]], BorderCylinderPair | None, int, int, np.ndarray, np.ndarray]:
    image = cv2.imread(str(args.image), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise FileNotFoundError(f"Could not read image: {args.image}")

    alpha_mask = None
    if image.ndim == 3 and image.shape[2] == 4:
        alpha_mask = image[:, :, 3]
        gray = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2GRAY)
    elif image.ndim == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image

    threshold_type = cv2.THRESH_BINARY if args.foreground == "light" else cv2.THRESH_BINARY_INV
    _, mask = cv2.threshold(gray, args.threshold, 255, threshold_type)
    if alpha_mask is not None:
        mask = cv2.bitwise_and(mask, mask, mask=alpha_mask)
    polygons: list[list[list[tuple[float, float]]]] = []
    image_width = int(image.shape[1])
    image_height = int(image.shape[0])
    border = None

    if args.border_cylinders:
        if args.border_circle:
            border = parse_border_circle(args.border_circle, image_width, image_height, args.width)
            removal_border = (
                parse_border_circle(args.border_remove_circle, image_width, image_height, args.width)
                if args.border_remove_circle
                else border
            )
            removal_border = expand_removal_border(mask, removal_border, args.border_mask_margin)
            mask = subtract_border_from_mask(
                mask,
                removal_border,
                args.border_mask_margin,
                args.border_preserve_overlap,
            )
        else:
            contours, hierarchy_raw = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
            if hierarchy_raw is not None:
                hierarchy = hierarchy_raw[0]
                border_index = detect_border_index(contours, hierarchy, image_width, image_height)
                if border_index is not None:
                    children = get_child_indices(hierarchy, border_index)
                    border = get_border_cylinders(
                        contours[border_index],
                        [contours[child] for child in children],
                        image_width,
                        image_height,
                        args.width,
                    )
                    if border is not None:
                        mask = subtract_border_from_mask(
                            mask,
                            border,
                            args.border_mask_margin,
                            args.border_preserve_overlap,
                        )

    contours, hierarchy_raw = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy_raw is None:
        return [], border, image_width, image_height, mask, image

    hierarchy = hierarchy_raw[0]

    for index, contour in enumerate(contours):
        parent = hierarchy[index][3]
        if parent != -1:
            continue

        children = get_child_indices(hierarchy, index)

        if abs(cv2.contourArea(contour)) < args.min_area:
            continue

        shell = contour_to_points(contour, args.simplify)
        if not shell:
            continue

        rings = [shell]
        for child in children:
            child_contour = contours[child]
            if abs(cv2.contourArea(child_contour)) >= args.min_area:
                hole = contour_to_points(child_contour, args.simplify)
                if hole:
                    rings.append(hole)

        polygons.append(rings)

    return polygons, border, image_width, image_height, mask, image


def triangulate_rings(
    rings_px: list[list[tuple[float, float]]],
    image_width: int,
    image_height: int,
    width_units: float,
    min_triangle_area: float,
    flip_winding: bool,
) -> list[tuple[tuple[float, float], tuple[float, float], tuple[float, float]]]:
    unity_rings: list[list[tuple[float, float]]] = []
    for ring in rings_px:
        unity_ring = [
            image_point_to_unity(point, image_width, image_height, width_units)
            for point in ring
        ]
        if len(unity_ring) >= 3 and not math.isclose(signed_area(unity_ring), 0.0):
            unity_rings.append(unity_ring)

    if not unity_rings:
        return []

    vertices = np.array([point for ring in unity_rings for point in ring], dtype=np.float64)
    ring_ends = np.cumsum([len(ring) for ring in unity_rings], dtype=np.uint32)
    indices = earcut.triangulate_float64(vertices, ring_ends)

    triangles = []
    for i in range(0, len(indices), 3):
        a = tuple(vertices[int(indices[i])])
        b = tuple(vertices[int(indices[i + 1])])
        c = tuple(vertices[int(indices[i + 2])])
        if triangle_area(a, b, c) >= min_triangle_area:
            if signed_area([a, b, c]) < 0:
                b, c = c, b
            if flip_winding:
                b, c = c, b
            triangles.append((a, b, c))

    return triangles


def triangle_parallelogram_tiles(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    # The ProjectMER runtime swaps B/C internally before building quad tiles.
    b, c = c, b
    midpoint_ab = mul2(add2(a, b), 0.5)
    midpoint_bc = mul2(add2(b, c), 0.5)
    midpoint_ca = mul2(add2(c, a), 0.5)

    center_a = mul2(add2(a, midpoint_bc), 0.5)
    center_b = mul2(add2(b, midpoint_ca), 0.5)
    center_c = mul2(add2(c, midpoint_ab), 0.5)

    return [
        (sub2(midpoint_ca, center_a), sub2(a, center_a)),
        (sub2(midpoint_ab, center_b), sub2(b, center_b)),
        (sub2(midpoint_bc, center_c), sub2(c, center_c)),
    ]


def is_rectangle_tile(v_up: tuple[float, float], v_left: tuple[float, float]) -> bool:
    up_length = length2(v_up)
    left_length = length2(v_left)
    if up_length < 0.000001 or left_length < 0.000001:
        return False

    return abs(up_length - left_length) <= max(up_length, left_length) * 0.0001


def estimate_shear_scale(v_up: tuple[float, float], v_left: tuple[float, float]) -> float | None:
    up_sqr = dot2(v_up, v_up)
    left_sqr = dot2(v_left, v_left)
    if up_sqr < 0.000000000001 or left_sqr < 0.000000000001:
        return None

    dot_abs = abs(dot2(v_left, v_up))
    swap_epsilon = max(up_sqr * 0.000001, 0.000000001)
    if dot_abs >= up_sqr - swap_epsilon:
        old_up = v_up
        v_up = v_left
        v_left = mul2(old_up, -1.0)

    up_length = length2(v_up)
    up_normal = mul2(v_up, 1.0 / up_length)
    left_y = clamp(dot2(v_left, up_normal), -up_length, up_length)
    projected = sub2(v_left, mul2(up_normal, left_y))
    left_x = length2(projected)
    if left_x < 0.000000000001:
        return None

    a = math.sqrt(max(2.0 * up_length * (up_length + left_y), 0.000000000001))
    b = math.sqrt(max(2.0 * up_length * (up_length - left_y), 0.000000000001))
    x = left_x * 2.0 * up_length / max(a * b, 0.000000000001)
    return max(abs(a), abs(b), abs(x), abs(a * x), abs(b * x))


def estimate_triangle_runtime_toys(
    triangles: list[tuple[tuple[float, float], tuple[float, float], tuple[float, float]]],
) -> tuple[int, int, float]:
    toys = 0
    rectangle_tiles = 0
    max_shear_scale = 0.0

    for triangle in triangles:
        for v_up, v_left in triangle_parallelogram_tiles(*triangle):
            if is_rectangle_tile(v_up, v_left):
                rectangle_tiles += 1
                toys += 1
                continue

            toys += 2
            shear_scale = estimate_shear_scale(v_up, v_left)
            if shear_scale is not None:
                max_shear_scale = max(max_shear_scale, shear_scale)

    return toys, rectangle_tiles, max_shear_scale


# Role prefixes (the block-name segment AFTER the schematic name) that mark a
# primitive as traced foreground fill, safe to recolour from the source image.
# Matching the suffix — not the full name — keeps this immune to schematic names
# that happen to contain these tokens, and border/bridge suffixes (border-*,
# bridge-*) are naturally excluded so they keep their own configured colours.
FILL_ROLE_PREFIXES = ("tri-", "par-", "tile-", "trace-")


def _source_to_bgr(image: np.ndarray) -> np.ndarray:
    """Normalise a cv2-read image (gray / BGR / BGRA) to a contiguous BGR uint8 array."""
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.shape[2] == 4:
        return np.ascontiguousarray(image[:, :, :3])
    return np.ascontiguousarray(image[:, :, :3])


def build_color_sampler(
    image: np.ndarray,
    mask: np.ndarray,
    image_width: int,
    image_height: int,
    width_units: float,
    radius: int,
):
    """Return sample(x, y) -> '#RRGGBBAA', reading the source colour at the Unity
    point (x, y). Uses the median of foreground pixels in a (2*radius+1)^2 window
    so a centroid that lands on a gap/edge pixel does not grab a stray colour."""
    bgr = _source_to_bgr(image)
    height, width = bgr.shape[:2]
    radius = max(0, int(radius))
    scale = width_units / image_width if image_width else 1.0

    def sample(x: float, y: float) -> str:
        # Quantise to the 5-decimal precision the schematic stores block Positions
        # at (round_vec/round_vec3), so a preview centroid and the corresponding
        # rounded JSON block Position map to the exact same source pixel.
        x, y = round(x, 5), round(y, 5)
        px = int(round((x / scale) + (image_width * 0.5))) if scale else 0
        py = int(round((image_height * 0.5) - (y / scale))) if scale else 0
        px = min(max(px, 0), width - 1)
        py = min(max(py, 0), height - 1)
        x0, x1 = max(px - radius, 0), min(px + radius + 1, width)
        y0, y1 = max(py - radius, 0), min(py + radius + 1, height)
        window = bgr[y0:y1, x0:x1].reshape(-1, 3)
        window_mask = mask[y0:y1, x0:x1].reshape(-1)
        foreground = window[window_mask > 0]
        pixels = foreground if foreground.size else window
        b, g, r = (int(round(v)) for v in np.median(pixels, axis=0))
        return f"#{r:02X}{g:02X}{b:02X}FF"

    return sample


def triangle_preview_tiles(
    triangle: tuple[tuple[float, float], tuple[float, float], tuple[float, float]],
    color: str,
) -> list[TraceParallelogram]:
    """Expand a fill triangle into the same 3 medial parallelogram tiles the JSON
    emits, so the preview draws (and colour-samples) each tile at the exact centre
    its JSON quad uses — keeping preview colours consistent with the per-tile JSON.

    Mirrors the tile geometry of triangle_parallelogram_tiles / the build path
    (including the B/C swap), and also returns each tile's centre so the drawn
    parallelogram's centroid equals the JSON quad's Position."""
    a, b, c = triangle
    b, c = c, b  # the runtime swaps B/C before building the quad tiles
    midpoint_ab = mul2(add2(a, b), 0.5)
    midpoint_bc = mul2(add2(b, c), 0.5)
    midpoint_ca = mul2(add2(c, a), 0.5)
    center_a = mul2(add2(a, midpoint_bc), 0.5)
    center_b = mul2(add2(b, midpoint_ca), 0.5)
    center_c = mul2(add2(c, midpoint_ab), 0.5)
    spec = [
        (sub2(midpoint_ca, center_a), sub2(a, center_a), center_a),
        (sub2(midpoint_ab, center_b), sub2(b, center_b), center_b),
        (sub2(midpoint_bc, center_c), sub2(c, center_c), center_c),
    ]
    tiles: list[TraceParallelogram] = []
    for v_up, v_left, center in spec:
        origin = sub2(center, v_left)
        edge_x = sub2(v_left, v_up)
        edge_y = add2(v_up, v_left)
        tiles.append(TraceParallelogram(origin, edge_x, edge_y, color))
    return tiles


def recolor_fill_blocks(schematic: dict, sample, name: str) -> int:
    """Recolour every traced foreground fill primitive from the source image.

    A quad's world centre must be resolved through its parent: sheared tiles are
    children of an Empty shear-parent, so their own Position is LOCAL, not the
    absolute Unity coordinate the sampler expects. The hierarchy is at most two
    deep (root -> shear-parent -> quad, or root -> quad), and shear-parents sit
    at the schematic root, so composing the parent's TRS once yields the world
    centre. Returns the number of primitives recoloured."""
    blocks = schematic.get("Blocks", [])
    by_id = {block.get("ObjectId"): block for block in blocks}
    prefix = f"{name}-"

    def is_fill(block_name: str) -> bool:
        # Classify by the role segment after the schematic name so a schematic
        # name that contains a role token can't misclassify border/bridge blocks.
        suffix = block_name[len(prefix):] if block_name.startswith(prefix) else block_name
        return suffix.startswith(FILL_ROLE_PREFIXES)

    def vec(value, default=(0.0, 0.0, 0.0)):
        if not isinstance(value, dict):
            return default
        return (float(value.get("x", default[0])), float(value.get("y", default[1])), float(value.get("z", default[2])))

    def world_xy(block):
        local = vec(block.get("Position"))
        parent = by_id.get(block.get("ParentId"))
        if parent is None:  # parented directly to the schematic root -> Position is absolute
            return local[0], local[1]
        world = transform_point(
            local, vec(parent.get("Position")), vec(parent.get("Rotation")),
            vec(parent.get("Scale"), (1.0, 1.0, 1.0)),
        )
        return world[0], world[1]

    changed = 0
    for block in blocks:
        if block.get("BlockType") != 1:
            continue
        if not is_fill(block.get("Name", "")):
            continue
        properties = block.get("Properties")
        if not isinstance(properties, dict) or "Color" not in properties:
            continue
        x, y = world_xy(block)
        properties["Color"] = sample(x, y)
        changed += 1
    return changed


def build_schematic(
    name: str,
    triangles: list[tuple[tuple[float, float], tuple[float, float], tuple[float, float]]],
    trace_rectangles: list[TraceRectangle],
    trace_parallelograms: list[TraceParallelogram],
    border: BorderCylinderPair | None,
    bridges: list[BridgeRect],
    color: str,
    border_outer_color: str | None,
    border_inner_color: str,
    thickness: float,
    trace_z: float,
    border_z: float,
    border_gap: float,
    fill_blocks: list[dict] | None = None,
) -> dict[str, object]:
    blocks = []
    next_id = 1
    if fill_blocks:
        blocks.extend(fill_blocks)
        next_id = 1 + max(int(block["ObjectId"]) for block in fill_blocks)

    for index, (a, b, c) in enumerate(triangles, start=1):
        triangle_blocks, next_id, _ = triangle_points_to_primitive_blocks(
            f"{name}-tri-{index:05d}",
            next_id,
            0,
            (a[0], a[1], trace_z),
            (b[0], b[1], trace_z),
            (c[0], c[1], trace_z),
            color,
            True,
        )
        blocks.extend(triangle_blocks)

    for index, rectangle in enumerate(trace_rectangles, start=1):
        blocks.append(
            {
                "Name": f"{name}-trace-rect-{index:05d}",
                "ObjectId": next_id,
                "ParentId": 0,
                "Position": round_vec(rectangle.center[0], rectangle.center[1], trace_z),
                "Rotation": round_vec(0.0, 0.0, rectangle.angle),
                "Scale": round_vec(rectangle.size[0], rectangle.size[1], 1.0),
                "BlockType": 1,
                "Properties": {
                    "PrimitiveType": 5,
                    "PrimitiveFlags": 2,  # SCP:SL enum: None=0, Collidable=1, Visible=2
                    "Color": rectangle.color,
                    "Static": True,
                },
            }
        )
        next_id += 1

    for index, parallelogram in enumerate(trace_parallelograms, start=1):
        shear = get_shear_transform_2d(
            parallelogram.origin,
            parallelogram.edge_x,
            parallelogram.edge_y,
        )
        if shear is None:
            continue

        parent_id = next_id
        blocks.append(
            {
                "Name": f"{name}-trace-corner-{index:05d}-shear",
                "ObjectId": parent_id,
                "ParentId": 0,
                "Position": round_vec(shear.center[0], shear.center[1], trace_z),
                "Rotation": round_vec(0.0, 0.0, shear.parent_angle),
                "Scale": round_vec(shear.parent_scale[0], shear.parent_scale[1], 1.0),
                "BlockType": 0,
                "Properties": {
                    "Static": True,
                },
            }
        )
        next_id += 1

        blocks.append(
            {
                "Name": f"{name}-trace-corner-{index:05d}",
                "ObjectId": next_id,
                "ParentId": parent_id,
                "Position": round_vec(0.0, 0.0, 0.0),
                "Rotation": round_vec(0.0, 0.0, shear.child_angle),
                "Scale": round_vec(1.0, 1.0, 1.0),
                "BlockType": 1,
                "Properties": {
                    "PrimitiveType": 5,
                    "PrimitiveFlags": 2,  # SCP:SL enum: None=0, Collidable=1, Visible=2
                    "Color": parallelogram.color,
                    "Static": True,
                },
            }
        )
        next_id += 1

    for index, bridge in enumerate(bridges, start=1):
        blocks.append(
            {
                "Name": f"{name}-bridge-{index:02d}",
                "ObjectId": next_id,
                "ParentId": 0,
                "Position": round_vec(bridge.center[0], bridge.center[1], 0.03),
                "Rotation": round_vec(0.0, 0.0, bridge.angle),
                "Scale": round_vec(bridge.size[0], bridge.size[1], 1.0),
                "BlockType": 1,
                "Properties": {
                    "PrimitiveType": 5,
                    "PrimitiveFlags": 2,  # SCP:SL enum: None=0, Collidable=1, Visible=2
                    "Color": bridge.color,
                    "Static": True,
                },
            }
        )
        next_id += 1

    if border is not None:
        outer_color = border_outer_color if border_outer_color is not None else color
        for suffix, diameter, primitive_color, z in (
            ("border-outer", border.outer_diameter, outer_color, border_z),
            ("border-inner", border.inner_diameter, border_inner_color, border_z + border_gap),
        ):
            blocks.append(
                {
                    "Name": f"{name}-{suffix}",
                    "ObjectId": next_id,
                    "ParentId": 0,
                    "Position": round_vec(border.center[0], border.center[1], z),
                    "Rotation": round_vec(90.0, 0.0, 0.0),
                    "Scale": round_vec(diameter, thickness, diameter),
                    "BlockType": 1,
                    "Properties": {
                        "PrimitiveType": 2,
                        "PrimitiveFlags": 2,  # SCP:SL enum: None=0, Collidable=1, Visible=2
                        "Color": primitive_color,
                        "Static": True,
                    },
                }
            )
            next_id += 1

    return {"RootObjectId": 0, "Blocks": blocks}


def write_preview(
    path: Path,
    triangles: list[tuple[tuple[float, float], tuple[float, float], tuple[float, float]]],
    trace_rectangles: list[TraceRectangle],
    trace_parallelograms: list[TraceParallelogram],
    border: BorderCylinderPair | None,
    bridges: list[BridgeRect],
    width_units: float,
    image_width: int,
    image_height: int,
    color: str,
    border_outer_color: str | None,
    border_inner_color: str,
    preview_bg: str,
    color_fn=None,
) -> None:
    height_units = width_units * (image_height / image_width)
    min_x = -width_units * 0.5
    min_y = -height_units * 0.5

    def fill_attr(cx: float, cy: float) -> str:
        return f' fill="{css_color(color_fn(cx, cy))}"' if color_fn else ""

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{min_x} {min_y} {width_units} {height_units}">',
        f'<rect x="{min_x}" y="{min_y}" width="{width_units}" height="{height_units}" fill="{css_color(preview_bg)}"/>',
    ]

    lines.append(f'<g fill="{css_color(color)}" stroke="none">')
    for bridge in bridges:
        cx, cy = bridge.center
        width, height = bridge.size
        angle = math.radians(-bridge.angle)
        corners = [
            (-width * 0.5, -height * 0.5),
            (width * 0.5, -height * 0.5),
            (width * 0.5, height * 0.5),
            (-width * 0.5, height * 0.5),
        ]
        points = []
        for x, y in corners:
            rx = (x * math.cos(angle)) - (y * math.sin(angle))
            ry = (x * math.sin(angle)) + (y * math.cos(angle))
            points.append(f"{cx + rx:.5f},{-(cy + ry):.5f}")
        lines.append(f'<polygon points="{" ".join(points)}" fill="{css_color(bridge.color)}"/>')

    for rectangle in trace_rectangles:
        cx, cy = rectangle.center
        width, height = rectangle.size
        angle = math.radians(-rectangle.angle)
        corners = [
            (-width * 0.5, -height * 0.5),
            (width * 0.5, -height * 0.5),
            (width * 0.5, height * 0.5),
            (-width * 0.5, height * 0.5),
        ]
        points = []
        for x, y in corners:
            rx = (x * math.cos(angle)) - (y * math.sin(angle))
            ry = (x * math.sin(angle)) + (y * math.cos(angle))
            points.append(f"{cx + rx:.5f},{-(cy + ry):.5f}")
        lines.append(f'<polygon points="{" ".join(points)}"{fill_attr(cx, cy)}/>')

    for parallelogram in trace_parallelograms:
        a = parallelogram.origin
        b = add2(a, parallelogram.edge_x)
        c = add2(b, parallelogram.edge_y)
        d = add2(a, parallelogram.edge_y)
        mx = a[0] + (parallelogram.edge_x[0] + parallelogram.edge_y[0]) * 0.5
        my = a[1] + (parallelogram.edge_x[1] + parallelogram.edge_y[1]) * 0.5
        points = " ".join(f"{x:.5f},{-y:.5f}" for x, y in (a, b, c, d))
        lines.append(f'<polygon points="{points}"{fill_attr(mx, my)}/>')

    for a, b, c in triangles:
        # SVG y grows downward, so invert the Unity y coordinates for preview.
        mx = (a[0] + b[0] + c[0]) / 3.0
        my = (a[1] + b[1] + c[1]) / 3.0
        points = " ".join(f"{x:.5f},{-y:.5f}" for x, y in (a, b, c))
        lines.append(f'<polygon points="{points}"{fill_attr(mx, my)}/>')
    lines.append("</g>")
    if border is not None:
        cx, cy = border.center
        outer_radius = border.outer_diameter * 0.5
        inner_radius = border.inner_diameter * 0.5
        stroke_width = max(outer_radius - inner_radius, 0.0001)
        stroke_radius = (outer_radius + inner_radius) * 0.5
        stroke_color = css_color(border_outer_color if border_outer_color is not None else color)
        lines.append(
            f'<circle cx="{cx:.5f}" cy="{-cy:.5f}" r="{stroke_radius:.5f}" fill="none" stroke="{stroke_color}" stroke-width="{stroke_width:.5f}"/>'
        )
    lines.append("</svg>")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_png_preview(
    path: Path,
    triangles: list[tuple[tuple[float, float], tuple[float, float], tuple[float, float]]],
    trace_rectangles: list[TraceRectangle],
    trace_parallelograms: list[TraceParallelogram],
    border: BorderCylinderPair | None,
    bridges: list[BridgeRect],
    width_units: float,
    image_width: int,
    image_height: int,
    preview_width: int,
    color: str,
    border_outer_color: str | None,
    border_inner_color: str,
    preview_bg: str,
    color_fn=None,
) -> None:
    preview_height = max(1, round(preview_width * (image_height / image_width)))
    height_units = width_units * (image_height / image_width)
    canvas = np.zeros((preview_height, preview_width, 3), dtype=np.uint8)
    canvas[:, :] = color_to_bgr(preview_bg)
    foreground_bgr = color_to_bgr(color)
    border_bgr = color_to_bgr(border_outer_color if border_outer_color is not None else color)

    def fill_bgr(cx: float, cy: float) -> tuple[int, int, int]:
        return color_to_bgr(color_fn(cx, cy)) if color_fn else foreground_bgr

    def to_pixel(point: tuple[float, float]) -> tuple[int, int]:
        x, y = point
        px = round(((x / width_units) + 0.5) * (preview_width - 1))
        py = round((0.5 - (y / height_units)) * (preview_height - 1))
        return (int(px), int(py))

    for bridge in bridges:
        cx, cy = bridge.center
        width, height = bridge.size
        angle = math.radians(bridge.angle)
        corners = [
            (-width * 0.5, -height * 0.5),
            (width * 0.5, -height * 0.5),
            (width * 0.5, height * 0.5),
            (-width * 0.5, height * 0.5),
        ]
        points = []
        for x, y in corners:
            rx = (x * math.cos(angle)) - (y * math.sin(angle))
            ry = (x * math.sin(angle)) + (y * math.cos(angle))
            points.append(to_pixel((cx + rx, cy + ry)))
        cv2.fillConvexPoly(canvas, np.array(points, dtype=np.int32), color_to_bgr(bridge.color), lineType=cv2.LINE_AA)

    for rectangle in trace_rectangles:
        cx, cy = rectangle.center
        width, height = rectangle.size
        angle = math.radians(rectangle.angle)
        corners = [
            (-width * 0.5, -height * 0.5),
            (width * 0.5, -height * 0.5),
            (width * 0.5, height * 0.5),
            (-width * 0.5, height * 0.5),
        ]
        points = []
        for x, y in corners:
            rx = (x * math.cos(angle)) - (y * math.sin(angle))
            ry = (x * math.sin(angle)) + (y * math.cos(angle))
            points.append(to_pixel((cx + rx, cy + ry)))
        cv2.fillConvexPoly(canvas, np.array(points, dtype=np.int32), fill_bgr(cx, cy), lineType=cv2.LINE_AA)

    for parallelogram in trace_parallelograms:
        a = parallelogram.origin
        b = add2(a, parallelogram.edge_x)
        c = add2(b, parallelogram.edge_y)
        d = add2(a, parallelogram.edge_y)
        mx = a[0] + (parallelogram.edge_x[0] + parallelogram.edge_y[0]) * 0.5
        my = a[1] + (parallelogram.edge_x[1] + parallelogram.edge_y[1]) * 0.5
        points = np.array([to_pixel(point) for point in (a, b, c, d)], dtype=np.int32)
        cv2.fillConvexPoly(canvas, points, fill_bgr(mx, my), lineType=cv2.LINE_AA)

    for triangle in triangles:
        mx = (triangle[0][0] + triangle[1][0] + triangle[2][0]) / 3.0
        my = (triangle[0][1] + triangle[1][1] + triangle[2][1]) / 3.0
        points = np.array([to_pixel(point) for point in triangle], dtype=np.int32)
        cv2.fillConvexPoly(canvas, points, fill_bgr(mx, my), lineType=cv2.LINE_AA)

    if border is not None:
        center_px = to_pixel(border.center)
        outer_radius_px = (border.outer_diameter / width_units) * preview_width * 0.5
        inner_radius_px = (border.inner_diameter / width_units) * preview_width * 0.5
        stroke_radius_px = round((outer_radius_px + inner_radius_px) * 0.5)
        stroke_width_px = max(1, round(outer_radius_px - inner_radius_px))
        cv2.circle(canvas, center_px, stroke_radius_px, border_bgr, thickness=stroke_width_px, lineType=cv2.LINE_AA)

    cv2.imwrite(str(path), canvas)


def resolve_trace_width(args: argparse.Namespace, image_width: int) -> float:
    if args.trace_width is not None and args.trace_width_px is not None:
        raise ValueError("Use only one of --trace-width or --trace-width-px")

    if args.trace_width is not None:
        trace_width = args.trace_width
    elif args.trace_width_px is not None:
        trace_width = args.trace_width_px * (args.width / image_width)
    else:
        trace_width = args.width / image_width

    if trace_width <= 0:
        raise ValueError("Rectangle-first trace width must be positive")

    return trace_width


def resolve_trace_merge_distance(args: argparse.Namespace, image_width: int) -> float:
    if args.trace_merge_distance_px <= 0.0:
        return 0.0

    return args.trace_merge_distance_px * (args.width / image_width)


def validate_layer_order(args: argparse.Namespace) -> None:
    if not args.border_cylinders:
        return

    inner_z = args.border_z + args.border_gap
    if args.border_gap >= 0:
        raise ValueError(
            "--border-gap must be negative so the inner cutout cylinder is in front of the outer border cylinder"
        )

    clearance = max(abs(args.thickness) * 2.0, 0.02)
    if args.trace_z >= inner_z - clearance:
        raise ValueError(
            "--trace-z must be in front of the border cylinders; use a value below "
            f"{inner_z - clearance:.5f} for the current border/thickness settings"
        )


def main() -> None:
    args = parse_args()
    if not args.image.exists():
        raise FileNotFoundError(args.image)
    if not 0 <= args.threshold <= 255:
        raise ValueError("--threshold must be between 0 and 255")
    if args.width <= 0:
        raise ValueError("--width must be positive")
    if args.trace_mode == "rectangle-first" and args.trace_miter_limit <= 0:
        raise ValueError("--trace-miter-limit must be positive")
    if args.trace_mode == "rectangle-first" and args.trace_merge_angle < 0:
        raise ValueError("--trace-merge-angle must be non-negative")
    if args.trace_mode == "rectangle-first" and args.trace_merge_distance_px < 0:
        raise ValueError("--trace-merge-distance-px must be non-negative")
    if args.trace_source == "centerline" and args.trace_mode != "rectangle-first":
        raise ValueError("--trace-source centerline requires --trace-mode rectangle-first")
    if args.trace_mode == "rectangle-first" and args.trace_centerline_min_length_px < 0:
        raise ValueError("--trace-centerline-min-length-px must be non-negative")
    validate_layer_order(args)

    name = clean_name(args.name or args.image.stem)
    bridges = parse_bridge_rects(args.bridge_rect, args.color)
    polygons, border, image_width, image_height, mask, source_image = extract_rings(args)

    triangles = []
    trace_rectangles: list[TraceRectangle] = []
    trace_parallelograms: list[TraceParallelogram] = []
    trace_width = 0.0
    if args.trace_mode == "rectangle-first":
        trace_width = resolve_trace_width(args, image_width)
        trace_merge_distance = resolve_trace_merge_distance(args, image_width)
        if args.trace_source == "centerline":
            trace_rectangles, trace_parallelograms = trace_centerlines_rectangle_first(
                mask,
                image_width,
                image_height,
                args.width,
                trace_width,
                args.trace_miter_limit,
                args.simplify,
                args.trace_merge_angle,
                trace_merge_distance,
                args.trace_centerline_min_length_px,
                args.color,
            )
        else:
            trace_rectangles, trace_parallelograms = trace_polygons_rectangle_first(
                polygons,
                image_width,
                image_height,
                args.width,
                trace_width,
                args.trace_miter_limit,
                args.trace_merge_angle,
                trace_merge_distance,
                args.color,
            )
    fill_blocks: list[dict] | None = None
    ngon_stats = None
    preview_triangles: list = []
    preview_parallelograms: list[TraceParallelogram] = []
    if args.trace_mode == "polygon" and args.fill_mode == "ngon":
        from mer_ngon_decomposition import rings_to_parallelogram_blocks

        unity_polygons = []
        for rings in polygons:
            unity_polygons.append([
                [image_point_to_unity(point, image_width, image_height, args.width) for point in ring]
                for ring in rings
            ])
        fill_blocks, _, ngon_stats, ngon_preview = rings_to_parallelogram_blocks(
            unity_polygons,
            mask,
            image_width,
            image_height,
            args.width,
            args.trace_z,
            args.color,
            name,
            min_triangle_area=args.min_triangle_area,
        )
        preview_triangles = ngon_preview["triangles"]
        preview_parallelograms = [
            TraceParallelogram(origin, edge_x, edge_y, args.color)
            for origin, edge_x, edge_y in ngon_preview["parallelograms"]
        ]
    elif args.trace_mode == "polygon":
        for rings in polygons:
            triangles.extend(
                triangulate_rings(
                    rings,
                    image_width,
                    image_height,
                    args.width,
                    args.min_triangle_area,
                    args.flip_triangle_winding or args.triangle_winding == "negative",
                )
            )

    out_dir = args.output / name
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{name}.json"
    schematic = build_schematic(
        name,
        triangles,
        trace_rectangles,
        trace_parallelograms,
        border,
        bridges,
        args.color,
        args.border_outer_color,
        args.border_inner_color,
        args.thickness,
        args.trace_z,
        args.border_z,
        args.border_gap,
        fill_blocks,
    )

    color_fn = None
    recolored = 0
    if args.color_source == "image":
        color_fn = build_color_sampler(
            source_image, mask, image_width, image_height, args.width, args.color_sample_radius
        )
        recolored = recolor_fill_blocks(schematic, color_fn, name)

    json_path.write_text(json.dumps(schematic, separators=(",", ":")), encoding="utf-8")

    if args.preview:
        preview_triangle_arg = triangles + preview_triangles
        preview_parallelogram_arg = trace_parallelograms + preview_parallelograms
        if color_fn is not None:
            # In image-colour mode, draw each fill triangle as the same 3 medial
            # tiles the JSON emits so preview colours match the per-tile JSON
            # exactly (a whole-triangle preview would show one averaged colour).
            fill_tiles = []
            for tri in preview_triangle_arg:
                fill_tiles.extend(triangle_preview_tiles(tri, args.color))
            preview_triangle_arg = []
            preview_parallelogram_arg = preview_parallelogram_arg + fill_tiles
        write_preview(
            out_dir / f"{name}.preview.svg",
            preview_triangle_arg,
            trace_rectangles,
            preview_parallelogram_arg,
            border,
            bridges,
            args.width,
            image_width,
            image_height,
            args.color,
            args.border_outer_color,
            args.border_inner_color,
            args.preview_bg,
            color_fn,
        )
        write_png_preview(
            out_dir / f"{name}.preview.png",
            preview_triangle_arg,
            trace_rectangles,
            preview_parallelogram_arg,
            border,
            bridges,
            args.width,
            image_width,
            image_height,
            args.preview_size,
            args.color,
            args.border_outer_color,
            args.border_inner_color,
            args.preview_bg,
            color_fn,
        )

    print(f"Wrote: {json_path}")
    if args.color_source == "image":
        print(f"Colour source: image ({recolored} primitives recoloured from source)")
    print(f"Foreground polygons: {len(polygons)}")
    print(f"Border cylinders: {2 if border is not None else 0}")
    print(f"Bridge primitives: {len(bridges)}")
    if args.trace_mode == "rectangle-first":
        print(f"Trace width: {trace_width:.5f}")
        print(f"Trace edge rectangles: {len(trace_rectangles)}")
        print(f"Trace corner parallelograms: {len(trace_parallelograms)}")
    if ngon_stats is not None:
        print(f"NGon decomposition: {ngon_stats.line()}")
        trace_toys = len(trace_rectangles) + (len(trace_parallelograms) * 2)
        print(f"Runtime primitive toys: about {ngon_stats.toys + trace_toys + len(bridges) + (2 if border is not None else 0)}")
        return
    print(f"Source triangles: {len(triangles)}")
    triangle_toys, rectangle_tiles, max_shear_scale = estimate_triangle_runtime_toys(triangles)
    print(
        "Expanded triangle primitives: about "
        f"{triangle_toys} ({rectangle_tiles} direct rectangle tiles; max shear scale {max_shear_scale:.3f})"
    )
    trace_toys = len(trace_rectangles) + (len(trace_parallelograms) * 2)
    print(f"Runtime primitive toys: about {triangle_toys + trace_toys + len(bridges) + (2 if border is not None else 0)}")


if __name__ == "__main__":
    main()
