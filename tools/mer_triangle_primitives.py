"""Expand triangles into standard (vanilla) MER quad primitives via TRS-hierarchy shear."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


Vector3 = tuple[float, float, float]
Matrix3 = tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]

BLOCK_EMPTY = 0
BLOCK_PRIMITIVE = 1

PRIMITIVE_QUAD = 5
PRIMITIVE_FLAGS_NONE = 0
PRIMITIVE_FLAGS_VISIBLE = 2

EPSILON = 0.000001


@dataclass
class ConversionStats:
    triangle_blocks: int = 0
    direct_tiles: int = 0
    sheared_tiles: int = 0
    skipped_tiles: int = 0

    @property
    def primitive_toys(self) -> int:
        return self.direct_tiles + (self.sheared_tiles * 2)

    def add(self, other: "ConversionStats") -> None:
        self.triangle_blocks += other.triangle_blocks
        self.direct_tiles += other.direct_tiles
        self.sheared_tiles += other.sheared_tiles
        self.skipped_tiles += other.skipped_tiles


def round_float(value: float) -> float:
    rounded = round(value, 5)
    return 0.0 if abs(rounded) < 0.000005 else rounded


def round_vec3(value: Vector3) -> dict[str, float]:
    return {"x": round_float(value[0]), "y": round_float(value[1]), "z": round_float(value[2])}


def add3(a: Vector3, b: Vector3) -> Vector3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub3(a: Vector3, b: Vector3) -> Vector3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def mul3(a: Vector3, scalar: float) -> Vector3:
    return (a[0] * scalar, a[1] * scalar, a[2] * scalar)


def scale3(a: Vector3, b: Vector3) -> Vector3:
    return (a[0] * b[0], a[1] * b[1], a[2] * b[2])


def dot3(a: Vector3, b: Vector3) -> float:
    return (a[0] * b[0]) + (a[1] * b[1]) + (a[2] * b[2])


def cross3(a: Vector3, b: Vector3) -> Vector3:
    return (
        (a[1] * b[2]) - (a[2] * b[1]),
        (a[2] * b[0]) - (a[0] * b[2]),
        (a[0] * b[1]) - (a[1] * b[0]),
    )


def length3(a: Vector3) -> float:
    return math.sqrt(dot3(a, a))


def normalize3(a: Vector3) -> Vector3 | None:
    length = length3(a)
    if length < EPSILON:
        return None
    return (a[0] / length, a[1] / length, a[2] / length)


def project_on_plane(vector: Vector3, normal: Vector3) -> Vector3:
    return sub3(vector, mul3(normal, dot3(vector, normal)))


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def look_rotation(forward: Vector3, upwards: Vector3) -> Matrix3 | None:
    z_axis = normalize3(forward)
    if z_axis is None:
        return None

    x_axis = normalize3(cross3(upwards, z_axis))
    if x_axis is None:
        return None

    y_axis = cross3(z_axis, x_axis)
    return (
        (x_axis[0], y_axis[0], z_axis[0]),
        (x_axis[1], y_axis[1], z_axis[1]),
        (x_axis[2], y_axis[2], z_axis[2]),
    )


def matrix_to_unity_euler_zxy(matrix: Matrix3) -> Vector3:
    x = math.asin(clamp(-matrix[1][2], -1.0, 1.0))
    cos_x = math.cos(x)

    if abs(cos_x) > EPSILON:
        y = math.atan2(matrix[0][2], matrix[2][2])
        z = math.atan2(matrix[1][0], matrix[1][1])
    else:
        y = math.atan2(-matrix[2][0], matrix[0][0])
        z = 0.0

    return (math.degrees(x), math.degrees(y), math.degrees(z))


def unity_euler_zxy_to_matrix(euler: Vector3) -> Matrix3:
    x = math.radians(euler[0])
    y = math.radians(euler[1])
    z = math.radians(euler[2])
    cx = math.cos(x)
    sx = math.sin(x)
    cy = math.cos(y)
    sy = math.sin(y)
    cz = math.cos(z)
    sz = math.sin(z)

    return (
        ((cy * cz) + (sy * sx * sz), (-cy * sz) + (sy * sx * cz), sy * cx),
        (cx * sz, cx * cz, -sx),
        ((-sy * cz) + (cy * sx * sz), (sy * sz) + (cy * sx * cz), cy * cx),
    )


def transform_point(point: Vector3, position: Vector3, rotation: Vector3, scale: Vector3) -> Vector3:
    scaled = scale3(point, scale)
    matrix = unity_euler_zxy_to_matrix(rotation)
    rotated = (
        (matrix[0][0] * scaled[0]) + (matrix[0][1] * scaled[1]) + (matrix[0][2] * scaled[2]),
        (matrix[1][0] * scaled[0]) + (matrix[1][1] * scaled[1]) + (matrix[1][2] * scaled[2]),
        (matrix[2][0] * scaled[0]) + (matrix[2][1] * scaled[1]) + (matrix[2][2] * scaled[2]),
    )
    return add3(rotated, position)


def triangle_parallelogram_tiles(
    point_a: Vector3,
    point_b: Vector3,
    point_c: Vector3,
) -> list[tuple[str, Vector3, Vector3, Vector3]]:
    # The custom runtime swaps B/C before constructing the quad tiles.
    point_b, point_c = point_c, point_b
    midpoint_ab = mul3(add3(point_a, point_b), 0.5)
    midpoint_bc = mul3(add3(point_b, point_c), 0.5)
    midpoint_ca = mul3(add3(point_c, point_a), 0.5)

    center_a = mul3(add3(point_a, midpoint_bc), 0.5)
    center_b = mul3(add3(point_b, midpoint_ca), 0.5)
    center_c = mul3(add3(point_c, midpoint_ab), 0.5)

    return [
        ("a", sub3(midpoint_ca, center_a), sub3(point_a, center_a), center_a),
        ("b", sub3(midpoint_ab, center_b), sub3(point_b, center_b), center_b),
        ("c", sub3(midpoint_bc, center_c), sub3(point_c, center_c), center_c),
    ]


def try_build_rectangle_tile(
    name: str,
    object_id: int,
    parent_id: int,
    v_up: Vector3,
    v_left: Vector3,
    center: Vector3,
    color: str,
    static: bool,
) -> dict[str, Any] | None:
    up_length = length3(v_up)
    left_length = length3(v_left)
    if up_length < EPSILON or left_length < EPSILON:
        return None

    if abs(up_length - left_length) > max(up_length, left_length) * 0.0001:
        return None

    edge_a = add3(v_left, v_up)
    edge_b = sub3(v_left, v_up)
    width = length3(edge_b)
    height = length3(edge_a)
    if width < EPSILON or height < EPSILON:
        return None

    forward = cross3(edge_b, edge_a)
    rotation_matrix = look_rotation(forward, edge_a)
    if rotation_matrix is None:
        return None

    return {
        "Name": name,
        "ObjectId": object_id,
        "ParentId": parent_id,
        "Position": round_vec3(center),
        "Rotation": round_vec3(matrix_to_unity_euler_zxy(rotation_matrix)),
        "Scale": round_vec3((width, height, 1.0)),
        "BlockType": BLOCK_PRIMITIVE,
        "Properties": {
            "PrimitiveType": PRIMITIVE_QUAD,
            "PrimitiveFlags": PRIMITIVE_FLAGS_VISIBLE,
            "Color": color,
            "Static": static,
        },
    }


def try_get_shear_transforms(
    v_up: Vector3,
    v_left: Vector3,
) -> tuple[Vector3, Vector3, float, Vector3] | None:
    if dot3(v_up, v_up) < EPSILON * EPSILON or dot3(v_left, v_left) < EPSILON * EPSILON:
        return None

    up_sqr_magnitude = dot3(v_up, v_up)
    dot_abs = abs(dot3(v_left, v_up))
    if dot_abs >= up_sqr_magnitude - max(up_sqr_magnitude * 0.000001, 0.000000001):
        old_up = v_up
        v_up = v_left
        v_left = mul3(old_up, -1.0)

    up_length = length3(v_up)
    up_normal = normalize3(v_up)
    if up_normal is None:
        return None

    perpendicular = project_on_plane(v_left, up_normal)
    if dot3(perpendicular, perpendicular) < EPSILON * EPSILON:
        return None

    perpendicular_normal = normalize3(perpendicular)
    if perpendicular_normal is None:
        return None

    if dot3(perpendicular_normal, v_left) < 0.0:
        perpendicular_normal = mul3(perpendicular_normal, -1.0)

    normal = cross3(perpendicular_normal, up_normal)
    normal_normalized = normalize3(normal)
    if normal_normalized is None:
        return None

    left_y = clamp(dot3(v_left, up_normal), -up_length, up_length)
    left_x = length3(project_on_plane(v_left, up_normal))
    a = math.sqrt(max(2.0 * up_length * (up_length + left_y), 0.000000001))
    b = math.sqrt(max(2.0 * up_length * (up_length - left_y), 0.000000001))
    x = left_x * 2.0 * up_length / max(a * b, 0.000000001)
    angle = -math.degrees(math.atan2(b, a))

    parent_matrix = look_rotation(normal_normalized, up_normal)
    if parent_matrix is None:
        return None

    return (
        matrix_to_unity_euler_zxy(parent_matrix),
        (x, 1.0, 1.0),
        angle,
        (b, a, 1.0),
    )


def triangle_points_to_primitive_blocks(
    name: str,
    object_id_start: int,
    parent_id: int,
    point_a: Vector3,
    point_b: Vector3,
    point_c: Vector3,
    color: str,
    static: bool = True,
) -> tuple[list[dict[str, Any]], int, ConversionStats]:
    blocks: list[dict[str, Any]] = []
    next_id = object_id_start
    stats = ConversionStats(triangle_blocks=1)

    for suffix, v_up, v_left, center in triangle_parallelogram_tiles(point_a, point_b, point_c):
        tile_name = f"{name}-tile-{suffix}"
        rectangle = try_build_rectangle_tile(
            tile_name,
            next_id,
            parent_id,
            v_up,
            v_left,
            center,
            color,
            static,
        )
        if rectangle is not None:
            blocks.append(rectangle)
            next_id += 1
            stats.direct_tiles += 1
            continue

        shear = try_get_shear_transforms(v_up, v_left)
        if shear is None:
            stats.skipped_tiles += 1
            continue

        parent_rotation, parent_scale, child_angle, child_scale = shear
        shear_parent_id = next_id
        blocks.append(
            {
                "Name": f"{tile_name}-shear",
                "ObjectId": shear_parent_id,
                "ParentId": parent_id,
                "Position": round_vec3(center),
                "Rotation": round_vec3(parent_rotation),
                "Scale": round_vec3(parent_scale),
                "BlockType": BLOCK_EMPTY,
                "Properties": {
                    "Static": static,
                },
            }
        )
        next_id += 1

        blocks.append(
            {
                "Name": tile_name,
                "ObjectId": next_id,
                "ParentId": shear_parent_id,
                "Position": round_vec3((0.0, 0.0, 0.0)),
                "Rotation": round_vec3((0.0, 0.0, child_angle)),
                "Scale": round_vec3(child_scale),
                "BlockType": BLOCK_PRIMITIVE,
                "Properties": {
                    "PrimitiveType": PRIMITIVE_QUAD,
                    "PrimitiveFlags": PRIMITIVE_FLAGS_VISIBLE,
                    "Color": color,
                    "Static": static,
                },
            }
        )
        next_id += 1
        stats.sheared_tiles += 1

    return blocks, next_id, stats
