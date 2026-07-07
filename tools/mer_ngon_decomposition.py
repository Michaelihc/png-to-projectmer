"""2D port of TriangleScpSl's n-gon parallelogram decomposition.

Turns filled polygons (with holes) into far fewer primitive toys than the
per-triangle medial split: earcut triangles are greedily merged back into
convex pieces (Hertel-Mehlhorn), each piece is covered by parallelograms via
parallel-sides peeling and fourth-vertex construction peeling, and tail
triangles collapse to a single parallelogram when their reflection stays
inside the same-color raster region (the 2D analog of TriangleScpSl's
hidden-tail-inside-solid test; overlapping coplanar same-color quads render
identically, so in-region overlap is free).

Sources ported: TriangleScpSl/Core/Decomposition/NGonDecomposition/
Merging/ConvexNGonDecomposer.cs and Parallelogram/HiddenTailParallelogramProcessor*.cs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import mapbox_earcut as earcut
import numpy as np

from mer_triangle_primitives import (
    triangle_points_to_primitive_blocks,
    try_build_rectangle_tile,
    try_get_shear_transforms,
    round_vec3,
    BLOCK_EMPTY,
    BLOCK_PRIMITIVE,
    PRIMITIVE_QUAD,
    PRIMITIVE_FLAGS_VISIBLE,
)

SIN_EPS = 0.005
LENGTH_EPS = 0.001
CONVEX_EPS = 0.0000001


@dataclass
class NGonStats:
    source_triangles: int = 0
    convex_pieces: int = 0
    whole_quads: int = 0
    rect_covers: int = 0
    peeled: int = 0
    hidden_tails: int = 0
    medial_tails: int = 0
    toys: int = 0

    def line(self) -> str:
        return (f"{self.source_triangles} triangles -> {self.convex_pieces} convex pieces "
                f"({self.whole_quads} whole quads, {self.rect_covers} rect covers, "
                f"{self.peeled} peels, {self.hidden_tails} hidden tails, "
                f"{self.medial_tails} medial tails) -> {self.toys} toys")


Vec2 = tuple[float, float]


def _sub(a: Vec2, b: Vec2) -> Vec2:
    return (a[0] - b[0], a[1] - b[1])


def _cross(a: Vec2, b: Vec2) -> float:
    return (a[0] * b[1]) - (a[1] * b[0])


def _dot(a: Vec2, b: Vec2) -> float:
    return (a[0] * b[0]) + (a[1] * b[1])


def _len(a: Vec2) -> float:
    return math.hypot(a[0], a[1])


def _is_convex(points: list[Vec2]) -> bool:
    n = len(points)
    if n < 3:
        return False
    got_pos = got_neg = False
    for i in range(n):
        a, b, c = points[i], points[(i + 1) % n], points[(i + 2) % n]
        cross = _cross(_sub(b, a), _sub(c, b))
        if cross > CONVEX_EPS:
            got_pos = True
        elif cross < -CONVEX_EPS:
            got_neg = True
        if got_pos and got_neg:
            return False
    return True


def _are_parallel_and_equal(a: Vec2, b: Vec2) -> bool:
    la, lb = _len(a), _len(b)
    if la < 1e-7 or lb < 1e-7:
        return False
    if abs(_cross(a, b)) / (la * lb) >= SIN_EPS:
        return False
    if _dot(a, b) <= 0.0:
        return False
    return abs(la - lb) / max(la, lb) < LENGTH_EPS


def _inside_convex_ccw(p: Vec2, poly: list[Vec2], eps: float) -> bool:
    n = len(poly)
    for i in range(n):
        if _cross(_sub(poly[(i + 1) % n], poly[i]), _sub(p, poly[i])) < -eps:
            return False
    return True


def _signed_area(points: list[Vec2]) -> float:
    total = 0.0
    for i, point in enumerate(points):
        nxt = points[(i + 1) % len(points)]
        total += (point[0] * nxt[1]) - (nxt[0] * point[1])
    return total * 0.5


# ---------- Hertel-Mehlhorn merge of an earcut triangulation ----------

def _hertel_mehlhorn(vertices: list[Vec2], triangles: list[tuple[int, int, int]]) -> list[list[int]]:
    """Greedily remove diagonals shared by two pieces while the union stays convex."""
    pieces: dict[int, list[int]] = {i: list(t) for i, t in enumerate(triangles)}
    edge_owner: dict[tuple[int, int], int] = {}
    for pid, piece in pieces.items():
        for k in range(len(piece)):
            edge_owner[(piece[k], piece[(k + 1) % len(piece)])] = pid

    def try_merge(p1: list[int], p2: list[int]) -> list[int] | None:
        n1, n2 = len(p1), len(p2)
        for i in range(n1):
            a, b = p1[i], p1[(i + 1) % n1]
            for j in range(n2):
                if p2[j] == b and p2[(j + 1) % n2] == a:
                    merged = [p1[i]]
                    cur = (j + 2) % n2
                    while True:
                        merged.append(p2[cur])
                        if cur == j:
                            break
                        cur = (cur + 1) % n2
                    p1_end = (i - 1) % n1
                    cur = (i + 2) % n1
                    if cur != i:
                        while True:
                            if cur == (i + 1) % n1:
                                break
                            merged.append(p1[cur])
                            if cur == p1_end:
                                break
                            cur = (cur + 1) % n1
                    if len(merged) >= 3 and _is_convex([vertices[k] for k in merged]):
                        return merged
                    return None
        return None

    diagonals = [
        (u, v) for (u, v) in edge_owner
        if u < v and (v, u) in edge_owner
    ]
    for u, v in diagonals:
        pid1 = edge_owner.get((u, v))
        pid2 = edge_owner.get((v, u))
        if pid1 is None or pid2 is None or pid1 == pid2:
            continue
        if pid1 not in pieces or pid2 not in pieces:
            continue
        merged = try_merge(pieces[pid1], pieces[pid2])
        if merged is None:
            continue
        del pieces[pid2]
        pieces[pid1] = merged
        for k in range(len(merged)):
            edge_owner[(merged[k], merged[(k + 1) % len(merged)])] = pid1

    return list(pieces.values())


def _drop_collinear(points: list[Vec2]) -> list[Vec2]:
    result = points[:]
    changed = True
    while changed and len(result) > 3:
        changed = False
        for i in range(len(result)):
            a = result[i - 1]
            b = result[i]
            c = result[(i + 1) % len(result)]
            ab, bc = _sub(b, a), _sub(c, b)
            lab, lbc = _len(ab), _len(bc)
            if lab < 1e-9 or lbc < 1e-9 or abs(_cross(ab, bc)) / (lab * lbc) < 0.0005:
                del result[i]
                changed = True
                break
    return result


# ---------- raster region ("solid") test ----------

class RegionMask:
    """Tests whether small polygons lie inside a color layer's raster mask."""

    def __init__(self, mask: np.ndarray, image_width: int, image_height: int, width_units: float):
        self.outside = (np.asarray(mask) == 0)
        self.w = image_width
        self.h = image_height
        self.scale = image_width / width_units  # unity -> px

    def to_px(self, p: Vec2) -> tuple[float, float]:
        return (p[0] * self.scale + self.w * 0.5, self.h * 0.5 - p[1] * self.scale)

    def contains_polygon(self, points: list[Vec2], pull_in: float = 0.02,
                         max_outside_frac: float = 0.005) -> bool:
        cx = sum(p[0] for p in points) / len(points)
        cy = sum(p[1] for p in points) / len(points)
        shrunk = [((p[0] - cx) * (1.0 - pull_in) + cx, (p[1] - cy) * (1.0 - pull_in) + cy)
                  for p in points]
        px = np.array([self.to_px(p) for p in shrunk], np.float64)
        x0 = max(0, int(np.floor(px[:, 0].min())))
        y0 = max(0, int(np.floor(px[:, 1].min())))
        x1 = min(self.w - 1, int(np.ceil(px[:, 0].max())))
        y1 = min(self.h - 1, int(np.ceil(px[:, 1].max())))
        if px[:, 0].min() < -0.5 or px[:, 1].min() < -0.5 or \
                px[:, 0].max() > self.w - 0.5 or px[:, 1].max() > self.h - 0.5:
            return False
        if x1 < x0 or y1 < y0:
            return True
        window = np.zeros((y1 - y0 + 1, x1 - x0 + 1), np.uint8)
        local = np.round(px - [x0, y0]).astype(np.int32)
        cv2.fillPoly(window, [local], 1)
        covered = window.astype(bool)
        total = int(covered.sum())
        if total == 0:
            return True
        outside = int((covered & self.outside[y0:y1 + 1, x0:x1 + 1]).sum())
        return outside <= max(1, total * max_outside_frac)


