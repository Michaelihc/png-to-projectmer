"""Emit source triangles as SCP:SL 15.0 native triangle primitives.

Two constructions, both opt-in (the default converter output stays quads):

* ``split``: cut the triangle along its altitude onto the longest edge into
  two right triangles; each becomes one right-isosceles ``Triangle90`` toy with
  non-uniform scale along its legs. 2 visible primitives, 2 objects, no
  helper empties. A triangle that already has a right angle needs only 1.
* ``shear``: one ``Triangle90`` under an Empty parent whose non-uniform scale
  shears it onto the triangle. 1 visible primitive, 2 objects (1 for right
  triangles, which need no shear).

Every unknown about the real 15.0 mesh lives in
``mer_native_triangle_calibration``; the geometry here derives the transform
from that table, so a measured mesh only changes the table.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

import mer_native_triangle_calibration as calibration
from mer_triangle_primitives import (
    BLOCK_EMPTY,
    BLOCK_PRIMITIVE,
    PRIMITIVE_FLAGS_VISIBLE,
    matrix_to_unity_euler_zxy,
    round_vec3 as _round_vec3,
)

Vec2 = tuple[float, float]

MODES = ("split", "shear")
# |cos(angle)| below this counts as an exact right angle. Deliberately tight:
# anything looser would bend the tile instead of splitting it.
RIGHT_ANGLE_COS_TOL = 1e-6
# A split half narrower than this fraction of the base is a numeric sliver.
SLIVER_FRACTION = 1e-6


@dataclass
class NativeTriangleStats:
    source_triangles: int = 0
    right_triangles: int = 0   # already right-angled: 1 primitive
    split_triangles: int = 0   # cut along the altitude: 2 primitives
    sheared_triangles: int = 0  # 1 primitive under a shear empty
    visible: int = 0
    objects: int = 0

    def line(self) -> str:
        return (f"{self.source_triangles} triangles -> {self.right_triangles} right, "
                f"{self.split_triangles} split, {self.sheared_triangles} sheared "
                f"-> {self.visible} visible / {self.objects} objects")


# ---------- 2D helpers ----------

def _sub(a: Vec2, b: Vec2) -> Vec2:
    return (a[0] - b[0], a[1] - b[1])


def _cross(a: Vec2, b: Vec2) -> float:
    return a[0] * b[1] - a[1] * b[0]


def _dot(a: Vec2, b: Vec2) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _len(a: Vec2) -> float:
    return math.hypot(a[0], a[1])


def _right_angle_vertex(tri: tuple[Vec2, Vec2, Vec2]) -> int | None:
    """Index of the vertex with an exact right angle, if any."""
    for i in range(3):
        p, a, b = tri[i], tri[(i + 1) % 3], tri[(i + 2) % 3]
        u, v = _sub(a, p), _sub(b, p)
        lu, lv = _len(u), _len(v)
        if lu > 0 and lv > 0 and abs(_dot(u, v)) <= RIGHT_ANGLE_COS_TOL * lu * lv:
            return i
    return None


# ---------- placement planning ----------

@dataclass(frozen=True)
class RightTriangle:
    """A right triangle in 2D: the right-angle vertex and the two leg ends."""
    corner: Vec2
    end_a: Vec2
    end_b: Vec2


def plan_triangle(tri: tuple[Vec2, Vec2, Vec2], mode: str) -> tuple[str, list[RightTriangle]]:
    """Decide how one source triangle is emitted.

    Returns (kind, right_triangles): kind is "right" (1 primitive), "split"
    (2 primitives) or "shear" (1 primitive under an empty; the returned list
    holds the single right-angle placement used as the shear basis).
    """
    if mode not in MODES:
        raise ValueError(f"unknown native triangle mode: {mode}")
    right = _right_angle_vertex(tri)
    if right is not None:
        return "right", [RightTriangle(tri[right], tri[(right + 1) % 3], tri[(right + 2) % 3])]

    if mode == "shear":
        # Shear basis: the vertex whose angle is nearest 90 degrees.
        best, best_cos = 0, 2.0
        for i in range(3):
            p, a, b = tri[i], tri[(i + 1) % 3], tri[(i + 2) % 3]
            u, v = _sub(a, p), _sub(b, p)
            cos = abs(_dot(u, v)) / (_len(u) * _len(v))
            if cos < best_cos:
                best, best_cos = i, cos
        return "shear", [RightTriangle(tri[best], tri[(best + 1) % 3], tri[(best + 2) % 3])]

    # Longest edge is the base: both base angles are acute, so the altitude foot
    # lies strictly inside the base.
    edges = [(_len(_sub(tri[(i + 1) % 3], tri[(i + 2) % 3])), i) for i in range(3)]
    _, apex_index = max(edges)
    apex, b, c = tri[apex_index], tri[(apex_index + 1) % 3], tri[(apex_index + 2) % 3]
    base = _sub(c, b)
    base_len = _len(base)
    t = _dot(_sub(apex, b), base) / (base_len * base_len)
    foot = (b[0] + base[0] * t, b[1] + base[1] * t)
    halves = []
    for end in (b, c):
        if _len(_sub(end, foot)) > SLIVER_FRACTION * base_len:
            halves.append(RightTriangle(foot, end, apex))
    return "split", halves


def count_triangle(tri: tuple[Vec2, Vec2, Vec2], mode: str) -> tuple[int, int]:
    """(visible primitives, total objects) for one triangle, without emitting."""
    kind, pieces = plan_triangle(tri, mode)
    if kind == "right":
        return 1, 1
    if kind == "shear":
        return 1, 2
    return len(pieces), len(pieces)


def count_triangles(triangles, mode: str) -> tuple[int, int]:
    visible = objects = 0
    for tri in triangles:
        v, o = count_triangle(tuple(tri), mode)
        visible += v
        objects += o
    return visible, objects


# ---------- transform fitting ----------

def round_vec3(value) -> dict[str, float]:
    """round_vec3 for numpy inputs: schematic values must be plain floats."""
    return _round_vec3(tuple(float(v) for v in value))


def _rotation_euler(matrix3: np.ndarray) -> tuple[float, float, float]:
    return matrix_to_unity_euler_zxy(tuple(tuple(float(x) for x in row) for row in matrix3))


def _mesh_legs(spec: calibration.NativeTriangleType):
    """(corner, leg_x, leg_y) of the unit mesh as 2D numpy vectors."""
    if spec.right_angle_index is None:
        raise ValueError(f"{spec.name} is not a right triangle type.")
    pts = [np.array(v[:2], float) for v in spec.vertices]
    for v in spec.vertices:
        if abs(v[2]) > 1e-12:
            raise ValueError("Calibrated triangle meshes must lie in the local XY plane.")
    i = spec.right_angle_index
    corner = pts[i]
    leg_1, leg_2 = pts[(i + 1) % 3] - corner, pts[(i + 2) % 3] - corner
    # Normalise so leg_x is the one along local X (the other along Y).
    if abs(leg_1[1]) < 1e-12 and abs(leg_2[0]) < 1e-12:
        return corner, leg_1, leg_2
    if abs(leg_2[1]) < 1e-12 and abs(leg_1[0]) < 1e-12:
        return corner, leg_2, leg_1
    raise ValueError("Triangle90 legs must run along the local X and Y axes.")


def fit_right_triangle(piece: RightTriangle, spec: calibration.NativeTriangleType):
    """Affine map (M, t) with M 2x2 such that world = M @ local + t sends the unit
    mesh onto `piece`, and whose determinant sign shows the visible face toward -Z.

    Returns (M, t, det_sign).
    """
    corner, leg_x, leg_y = _mesh_legs(spec)
    local = np.column_stack((leg_x, leg_y))  # columns are the unit-mesh legs
    # Facing: a one-sided face visible from local -Z needs det > 0 (local Z maps to
    # world +Z); visible from local +Z needs det < 0 (local Z maps to world -Z).
    want = -spec.visible_local_z if spec.visible_local_z else 1
    p = np.array(piece.corner, float)
    ends = (np.array(piece.end_a, float), np.array(piece.end_b, float))
    for first, second in (ends, ends[::-1]):
        world = np.column_stack((first - p, second - p))
        m = world @ np.linalg.inv(local)
        if np.sign(np.linalg.det(m)) == want:
            return m, p - m @ corner, want
    raise ValueError("Degenerate right triangle.")


def _decompose(m: np.ndarray, det_sign: int):
    """Split M into (U, sigma, Vt) with U a proper 2D rotation and Vt of the
    determinant sign of M, so Vt extended with det_sign in Z is a proper 3D
    rotation."""
    u, sigma, vt = np.linalg.svd(m)
    if np.linalg.det(u) < 0:
        u[:, 1] *= -1
        vt[1, :] *= -1
    return u, sigma, vt


def _extend(m2: np.ndarray, z: float) -> np.ndarray:
    out = np.eye(3)
    out[:2, :2] = m2
    out[2, 2] = z
    return out


def _right_triangle_blocks(name: str, next_id: int, parent_id: int, z: float, color: str,
                           piece: RightTriangle, spec: calibration.NativeTriangleType,
                           static: bool):
    """Blocks for one right-triangle placement. Returns (blocks, next_id)."""
    m, t, det_sign = fit_right_triangle(piece, spec)
    c1, c2 = m[:, 0], m[:, 1]
    n1, n2 = np.linalg.norm(c1), np.linalg.norm(c2)
    orthogonal = abs(float(c1 @ c2)) <= RIGHT_ANGLE_COS_TOL * n1 * n2
    properties = {
        "PrimitiveType": calibration.schematic_value(spec.name),
        "PrimitiveFlags": PRIMITIVE_FLAGS_VISIBLE,
        "Color": color,
        "Static": static,
    }
    if orthogonal:
        rotation = _extend(np.column_stack((c1 / n1, c2 / n2)), float(det_sign))
        block = {
            "Name": name, "ObjectId": next_id, "ParentId": parent_id,
            "Position": round_vec3((t[0], t[1], z)),
            "Rotation": round_vec3(_rotation_euler(rotation)),
            "Scale": round_vec3((n1, n2, 1.0)),
            "BlockType": BLOCK_PRIMITIVE, "Properties": properties,
        }
        return [block], next_id + 1

    u, sigma, vt = _decompose(m, det_sign)
    shear_id = next_id
    parent = {
        "Name": f"{name}-shear", "ObjectId": shear_id, "ParentId": parent_id,
        "Position": round_vec3((t[0], t[1], z)),
        "Rotation": round_vec3(_rotation_euler(_extend(u, 1.0))),
        "Scale": round_vec3((sigma[0], sigma[1], 1.0)),
        "BlockType": BLOCK_EMPTY, "Properties": {"Static": static},
    }
    child = {
        "Name": name, "ObjectId": next_id + 1, "ParentId": shear_id,
        "Position": round_vec3((0.0, 0.0, 0.0)),
        "Rotation": round_vec3(_rotation_euler(_extend(vt, float(det_sign)))),
        "Scale": round_vec3((1.0, 1.0, 1.0)),
        "BlockType": BLOCK_PRIMITIVE, "Properties": properties,
    }
    return [parent, child], next_id + 2


def triangle_to_blocks(name: str, next_id: int, parent_id: int, z: float, color: str,
                       tri: tuple[Vec2, Vec2, Vec2], mode: str, solid: bool,
                       stats: NativeTriangleStats | None = None, static: bool = True):
    """Emit one source triangle. Returns (blocks, next_id)."""
    spec = calibration.right_triangle_type(solid)
    kind, pieces = plan_triangle(tri, mode)
    blocks: list[dict] = []
    for index, piece in enumerate(pieces):
        piece_name = name if len(pieces) == 1 else f"{name}-{'ab'[index]}"
        new_blocks, next_id = _right_triangle_blocks(
            piece_name, next_id, parent_id, z, color, piece, spec, static)
        blocks.extend(new_blocks)
    if stats is not None:
        stats.source_triangles += 1
        stats.right_triangles += kind == "right"
        stats.split_triangles += kind == "split"
        stats.sheared_triangles += kind == "shear" and len(blocks) == 2
        stats.visible += sum(b["BlockType"] == BLOCK_PRIMITIVE for b in blocks)
        stats.objects += len(blocks)
    return blocks, next_id


# ---------- geometry for verification and preview ----------

def block_world_matrices(blocks: list[dict]) -> dict[int, np.ndarray]:
    """4x4 world matrix (Unity TRS: T * R(zxy) * S, parents compose) per ObjectId."""
    from mer_triangle_primitives import unity_euler_zxy_to_matrix

    by_id = {b["ObjectId"]: b for b in blocks}
    cache: dict[int, np.ndarray] = {}

    def local(b):
        pos = b["Position"]; rot = b["Rotation"]; sc = b["Scale"]
        m = np.eye(4)
        r = np.array(unity_euler_zxy_to_matrix((rot["x"], rot["y"], rot["z"])))
        m[:3, :3] = r @ np.diag((sc["x"], sc["y"], sc["z"]))
        m[:3, 3] = (pos["x"], pos["y"], pos["z"])
        return m

    def world(oid):
        if oid in cache:
            return cache[oid]
        b = by_id[oid]
        parent = b["ParentId"]
        m = local(b) if parent not in by_id else world(parent) @ local(b)
        cache[oid] = m
        return m

    for oid in by_id:
        world(oid)
    return cache


def native_triangle_world_vertices(blocks: list[dict]) -> list[tuple[dict, np.ndarray]]:
    """(block, 3x3 world vertices) for every native triangle primitive."""
    matrices = block_world_matrices(blocks)
    out = []
    for b in blocks:
        if b.get("BlockType") != BLOCK_PRIMITIVE:
            continue
        spec = calibration.type_from_schematic_value(b["Properties"].get("PrimitiveType"))
        if spec is None:
            continue
        verts = np.array(spec.vertices, float)
        m = matrices[b["ObjectId"]]
        out.append((b, (verts @ m[:3, :3].T) + m[:3, 3]))
    return out


# ---------- converter entry point ----------

def rings_to_native_triangle_blocks(
    unity_polygons,
    mask,
    image_width: int,
    image_height: int,
    width_units: float,
    z: float,
    color: str,
    name_prefix: str,
    start_id: int = 1,
    parent_id: int = 0,
    mode: str = "split",
    solid: bool = False,
    min_triangle_area: float = 0.000002,
):
    """Triangulate polygons (UNITY coords, shell first) and emit every earcut
    triangle as native triangle primitives. Same return shape as
    ``mer_ngon_decomposition.rings_to_parallelogram_blocks`` (the mask and image
    size are accepted for signature parity; native triangles need no raster
    test because they cover each source triangle exactly)."""
    import mer_ngon_decomposition as ngon

    stats = NativeTriangleStats()
    blocks: list[dict] = []
    source_triangles = []
    next_id = start_id
    count = 0
    for vertices, triangles in ngon.triangulate_polygons(unity_polygons, min_triangle_area):
        for tri in triangles:
            points = tuple(vertices[k] for k in tri)
            source_triangles.append(points)
            count += 1
            new_blocks, next_id = triangle_to_blocks(
                f"{name_prefix}-ntri-{count:05d}", next_id, parent_id, z, color,
                points, mode, solid, stats)
            blocks.extend(new_blocks)
    return blocks, next_id, stats, {"source_triangles": source_triangles}


def native_counts(source_triangles) -> dict:
    """Cost of every native mode for the same source triangles (no emission)."""
    result = {}
    for mode in MODES:
        visible, objects = count_triangles(source_triangles, mode)
        result[mode] = {"primitives": visible, "objects": objects}
    return result
