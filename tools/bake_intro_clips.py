"""Bake the emblem intro timelines into runtime clips for reinforcements-system.

Two outputs per emblem:
  * a COMPILED game-ready MER (BlockType-11 triangles expanded into vanilla
    quad / shear-pair blocks via mer_triangle_primitives, BT8 TextToys and
    BT1 primitives passed through) written to
    reinforcements-system/generated/logos/<out>-emblem.mer.json
  * a baked clip (reinforcements.emblem-intro-clip v1) with per-group
    fixed-rate sample channels written to
    reinforcements-system/generated/animations/<out>-intro.clip.json

Clip model (mirrors the preview engine's group math exactly):
  M_group(t) = T(pivot + [tx,ty]) . R(rot) . R(axis) . S(sx,sy) . R(-axis) . T(-pivot)
which the runtime replicates with a 3-empty rig per group
(E1 pos/rot -> E2 scale -> E3 rot(-axis) -> members). `opacity` is applied as
color-alpha on member toys. Staggered per-block tweens (motto/caption
letters) are expanded into one single-binding group per block.

Playback authority is the fixed-rate samples; the authoring timeline JSONs
remain provenance only (same policy as the crownfall mechanism clips).

Usage: python tools/bake_intro_clips.py
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))

import build_intro_animation_preview as preview  # noqa: E402
import mer_triangle_primitives as tri_prims  # noqa: E402

RS = ROOT.parent / "reinforcements-system"
OUT_LOGOS = RS / "generated" / "logos"
OUT_ANIMS = RS / "generated" / "animations"

SAMPLE_RATE_HZ = 30

EMBLEMS = [
    # (builder, source json, output stem, view height meters)
    (preview.build_goc, preview.GOC_JSON, "goc-seal", 5.5),
    (preview.build_strike, preview.STRIKE_JSON, "goc-strike", 10.7),
    (preview.build_chaos, preview.CHAOS_JSON, "ci-dual", 8.3),
]

# 2026-07-16 live review: rasterized fills read pixelated in game, so ALL
# triangle families keep their EXACT converted geometry. The only reduction is
# sliver decimation (drops sub-visible triangles; ~91% map / ~99% laurel area
# retained) to keep the 6-blocks-per-triangle shear expansion inside a
# spawnable toy budget (~2.5k for the GOC seal, chunked at 50/frame).
MAP_MIN_TRI_AREA = 0.001
LAUREL_MIN_TRI_AREA = 0.002
GOC_CENTER_X = 0.00145

# Emissive color pass: the emblems render self-lit (raw ColorRgba overrides)
# so they read vivid under a dim fill light; pure black parts stay (0,0,0) and
# melt into the void skybox instead of graying under a bright light.
# Channels are copied 1:1 -- boosting past 1.0 clips per-channel and shifts
# hue (the blues went green-cyan in game).
BLACK_CHANNEL_SUM = 0.05


# ---- easing (must match the preview JS engine) ------------------------------

def ease(name: str, t: float) -> float:
    t = min(1.0, max(0.0, t))
    if name == "linear":
        return t
    if name == "outCubic":
        return 1.0 - (1.0 - t) ** 3
    if name == "inOutCubic":
        return 4 * t * t * t if t < 0.5 else 1.0 - ((-2 * t + 2) ** 3) / 2.0
    if name == "outQuint":
        return 1.0 - (1.0 - t) ** 5
    if name.startswith("outBack"):
        k = float(name.split(":")[1]) if ":" in name else 1.7
        u = t - 1.0
        return 1.0 + u * u * ((k + 1.0) * u + k)
    raise ValueError(f"unknown ease '{name}'")


def tween_value(tw: dict, t: float, delay: float = 0.0) -> float:
    t0 = tw["t0"] + delay
    k = 0.0 if t <= t0 else ease(tw["ease"], (t - t0) / tw["dur"])
    return tw["from"] + (tw["to"] - tw["from"]) * k


# ---- triangle compilation ----------------------------------------------------

def tri_points(block: dict) -> list[tuple[float, float]]:
    pr = block["Properties"]
    return [(pr[k]["x"], pr[k]["y"]) for k in ("PointA", "PointB", "PointC")]


def rasterize_tris_to_rects(triangles: list[list[tuple[float, float]]],
                            cell: float) -> list[tuple[float, float, float, float]]:
    """Coverage-rasterize triangles onto a grid, then greedily merge filled
    cells into maximal rectangles. Returns (cx, cy, w, h) rects."""
    xs = [p[0] for t in triangles for p in t]
    ys = [p[1] for t in triangles for p in t]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    w_cells = int(math.ceil((x1 - x0) / cell))
    h_cells = int(math.ceil((y1 - y0) / cell))
    grid = [[False] * w_cells for _ in range(h_cells)]
    for a, b, c in triangles:
        bx0 = max(0, int((min(a[0], b[0], c[0]) - x0) / cell))
        bx1 = min(w_cells, int((max(a[0], b[0], c[0]) - x0) / cell) + 1)
        by0 = max(0, int((min(a[1], b[1], c[1]) - y0) / cell))
        by1 = min(h_cells, int((max(a[1], b[1], c[1]) - y0) / cell) + 1)
        for gy in range(by0, by1):
            cy = y0 + (gy + 0.5) * cell
            row = grid[gy]
            for gx in range(bx0, bx1):
                if row[gx]:
                    continue
                cx = x0 + (gx + 0.5) * cell
                d1 = (b[0] - a[0]) * (cy - a[1]) - (b[1] - a[1]) * (cx - a[0])
                d2 = (c[0] - b[0]) * (cy - b[1]) - (c[1] - b[1]) * (cx - b[0])
                d3 = (a[0] - c[0]) * (cy - c[1]) - (a[1] - c[1]) * (cx - c[0])
                neg = d1 < 0 or d2 < 0 or d3 < 0
                pos = d1 > 0 or d2 > 0 or d3 > 0
                if not (neg and pos):
                    row[gx] = True
    rects: list[tuple[float, float, float, float]] = []
    used = [[False] * w_cells for _ in range(h_cells)]
    for gy in range(h_cells):
        gx = 0
        while gx < w_cells:
            if grid[gy][gx] and not used[gy][gx]:
                x_end = gx
                while (x_end + 1 < w_cells and grid[gy][x_end + 1]
                       and not used[gy][x_end + 1]):
                    x_end += 1
                y_end = gy
                while y_end + 1 < h_cells and all(
                        grid[y_end + 1][i] and not used[y_end + 1][i]
                        for i in range(gx, x_end + 1)):
                    y_end += 1
                for yy in range(gy, y_end + 1):
                    for xx in range(gx, x_end + 1):
                        used[yy][xx] = True
                rw = (x_end - gx + 1) * cell
                rh = (y_end - gy + 1) * cell
                rects.append((x0 + gx * cell + rw / 2.0,
                              y0 + gy * cell + rh / 2.0, rw, rh))
                gx = x_end + 1
            else:
                gx += 1
    return rects


def rect_quad(name: str, object_id: int, rect, z: float, color: str) -> dict:
    cx, cy, w, h = rect
    return {"Name": name, "ObjectId": object_id, "ParentId": 0,
            "Position": {"x": round(cx, 5), "y": round(cy, 5), "z": z},
            "Rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
            "Scale": {"x": round(w, 5), "y": round(h, 5), "z": 1.0},
            "BlockType": 1,
            "Properties": {"PrimitiveType": 5, "PrimitiveFlags": 2,
                           "Color": color, "Static": True}}


def tri_area(pts: list[tuple[float, float]]) -> float:
    (ax, ay), (bx, by), (cx, cy) = pts
    return abs((bx - ax) * (cy - ay) - (cx - ax) * (by - ay)) / 2.0


def compile_blocks(blocks: list[dict],
                   rasterize: list[tuple] | None = None,
                   decimate: dict[str, float] | None = None) -> tuple[list[dict], dict]:
    """Expand BT11 triangles into shear-quad tiles; optionally divert selected
    triangle families into rasterized rect quads (rasterize entries:
    (name_substring, out_prefix, split_by_center_x, cell)) and drop sliver
    triangles below a per-family area threshold (decimate: substring->m^2)."""
    out: list[dict] = []
    raster_sets: dict[str, dict] = {}
    rasterize = rasterize or []
    decimate = decimate or {}
    decimated = 0

    def raster_target(bname: str):
        for substr, prefix, split_x, cell in rasterize:
            if substr in bname:
                return substr, prefix, split_x, cell
        return None

    next_id = max(b["ObjectId"] for b in blocks) + 1
    stats = tri_prims.ConversionStats()
    for b in blocks:
        if b["BlockType"] != 11:
            out.append(b)
            continue
        target = raster_target(b["Name"])
        if target is not None:
            substr, prefix, split_x, cell = target
            entry = raster_sets.setdefault(substr, {
                "prefix": prefix, "split_x": split_x, "cell": cell, "tris": [],
                "z": b["Position"]["z"], "color": b["Properties"]["Color"]})
            entry["tris"].append(tri_points(b))
            continue
        min_area = next((a for substr, a in decimate.items()
                         if substr in b["Name"]), 0.0)
        if min_area > 0.0 and tri_area(tri_points(b)) < min_area:
            decimated += 1
            continue
        pr = b["Properties"]
        z = b["Position"]["z"]
        pts = [(pr[k]["x"], pr[k]["y"], pr[k].get("z", 0.0) + z)
               for k in ("PointA", "PointB", "PointC")]
        tiles, next_id, tri_stats = tri_prims.triangle_points_to_primitive_blocks(
            b["Name"], next_id, b.get("ParentId", 0),
            pts[0], pts[1], pts[2], pr["Color"],
            static=pr.get("Static", True))
        stats.add(tri_stats)
        out.extend(tiles)

    raster_count = 0
    for entry in raster_sets.values():
        buckets: dict[str, list] = {}
        if entry["split_x"] is not None:
            for t in entry["tris"]:
                cx = sum(p[0] for p in t) / 3.0
                side = "L" if cx < entry["split_x"] else "R"
                buckets.setdefault(side, []).append(t)
        else:
            buckets[""] = entry["tris"]
        for side, tris in sorted(buckets.items()):
            rects = rasterize_tris_to_rects(tris, entry["cell"])
            for i, rect in enumerate(rects):
                name = (f"{entry['prefix']}-{side}-{i:04d}" if side
                        else f"{entry['prefix']}-{i:04d}")
                out.append(rect_quad(name, next_id, rect, entry["z"],
                                     entry["color"]))
                next_id += 1
                raster_count += 1

    apply_emissive_colors(out)
    summary = {"triangles": stats.triangle_blocks,
               "directTiles": stats.direct_tiles,
               "shearedTiles": stats.sheared_tiles,
               "rasterRects": raster_count,
               "decimated": decimated,
               "skipped": stats.skipped_tiles}
    return out, summary


def apply_emissive_colors(blocks: list[dict]) -> None:
    """Give every visible primitive a raw ColorRgba (1:1 channels, hue
    preserved) so it renders self-lit; pure blacks pin to (0,0,0) and vanish
    into the void. Authored ColorRgba overrides (the backdrop walls) are kept."""
    for b in blocks:
        props = b.get("Properties")
        if b.get("BlockType") != 1 or not props or "ColorRgba" in props:
            continue
        col = props.get("Color", "")
        if not col.startswith("#") or len(col) < 7:
            continue
        r = int(col[1:3], 16) / 255.0
        g = int(col[3:5], 16) / 255.0
        bl = int(col[5:7], 16) / 255.0
        a = int(col[7:9], 16) / 255.0 if len(col) >= 9 else 1.0
        if r + g + bl <= BLACK_CHANNEL_SUM:
            props["ColorRgba"] = {"r": 0.0, "g": 0.0, "b": 0.0, "a": round(a, 4)}
            continue
        props["ColorRgba"] = {"r": round(r, 4), "g": round(g, 4),
                              "b": round(bl, 4), "a": round(a, 4)}


# ---- clip baking --------------------------------------------------------------

def split_group_tweens(emblem: dict) -> tuple[dict, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    staggered: list[dict] = []
    for tw in emblem["tweens"]:
        if tw.get("stagger"):
            staggered.append(tw)
        else:
            grouped.setdefault(tw["group"], []).append(tw)
    return grouped, staggered


def bake_channels(tweens: list[dict], sample_times: list[float]) -> dict:
    """Evaluate one group's tween stack into sampled channels."""
    channels: dict[str, list[float]] = {}
    value_kinds = [tw["kind"] for tw in tweens if tw["kind"] != "opacity"]
    if len([k for k in value_kinds if k in ("scale", "scaleAxis")]) > 1:
        raise ValueError("a group may carry at most one scale tween")
    if value_kinds.count("rotate") > 1 or value_kinds.count("translate") > 1:
        raise ValueError("a group may carry at most one rotate/translate tween")

    for t in sample_times:
        tx = ty = 0.0
        rot = 0.0
        sx = sy = 1.0
        opacity = 1.0
        for tw in tweens:
            kind = tw["kind"]
            if kind == "translate":
                t0 = tw["t0"]
                k = 0.0 if t <= t0 else ease(tw["ease"], (t - t0) / tw["dur"])
                tx = tw["from"][0] + (tw["to"][0] - tw["from"][0]) * k
                ty = tw["from"][1] + (tw["to"][1] - tw["from"][1]) * k
            elif kind == "rotate":
                rot = tween_value(tw, t)
            elif kind == "scale":
                sx = sy = tween_value(tw, t)
            elif kind == "scaleAxis":
                sx = tween_value(tw, t)
            elif kind == "opacity":
                opacity *= tween_value(tw, t)
        for key, val in (("tx", tx), ("ty", ty), ("rot", rot),
                         ("sx", sx), ("sy", sy), ("opacity", opacity)):
            channels.setdefault(key, []).append(round(val, 5))

    identity = {"tx": 0.0, "ty": 0.0, "rot": 0.0, "sx": 1.0, "sy": 1.0,
                "opacity": 1.0}
    return {k: v for k, v in channels.items()
            if any(abs(s - identity[k]) > 1e-4 for s in v)}