# ---------- parallelogram emission ----------

class _Emitter:
    def __init__(self, name_prefix: str, start_id: int, parent_id: int, z: float, color: str,
                 stats: NGonStats):
        self.prefix = name_prefix
        self.next_id = start_id
        self.parent_id = parent_id
        self.z = z
        self.color = color
        self.stats = stats
        self.blocks: list[dict] = []
        self.count = 0
        self.preview_parallelograms: list[tuple[Vec2, Vec2, Vec2]] = []  # origin, edge_x, edge_y
        self.preview_triangles: list[tuple[Vec2, Vec2, Vec2]] = []

    def emit_parallelogram(self, center: Vec2, v_left: Vec2, v_up: Vec2) -> None:
        """center + half-diagonals, matching ModelParallelogram semantics."""
        # match the facing convention of the triangle pipeline (see medial tiles)
        if _cross(v_up, v_left) > 0.0:
            v_up = (-v_up[0], -v_up[1])
        corner_a = (center[0] - v_left[0], center[1] - v_left[1])
        corner_b = (center[0] - v_up[0], center[1] - v_up[1])
        corner_d = (center[0] + v_up[0], center[1] + v_up[1])
        self.preview_parallelograms.append((corner_a, _sub(corner_b, corner_a), _sub(corner_d, corner_a)))
        self.count += 1
        name = f"{self.prefix}-par-{self.count:05d}"
        v_up3 = (v_up[0], v_up[1], 0.0)
        v_left3 = (v_left[0], v_left[1], 0.0)
        center3 = (center[0], center[1], self.z)

        rectangle = try_build_rectangle_tile(name, self.next_id, self.parent_id,
                                             v_up3, v_left3, center3, self.color, True)
        if rectangle is not None:
            self.blocks.append(rectangle)
            self.next_id += 1
            self.stats.toys += 1
            return

        shear = try_get_shear_transforms(v_up3, v_left3)
        if shear is None:
            return
        parent_rotation, parent_scale, child_angle, child_scale = shear
        shear_id = self.next_id
        self.blocks.append({
            "Name": f"{name}-shear", "ObjectId": shear_id, "ParentId": self.parent_id,
            "Position": round_vec3(center3), "Rotation": round_vec3(parent_rotation),
            "Scale": round_vec3(parent_scale), "BlockType": BLOCK_EMPTY,
            "Properties": {"Static": True},
        })
        self.next_id += 1
        self.blocks.append({
            "Name": name, "ObjectId": self.next_id, "ParentId": shear_id,
            "Position": round_vec3((0.0, 0.0, 0.0)), "Rotation": round_vec3((0.0, 0.0, child_angle)),
            "Scale": round_vec3(child_scale), "BlockType": BLOCK_PRIMITIVE,
            "Properties": {"PrimitiveType": PRIMITIVE_QUAD, "PrimitiveFlags": PRIMITIVE_FLAGS_VISIBLE,
                           "Color": self.color, "Static": True},
        })
        self.next_id += 1
        self.stats.toys += 2

    def emit_medial_triangle(self, a: Vec2, b: Vec2, c: Vec2) -> None:
        self.preview_triangles.append((a, b, c))
        self.count += 1
        blocks, self.next_id, tri_stats = triangle_points_to_primitive_blocks(
            f"{self.prefix}-tri-{self.count:05d}", self.next_id, self.parent_id,
            (a[0], a[1], self.z), (b[0], b[1], self.z), (c[0], c[1], self.z),
            self.color, True)
        self.blocks.extend(blocks)
        self.stats.toys += tri_stats.primitive_toys


