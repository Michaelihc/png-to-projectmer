"""Fit overlapping inward rectangles, then decompose the uncovered regions."""
from __future__ import annotations

import heapq
import math

from shapely.geometry import Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from shapely.prepared import prep

import mer_ngon_decomposition as ngon


def polygons(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    if not hasattr(geometry, "geoms"):
        return []
    return [p for part in geometry.geoms for p in polygons(part)]


def rectangle_candidates(target, tolerance):
    """Boundary edges set strip orientation; containment limits inward depth."""
    permitted = prep(target.buffer(tolerance))
    x0, y0, x1, y1 = target.bounds
    max_depth = math.hypot(x1 - x0, y1 - y0)
    for polygon in polygons(target):
        polygon = orient(polygon, sign=1)
        for ring in [polygon.exterior, *polygon.interiors]:
            coords = list(ring.coords)
            for a, b in zip(coords, coords[1:]):
                dx, dy = b[0] - a[0], b[1] - a[1]
                length = math.hypot(dx, dy)
                if length < max(tolerance * 2, 1e-6):
                    continue
                nx, ny = -dy / length, dx / length
                for fraction in (1.0, 0.8, 0.5):
                    trim = (1 - fraction) / 2
                    start = (a[0] + trim * dx, a[1] + trim * dy)
                    end = (b[0] - trim * dx, b[1] - trim * dy)

                    def rectangle(depth):
                        return Polygon([start, end, (end[0] + nx * depth, end[1] + ny * depth),
                                        (start[0] + nx * depth, start[1] + ny * depth)])

                    lo, hi = 0.0, max_depth
                    for _ in range(22):
                        mid = (lo + hi) / 2
                        if permitted.covers(rectangle(mid)):
                            lo = mid
                        else:
                            hi = mid
                    if lo > max(tolerance, 1e-6):
                        yield rectangle(lo)


def rings_to_strip_blocks(unity_polygons, mask, image_width, image_height,
                         width_units, z, color, name_prefix, start_id=1,
                         parent_id=0, tolerance_px=0.25):
    if not math.isfinite(tolerance_px) or tolerance_px < 0:
        raise ValueError("Strip tolerance must be a finite, non-negative pixel distance.")
    baseline = ngon.rings_to_parallelogram_blocks(
        unity_polygons, mask, image_width, image_height, width_units, z, color,
        name_prefix, start_id=start_id, parent_id=parent_id)
    best = baseline
    target = unary_union([Polygon(r[0], r[1:]) for r in unity_polygons])
    if target.is_empty:
        return baseline
    tolerance = tolerance_px * width_units / image_width
    candidates = list(rectangle_candidates(target, tolerance))
    pending = [(-p.intersection(target).area, i) for i, p in enumerate(candidates)]
    heapq.heapify(pending)
    remaining = target
    selected = []
    source_tiles = [Polygon(t) for t in baseline[3]["source_triangles"]]
    min_gain = max(tolerance * tolerance * 4, target.area * 0.0001)
    while pending:
        _score, index = heapq.heappop(pending)
        candidate = candidates[index]
        gain = candidate.intersection(remaining).area
        if gain < min_gain:
            continue
        if pending and gain < -pending[0][0] - 1e-10:
            heapq.heappush(pending, (-gain, index))
            continue
        selected.append(candidate)
        remaining = remaining.difference(candidate)
        if len(selected) in (16, 32, 64, 128, 256):
            result = _emit_cover(selected, remaining, tolerance, mask, image_width,
                                 image_height, width_units, z, color, name_prefix,
                                 start_id, parent_id, source_tiles)
            if len(result[0]) < len(best[0]):
                best = result
    result = _emit_cover(selected, remaining, tolerance, mask, image_width,
                         image_height, width_units, z, color, name_prefix,
                         start_id, parent_id, source_tiles)
    if len(result[0]) < len(best[0]):
        best = result
    # Source triangulation remains the input outline, not only the uncovered gaps.
    best[2].source_triangles = baseline[2].source_triangles
    best[3]["source_triangles"] = baseline[3]["source_triangles"]
    return best


def _emit_cover(selected, remaining, tolerance, mask, image_width, image_height,
                width_units, z, color, name_prefix, start_id, parent_id, source_tiles=None):
    if source_tiles is not None:
        clipped = _emit_cover(selected, remaining, tolerance, mask, image_width, image_height,
                              width_units, z, color, name_prefix, start_id, parent_id)
        covered = prep(unary_union(selected).buffer(tolerance))
        whole_remainder = unary_union([p for p in source_tiles if not covered.covers(p)])
        whole = _emit_cover(selected, whole_remainder, tolerance, mask, image_width, image_height,
                            width_units, z, color, name_prefix, start_id, parent_id)
        return min((clipped, whole), key=lambda result: len(result[0]))
    stats = ngon.NGonStats()
    emitter = ngon._Emitter(name_prefix, start_id, parent_id, z, color, stats)
    for rectangle in selected:
        a, b, c, d = list(rectangle.exterior.coords)[:4]
        center = ((a[0] + c[0]) / 2, (a[1] + c[1]) / 2)
        emitter.emit_parallelogram(center, ngon._sub(a, center), ngon._sub(b, center))
        stats.rect_covers += 1
    residual_rings = []
    for p in polygons(remaining):
        if p.area < tolerance * tolerance:
            continue
        p = orient(p, sign=1)
        residual_rings.append([list(p.exterior.coords)[:-1],
                               *[list(r.coords)[:-1] for r in p.interiors]])
    blocks, next_id, residual_stats, geometry = ngon.rings_to_parallelogram_blocks(
        residual_rings, mask, image_width, image_height, width_units, z, color,
        name_prefix + "-fill", start_id=emitter.next_id, parent_id=parent_id)
    for field in stats.__dataclass_fields__:
        setattr(stats, field, getattr(stats, field) + getattr(residual_stats, field))
    geometry["parallelograms"] = emitter.preview_parallelograms + geometry["parallelograms"]
    return emitter.blocks + blocks, next_id, stats, geometry