def bake_emblem(emblem: dict, sample_times: list[float],
                bindings_override: dict[str, list[str]] | None = None) -> list[dict]:
    grouped, staggered = split_group_tweens(emblem)
    meta = emblem["groups"]

    bindings_by_group: dict[str, list[str]] = {}
    shape_by_id: dict[str, dict] = {}
    for s in emblem["shapes"]:
        bindings_by_group.setdefault(s["group"], []).append(s["id"])
        shape_by_id[s["id"]] = s
    for gid, override in (bindings_override or {}).items():
        bindings_by_group[gid] = list(override)

    out_groups: list[dict] = []
    emitted: set[str] = set()

    def emit(gid: str, tweens: list[dict], bindings: list[str],
             pivot, axis: float, parent: str | None) -> None:
        channels = bake_channels(tweens, sample_times)
        if not channels and parent is None:
            return  # fully static group: leave its blocks un-rigged
        out_groups.append({
            "id": gid,
            "pivot": [round(pivot[0], 5), round(pivot[1], 5)],
            "axisDeg": round(axis, 4),
            **({"parent": parent} if parent else {}),
            "bindings": sorted(bindings),
            "channels": channels,
        })
        emitted.add(gid)

    for gid, g in meta.items():
        tweens = grouped.get(gid, [])
        bindings = bindings_by_group.get(gid, [])
        if not bindings and not any(
                (meta[og].get("parent") == gid) for og in meta):
            continue  # no members and no children: nothing to drive
        emit(gid, tweens, bindings, g.get("pivot", (0.0, 0.0)),
             g.get("axis", 0.0), g.get("parent"))

    # staggered tweens -> one group per block (letters / caption glyphs)
    per_block: dict[str, list[tuple[dict, float]]] = {}
    for tw in staggered:
        for i, bid in enumerate(tw["stagger"]["order"]):
            per_block.setdefault(bid, []).append((tw, i * tw["stagger"]["per"]))
    for bid, entries in per_block.items():
        shape = shape_by_id.get(bid)
        if shape is None:
            continue
        shifted = []
        for tw, delay in entries:
            cp = {k: v for k, v in tw.items() if k != "stagger"}
            cp["t0"] = cp["t0"] + delay
            shifted.append(cp)
        pivot = shape.get("pivot") or [shape.get("x", 0.0), shape.get("y", 0.0)]
        parent_group = shape["group"] if shape["group"] in emitted else None
        emit(f"{bid}#pop", shifted, [bid], pivot, 0.0, parent_group)

    # parents referenced before being emitted must exist: order parents first
    order = {g["id"]: i for i, g in enumerate(out_groups)}
    out_groups.sort(key=lambda g: (0 if "parent" not in g
                                   else order.get(g["parent"], 0) + 1,
                                   order[g["id"]]))
    return out_groups


