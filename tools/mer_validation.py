"""Validate stock ProjectMER hierarchy and supported image-converter blocks."""
from __future__ import annotations

import json
import math
from pathlib import Path


class ExportValidationError(ValueError):
    def __init__(self, warnings):
        self.warnings = warnings
        super().__init__("ProjectMER export validation failed: " + "; ".join(warnings))


def validate_schematic(document):
    issues = []
    if not isinstance(document, dict) or type(document.get("RootObjectId")) is not int:
        raise ExportValidationError(["Missing or invalid RootObjectId."])
    root = document["RootObjectId"]
    if root != 0:
        raise ExportValidationError(["Image converter RootObjectId must be 0."])
    blocks = document.get("Blocks")
    if not isinstance(blocks, list):
        raise ExportValidationError(["Blocks must be a list."])
    parents = {}
    for index, block in enumerate(blocks):
        label = f"Block {index + 1}"
        if not isinstance(block, dict):
            issues.append(f"{label} is not an object.")
            continue
        object_id, parent = block.get("ObjectId"), block.get("ParentId")
        if type(object_id) is not int or type(parent) is not int:
            issues.append(f"{label} has missing or invalid IDs.")
            continue
        if object_id == root:
            issues.append(f"{label} uses reserved server root ID {root}.")
        if not (0 <= object_id <= 2147483647 and 0 <= parent <= 2147483647):
            issues.append(f"{label} IDs must be non-negative 32-bit integers.")
        if object_id in parents:
            issues.append(f"Duplicate ObjectId {object_id}.")
        parents[object_id] = parent
        for field in ("Position", "Rotation", "Scale"):
            vector = block.get(field)
            if not isinstance(vector, dict) or any(
                type(vector.get(axis)) not in (int, float) or not math.isfinite(vector[axis])
                for axis in "xyz"
            ):
                issues.append(f"{label} has an invalid {field}.")
        kind, properties = block.get("BlockType"), block.get("Properties")
        if type(kind) is not int or kind not in (0, 1):
            issues.append(f"{label} has unsupported BlockType {kind}; rebuild from the image.")
        if not isinstance(properties, dict):
            issues.append(f"{label} is missing Properties.")
        elif kind == 1 and (
            type(properties.get("PrimitiveType")) is not int
            or properties["PrimitiveType"] not in range(6)
            or not isinstance(properties.get("Color"), str)
        ):
            issues.append(f"{label} is missing valid primitive properties.")
    # Walk each chain once, including disconnected cycles that the server would omit.
    connected = {root}
    for object_id in parents:
        trail = set()
        cursor = object_id
        while cursor not in connected:
            if cursor in trail or cursor not in parents:
                issues.append(f"ObjectId {object_id} has a cycle or missing parent {cursor}.")
                break
            trail.add(cursor)
            cursor = parents[cursor]
        connected.update(trail)
    if issues:
        raise ExportValidationError(issues)
    return document


def read_schematic(path):
    try:
        document = json.loads(Path(path).read_text("utf-8"))
    except (ValueError, OSError) as error:
        raise ExportValidationError([f"Cannot read schematic: {error}"]) from error
    return validate_schematic(document)