# ---------- per-piece decomposition (HiddenTailParallelogramProcessor port) ----------

def _try_whole_quad(poly: list[Vec2], emitter: _Emitter) -> bool:
    a, b, c, d = poly
    if not _are_parallel_and_equal(_sub(b, a), _sub(c, d)):
        return False
    if not _are_parallel_and_equal(_sub(d, a), _sub(c, b)):
        return False
    center = ((a[0] + c[0]) * 0.5, (a[1] + c[1]) * 0.5)
    emitter.emit_parallelogram(center, ((c[0] - a[0]) * 0.5, (c[1] - a[1]) * 0.5),
                               ((d[0] - b[0]) * 0.5, (d[1] - b[1]) * 0.5))
    return True


def _try_bounding_rect(poly: list[Vec2], emitter: _Emitter, region: RegionMask) -> bool:
    """Min-area oriented bounding rect; emit as 1 rect toy if it stays in-region."""
    best = None
    n = len(poly)
    for i in range(n):
        edge = _sub(poly[(i + 1) % n], poly[i])
        le = _len(edge)
        if le < 1e-9:
            continue
        ux, uy = edge[0] / le, edge[1] / le
        us = [_dot(p, (ux, uy)) for p in poly]
        vs = [_dot(p, (-uy, ux)) for p in poly]
        u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
        area = (u1 - u0) * (v1 - v0)
        if best is None or area < best[0]:
            best = (area, (ux, uy), u0, u1, v0, v1)
    if best is None:
        return False
    _, (ux, uy), u0, u1, v0, v1 = best
    corners = []
    for u, v in ((u0, v0), (u1, v0), (u1, v1), (u0, v1)):
        corners.append((u * ux - v * uy, u * uy + v * ux))
    if not region.contains_polygon(corners):
        return False
    a, b, c, d = corners
    center = ((a[0] + c[0]) * 0.5, (a[1] + c[1]) * 0.5)
    emitter.emit_parallelogram(center, ((c[0] - a[0]) * 0.5, (c[1] - a[1]) * 0.5),
                               ((d[0] - b[0]) * 0.5, (d[1] - b[1]) * 0.5))
    return True