def main() -> None:
    OUT_LOGOS.mkdir(parents=True, exist_ok=True)
    OUT_ANIMS.mkdir(parents=True, exist_ok=True)

    for builder, src_json, stem, view_height in EMBLEMS:
        emblem = builder()
        raw = json.loads(Path(src_json).read_text(encoding="utf-8"))
        decimate = None
        bindings_override = None
        if stem == "goc-seal":
            decimate = {"map-tri": MAP_MIN_TRI_AREA,
                        "laurel-tri": LAUREL_MIN_TRI_AREA}
        compiled, tri_summary = compile_blocks(raw["Blocks"], None, decimate)
        mer_out = OUT_LOGOS / f"{stem}-emblem.mer.json"
        mer_out.write_text(json.dumps(
            {"RootObjectId": raw.get("RootObjectId", 0), "Blocks": compiled},
            indent=1), encoding="utf-8")
        sha = hashlib.sha256(mer_out.read_bytes()).hexdigest().upper()

        duration = emblem["duration"]
        sample_count = int(math.ceil(duration * SAMPLE_RATE_HZ)) + 1
        sample_times = [min(duration, i / SAMPLE_RATE_HZ)
                        for i in range(sample_count)]
        groups = bake_emblem(emblem, sample_times, bindings_override)

        clip_out = OUT_ANIMS / f"{stem}-intro.clip.json"
        clip_out.write_text(json.dumps({
            "format": "reinforcements.emblem-intro-clip",
            "version": 1,
            "name": f"{stem}-intro",
            "sourceGenerator": "Embel-conversion-fr/tools/bake_intro_clips.py",
            "asset": {"path": f"generated/logos/{stem}-emblem.mer.json",
                      "sha256": sha},
            "sampleRateHz": SAMPLE_RATE_HZ,
            "sampleCount": sample_count,
            "durationSeconds": round(duration, 3),
            "viewHeightMeters": view_height,
            "groups": groups,
        }, indent=1), encoding="utf-8")

        n_blocks = len(compiled)
        print(f"{stem}: {n_blocks} blocks ({tri_summary}), "
              f"{len(groups)} clip groups, {sample_count} samples "
              f"-> {mer_out.name}, {clip_out.name}")


if __name__ == "__main__":
    main()
