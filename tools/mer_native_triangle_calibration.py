"""Calibration table for the SCP:SL 15.0 native triangle primitives.

Everything about the 15.0 triangle toys that is an assumption lives in this
file. When the real meshes are extracted from the client, edit only the values
below; `mer_native_triangles.py`, the preview renderer and the tests read them.

Assumptions (verify against the extracted 15.0 meshes):

* `PrimitiveToyType` names `Triangle90`, `Triangle90Solid`, `Triangle` and
  `TriangleSolid` are written to the schematic as strings, e.g.
  `"PrimitiveType": "Triangle90"`. ProjectMER reads schematics with Utf8Json,
  whose enum formatter accepts either the enum name or its number. If the
  forked ProjectMER port turns out to need numbers, fill in `NUMERIC_VALUES`
  and set `WRITE_AS = "number"`.
* Triangle90 unit mesh: right angle at local (-0.5, -0.5, 0), legs of length 1
  along +X and +Y, so the vertices are (-0.5,-0.5,0), (0.5,-0.5,0), (-0.5,0.5,0).
* One-sided variants are visible from local -Z (like Unity's Quad, which
  ProjectMER quads already rely on). Solid variants render both sides.
* The mesh lies in the local XY plane and `Triangle90` legs run along the
  local X and Y axes. The solver relies on both (see
  `mer_native_triangles.fit_right_triangle`).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NativeTriangleType:
    name: str
    vertices: tuple[tuple[float, float, float], ...]
    # Which local Z direction the one-sided face is visible from:
    # -1 = seen from local -Z (Quad-like), +1 = seen from local +Z,
    # 0 = both sides (Solid variants).
    visible_local_z: int
    # Index of the right-angle vertex for Triangle90 types, else None.
    right_angle_index: int | None = None

    @property
    def solid(self) -> bool:
        return self.visible_local_z == 0


_RIGHT_ISOSCELES = ((-0.5, -0.5, 0.0), (0.5, -0.5, 0.0), (-0.5, 0.5, 0.0))
# Equilateral: base 1 along X, apex up. Listed so the renderer and validation
# know the names; the converter does not emit them.
_EQUILATERAL = ((-0.5, -0.5, 0.0), (0.5, -0.5, 0.0), (0.0, 0.3660254037844386, 0.0))

TYPES: dict[str, NativeTriangleType] = {
    "Triangle90": NativeTriangleType("Triangle90", _RIGHT_ISOSCELES, -1, 0),
    "Triangle90Solid": NativeTriangleType("Triangle90Solid", _RIGHT_ISOSCELES, 0, 0),
    "Triangle": NativeTriangleType("Triangle", _EQUILATERAL, -1),
    "TriangleSolid": NativeTriangleType("TriangleSolid", _EQUILATERAL, 0),
}

# Enum numbers are not known until the beta client is inspected.
NUMERIC_VALUES: dict[str, int | None] = {name: None for name in TYPES}

# "name" writes the enum name string; "number" writes NUMERIC_VALUES[name].
WRITE_AS = "name"

# Which type each converter option maps to.
ONE_SIDED_RIGHT_TRIANGLE = "Triangle90"
SOLID_RIGHT_TRIANGLE = "Triangle90Solid"


def right_triangle_type(solid: bool) -> NativeTriangleType:
    return TYPES[SOLID_RIGHT_TRIANGLE if solid else ONE_SIDED_RIGHT_TRIANGLE]


def schematic_value(type_name: str):
    """Value written to `Properties.PrimitiveType` for a native type."""
    if WRITE_AS == "name":
        return type_name
    number = NUMERIC_VALUES[type_name]
    if number is None:
        raise ValueError(f"No numeric enum value is calibrated for {type_name}.")
    return number


def type_from_schematic_value(value) -> NativeTriangleType | None:
    """Reverse lookup for either representation; None for vanilla primitives."""
    if isinstance(value, str):
        return TYPES.get(value)
    if type(value) is int:
        for name, number in NUMERIC_VALUES.items():
            if number is not None and number == value:
                return TYPES[name]
    return None