def _try_parallel_sides_peel(poly: list[Vec2], emitter: _Emitter) -> bool:
    n = len(poly)
    if n < 5:
        return False
    best_start, best_score = -1, -1
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        c, d = poly[(i + 2) % n], poly[(i + 3) % n]
        if not _are_parallel_and_equal(_sub(b, a), _sub(c, d)):
            continue
        if not _are_parallel_and_equal(_sub(d, a), _sub(c, b)):
            continue
        v_left = ((c[0] - a[0]) * 0.5, (c[1] - a[1]) * 0.5)
        v_up = ((d[0] - b[0]) * 0.5, (d[1] - b[1]) * 0.5)
        ll, lu = _len(v_left), _len(v_up)
        if ll < 1e-7 or lu < 1e-7:
            continue
        score = 1 if abs(ll - lu) / max(ll, lu) < LENGTH_EPS else 0
        if score > best_score:
            best_score, best_start = score, i
    if best_start < 0:
        return False
    a, b = poly[best_start], poly[(best_start + 1) % n]
    c, d = poly[(best_start + 2) % n], poly[(best_start + 3) % n]
    center = ((a[0] + c[0]) * 0.5, (a[1] + c[1]) * 0.5)
    emitter.emit_parallelogram(center, ((c[0] - a[0]) * 0.5, (c[1] - a[1]) * 0.5),
                               ((d[0] - b[0]) * 0.5, (d[1] - b[1]) * 0.5))
    rem1, rem2 = (best_start + 1) % n, (best_start + 2) % n
    for idx in sorted((rem1, rem2), reverse=True):
        del poly[idx]
    return True


def _construction_peel(poly: list[Vec2], emitter: _Emitter, region: RegionMask) -> bool:
    """Fourth-vertex peel. Unlike the C# least-violating fallback, a peel whose
    reflected point escapes the piece must keep its overhang inside the raster
    region, else it would paint outside the layer."""
    n = len(poly)
    pick = -1
    for eps in (1e-5, 1e-3):
        for i in range(n):
            v = poly[i]
            a, b = poly[(i - 1) % n], poly[(i + 1) % n]
            p = (a[0] + b[0] - v[0], a[1] + b[1] - v[1])
            if _inside_convex_ccw(p, poly, eps):
                pick = i
                break
        if pick >= 0:
            break
    if pick < 0:
        for i in range(n):
            v = poly[i]
            a, b = poly[(i - 1) % n], poly[(i + 1) % n]
            p = (a[0] + b[0] - v[0], a[1] + b[1] - v[1])
            if region.contains_polygon([a, p, b]):
                pick = i
                break
    if pick < 0:
        return False
    v = poly[pick]
    a, b = poly[(pick - 1) % n], poly[(pick + 1) % n]
    center = ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5)
    emitter.emit_parallelogram(center, _sub(a, center), _sub(v, center))
    del poly[pick]
    return True


def _emit_tail_triangle(a: Vec2, b: Vec2, c: Vec2, emitter: _Emitter, region: RegionMask) -> None:
    for v, e1, e2 in ((a, b, c), (b, a, c), (c, a, b)):
        p = (e1[0] + e2[0] - v[0], e1[1] + e2[1] - v[1])
        if region.contains_polygon([e1, p, e2]):
            center = ((e1[0] + e2[0]) * 0.5, (e1[1] + e2[1]) * 0.5)
            emitter.emit_parallelogram(center, _sub(e1, center), _sub(v, center))
            emitter.stats.hidden_tails += 1
            return
    emitter.emit_medial_triangle(a, b, c)
    emitter.stats.medial_tails += 1


def _process_piece(points: list[Vec2], emitter: _Emitter, region: RegionMask) -> None:
    poly = _drop_collinear(points)
    if len(poly) < 3 or abs(_signed_area(poly)) < 1e-10:
        return
    if _signed_area(poly) < 0:
        poly.reverse()
    emitter.stats.convex_pieces += 1

    if len(poly) == 3:
        _emit_tail_triangle(poly[0], poly[1], poly[2], emitter, region)
        return
    if len(poly) == 4 and _try_whole_quad(poly, emitter):
        emitter.stats.whole_quads += 1
        return
    if _try_bounding_rect(poly, emitter, region):
        emitter.stats.rect_covers += 1
        return
    while len(poly) > 3:
        if _try_parallel_sides_peel(poly, emitter):
            emitter.stats.peeled += 1
            continue
        if _construction_peel(poly, emitter, region):
            emitter.stats.peeled += 1
            continue
        for i in range(1, len(poly) - 1):  # peel stalled: fan-triangulate remainder
            _emit_tail_triangle(poly[0], poly[i], poly[i + 1], emitter, region)
        return
    _emit_tail_triangle(poly[0], poly[1], poly[2], emitter, region)


# ---------- public entry point ----------

def rings_to_parallelogram_blocks(
    unity_polygons: list[list[list[Vec2]]],
    mask: np.ndarray,
    image_width: int,
    image_height: int,
    width_units: float,
    z: float,
    color: str,
    name_prefix: str,
    start_id: int = 1,
    parent_id: int = 0,
    min_triangle_area: float = 0.000002,
) -> tuple[list[dict], int, NGonStats, dict]:
    """Decompose polygons (each a list of rings in UNITY coords, shell first)
    into parallelogram quad blocks. The last return value carries preview
    geometry: {"parallelograms": [(origin, edge_x, edge_y)], "triangles": [...]}."""
    stats = NGonStats()
    region = RegionMask(mask, image_width, image_height, width_units)
    emitter = _Emitter(name_prefix, start_id, parent_id, z, color, stats)

    for rings in unity_polygons:
        rings = [r for r in rings if len(r) >= 3 and abs(_signed_area(r)) > 1e-12]
        if not rings:
            continue
        vertices = [point for ring in rings for point in ring]
        ring_ends = np.cumsum([len(r) for r in rings], dtype=np.uint32)
        flat = np.array(vertices, dtype=np.float64)
        indices = earcut.triangulate_float64(flat, ring_ends)
        triangles = []
        for i in range(0, len(indices), 3):
            tri = (int(indices[i]), int(indices[i + 1]), int(indices[i + 2]))
            pts = [vertices[k] for k in tri]
            area = _signed_area(pts)
            if abs(area) < min_triangle_area:
                continue
            triangles.append(tri if area > 0 else (tri[0], tri[2], tri[1]))
        stats.source_triangles += len(triangles)
        for piece in _hertel_mehlhorn(vertices, triangles):
            _process_piece([vertices[k] for k in piece], emitter, region)

    preview = {"parallelograms": emitter.preview_parallelograms,
               "triangles": emitter.preview_triangles}
    return emitter.blocks, emitter.next_id, stats, preview
