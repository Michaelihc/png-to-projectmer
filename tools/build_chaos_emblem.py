"""Assemble the Chaos Insurgency dual emblem into one MER block set.

Variant A (classic, hand-authored simple shapes -- no raster conversion):
red field, white ring, black center ring, three parallel black arrows at
45 deg that pass THROUGH the rings (the middle shaft is interrupted by the
small circle exactly like the source art, via the red cutout layering).

Variant B (plague daisy): geometry reused from the already-converted
converted_mer/ci-plague-opt/ci-plague-opt.json (petals, outline rings, text
band, white disc, red dot), rescaled/re-layered to stack in FRONT of variant
A, with its baked text-stroke quads REPLACED by per-letter TextToys (same
BT8 convention as the GOC seal motto).

Both variants live in ONE block set so the intro timeline can transition
A -> B in place (variant B starts faded out).

Writes converted_mer/chaos-insurgency/chaos-insurgency.json plus
chaos-insurgency.meta.json with animation pivots (arrow tails/head bases,
petal tips + per-petal block lists) consumed by
tools/build_intro_animation_preview.py.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "converted_mer" / "chaos-insurgency"
PLAGUE_JSON = ROOT / "converted_mer" / "ci-plague-opt" / "ci-plague-opt.json"

# palette (variant A)
RED_BG = "#F20D0DFF"
WHITE = "#FFFFFFFF"
BLACK = "#0B0B0BFF"
CREAM = "#F4F1E6FF"
TEXT_COLOR = "#111111"

# deployment rig props (intro-only hardware; hidden in the final emblem)
GUNMETAL = "#2A2F36FF"
GUNMETAL_DARK = "#1B1F24FF"
HAZARD_RED = "#C8102EFF"
STATUS_AMBER = "#E8A33DFF"

CANVAS = 8.0          # world units, emblem centered on (0,0)
WALL_W, WALL_H = 60.0, 34.0   # backdrop wall size (fills the camera view)
DAISY_SCALE = 0.72    # source daisy spans ~9.7 world units -> fit the canvas
DAISY_Z_SHIFT = -0.035  # stack the whole daisy in front of variant A layers

MOTTO = "SHOULD INTERMITTENT VENGEANCE ARM AGAIN HIS RED RIGHT HAND TO PLAGUE US?"
# centerline of the white text band (band spans r 4.49..4.93 in source units),
# so the TextToy glyphs stay clear of both black outline rings
TEXT_R = 4.71 * DAISY_SCALE
TEXT_ARC_DEG = 330.0
TEXT_START_DEG = 86.0         # first glyph polar angle, running clockwise

_next_id = 0


def nid() -> int:
    global _next_id
    _next_id += 1
    return _next_id


def vec(x: float, y: float, z: float) -> dict:
    return {"x": round(x, 5), "y": round(y, 5), "z": round(z, 5)}


def quad(name: str, x: float, y: float, z: float, w: float, h: float,
         rot_z: float, color: str, hdr: tuple | None = None) -> dict:
    props = {"PrimitiveType": 5, "PrimitiveFlags": 2,
             "Color": color, "Static": True}
    if hdr is not None:
        # raw (possibly >1) channel override so the Lit material can feed
        # client bloom -- the skybox is untunable, so the intro background is
        # a massive emissive wall behind the emblem instead.
        props["ColorRgba"] = {"r": hdr[0], "g": hdr[1], "b": hdr[2],
                              "a": hdr[3] if len(hdr) > 3 else 1.0}
    return {"Name": name, "ObjectId": nid(), "ParentId": 0,
            "Position": vec(x, y, z), "Rotation": vec(0, 0, rot_z),
            "Scale": vec(w, h, 1.0), "BlockType": 1, "Properties": props}


def disc(name: str, x: float, y: float, z: float, d: float, color: str) -> dict:
    return {"Name": name, "ObjectId": nid(), "ParentId": 0,
            "Position": vec(x, y, z), "Rotation": vec(90.0, 0, 0),
            "Scale": vec(d, 0.01, d), "BlockType": 1,
            "Properties": {"PrimitiveType": 2, "PrimitiveFlags": 2,
                           "Color": color, "Static": True}}


def tri(name: str, a, b, c, z: float, color: str) -> dict:
    return {"Name": name, "ObjectId": nid(), "ParentId": 0,
            "Position": vec(0, 0, z), "Rotation": vec(0, 0, 0),
            "Scale": vec(1, 1, 1), "BlockType": 11,
            "Properties": {"PointA": vec(a[0], a[1], 0),
                           "PointB": vec(b[0], b[1], 0),
                           "PointC": vec(c[0], c[1], 0),
                           "Color": color, "Thickness": 0.01, "Static": True}}


def text(name: str, x: float, y: float, z: float, rot_z: float,
         char: str, tmp_size: int) -> dict:
    return {"Name": name, "ObjectId": nid(), "ParentId": 0,
            "Position": vec(x, y, z), "Rotation": vec(0, 0, rot_z),
            "Scale": vec(0.08, 0.08, 0.08), "BlockType": 8,
            "Properties": {
                "Text": (f"<align=center><size={tmp_size}><b>"
                         f"<color={TEXT_COLOR}>{char}</color></b></size></align>"),
                "DisplaySize": {"x": 1.5, "y": 0.4}, "Static": True}}


def rot2(x: float, y: float, deg: float) -> tuple[float, float]:
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return (c * x - s * y, s * x + c * y)


# --------------------------------------------------------------------------
# variant B: import the converted daisy
# --------------------------------------------------------------------------

def import_daisy(blocks: list[dict], meta: dict) -> None:
    src = json.loads(PLAGUE_JSON.read_text(encoding="utf-8"))["Blocks"]
    by_id = {b["ObjectId"]: b for b in src}
    id_map: dict[int, int] = {0: 0}
    k = DAISY_SCALE

    kept: list[dict] = []
    for b in src:
        if b["Name"].startswith("plague-text-"):
            continue  # baked text strokes are replaced by TextToys
        kept.append(b)
    kept_ids = {b["ObjectId"] for b in kept}

    for b in kept:
        nb = json.loads(json.dumps(b))  # deep copy
        nb["ObjectId"] = id_map.setdefault(b["ObjectId"], nid())
        parent = b.get("ParentId", 0)
        if parent != 0 and parent not in kept_ids:
            parent = 0
        nb["ParentId"] = id_map.setdefault(parent, nid()) if parent else 0
        top_level = nb["ParentId"] == 0
        if top_level:
            nb["Position"]["x"] = round(nb["Position"]["x"] * k, 5)
            nb["Position"]["y"] = round(nb["Position"]["y"] * k, 5)
            nb["Position"]["z"] = round(nb["Position"]["z"] + DAISY_Z_SHIFT, 5)
            nb["Scale"]["x"] = round(nb["Scale"]["x"] * k, 5)
            # discs carry their diameter in x/z; quads/empties are planar in x/y
            if (nb.get("Properties") or {}).get("PrimitiveType") == 2:
                nb["Scale"]["z"] = round(nb["Scale"]["z"] * k, 5)
            else:
                nb["Scale"]["y"] = round(nb["Scale"]["y"] * k, 5)
        blocks.append(nb)

    # ---- per-petal grouping for the animation --------------------------------
    # compose each petal tile to world space, cluster the 51 petal triangles
    # into 8 petals by polar angle of their composed centroids.
    def composed_centroid(child: dict) -> tuple[float, float]:
        pts = []
        for ux, uy in ((-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)):
            x, y = rot2(ux * child["Scale"]["x"], uy * child["Scale"]["y"],
                        child["Rotation"]["z"])
            x, y = x + child["Position"]["x"], y + child["Position"]["y"]
            parent = by_id.get(child["ParentId"])
            if parent is not None and parent["ObjectId"] != 0:
                x, y = x * parent["Scale"]["x"], y * parent["Scale"]["y"]
                x, y = rot2(x, y, parent["Rotation"]["z"])
                x += parent["Position"]["x"]
                y += parent["Position"]["y"]
            pts.append((x, y))
        return (sum(p[0] for p in pts) / 4.0, sum(p[1] for p in pts) / 4.0)

    tri_info: dict[str, dict] = {}  # tri number -> {angle, pts}
    for b in src:
        m = b["Name"]
        if b["BlockType"] != 1 or not m.startswith("ci-plague-petal-tri-"):
            continue
        num = m.split("ci-plague-petal-tri-")[1].split("-")[0]
        cx, cy = composed_centroid(b)
        info = tri_info.setdefault(num, {"xs": [], "ys": []})
        info["xs"].append(cx)
        info["ys"].append(cy)

    entries = []
    for num, info in tri_info.items():
        cx = sum(info["xs"]) / len(info["xs"])
        cy = sum(info["ys"]) / len(info["ys"])
        entries.append((num, math.degrees(math.atan2(cy, cx)) % 360.0,
                        math.hypot(cx, cy)))
    entries.sort(key=lambda e: e[1])
    # split the sorted angle ring at the 8 largest gaps
    gaps = []
    for i in range(len(entries)):
        nxt = entries[(i + 1) % len(entries)]
        gap = (nxt[1] - entries[i][1]) % 360.0
        gaps.append((gap, i))
    cut_points = sorted(sorted(gaps, reverse=True)[:8], key=lambda g: g[1])
    petals: list[list[tuple[str, float, float]]] = []
    start = (cut_points[-1][1] + 1) % len(entries)
    ordered = entries[start:] + entries[:start]
    cuts = {(c[1] - start) % len(entries) for c in cut_points[:-1]}
    current: list[tuple[str, float, float]] = []
    for i, e in enumerate(ordered):
        current.append(e)
        if i in cuts:
            petals.append(current)
            current = []
    if current:
        petals.append(current)

    for petal in petals:
        nums = [e[0] for e in petal]
        # tip = the composed point farthest from center among this petal's tiles
        best = (0.0, (0.0, 0.0))
        for num in nums:
            info = tri_info[num]
            for cx, cy in zip(info["xs"], info["ys"]):
                r = math.hypot(cx, cy)
                if r > best[0]:
                    best = (r, (cx, cy))
        tip = (best[1][0] * k, best[1][1] * k)
        chord = math.degrees(math.atan2(-tip[1], -tip[0]))  # toward center
        mean_angle = sum(e[1] for e in petal) / len(petal)
        meta["petals"].append({"tris": sorted(nums), "tip": [round(tip[0], 4),
                               round(tip[1], 4)], "chord": round(chord, 2),
                               "angle": round(mean_angle, 2)})


def build() -> None:
    global _next_id
    _next_id = 0
    blocks: list[dict] = []
    meta: dict = {"center": [0.0, 0.0], "arrows": [], "petals": []}

    # ---- backgrounds: massive HDR walls (bg-b stacked just in front of bg-a)
    # sized to fill the whole camera view at cinematic framing distance.
    # 8mm authored gap: two coplanar 30m walls z-fight at cinematic distance
    # even after the runtime depth exaggeration.
    blocks.append(quad("ci-bg-a", 0, 0, -0.001, WALL_W, WALL_H, 0, RED_BG,
                       hdr=(1.8, 0.07, 0.07)))
    blocks.append(quad("ci-bg-b", 0, 0, -0.009, WALL_W, WALL_H, 0, CREAM,
                       hdr=(1.15, 1.12, 1.02)))

    # ---- variant A: white ring (2026-07-16 live review: shrunk from 5.75 --
    # it dominated the frame; the arrows still overshoot past it like the art)
    blocks.append(disc("ci-white-ring-outer", 0, 0, -0.010, 5.0, WHITE))
    blocks.append(disc("ci-white-ring-cutout", 0, 0, -0.012, 4.35, RED_BG))

    # ---- variant A: three arrows at 45 deg, passing through -----------------
    ang = math.radians(45.0)
    dx, dy = math.cos(ang), math.sin(ang)   # arrow direction (up-right)
    nx, ny = -dy, dx                        # left normal
    SHAFT_W = 0.34
    HEAD_LEN = 0.95
    HEAD_HALF_W = 0.52
    arrows = [                              # (tail, tip) measured off the art
        ((-3.44, -1.36), (1.50, 3.50)),     # upper-left arrow
        ((-2.96, -2.86), (2.92, 3.02)),     # middle arrow (through the circle)
        ((-1.36, -3.44), (3.44, 1.36)),     # lower-right arrow
    ]
    for i, (tail, tip) in enumerate(arrows):
        vx, vy = tip[0] - tail[0], tip[1] - tail[1]
        length = math.hypot(vx, vy)
        base = (tip[0] - dx * HEAD_LEN, tip[1] - dy * HEAD_LEN)
        shaft_len = length - HEAD_LEN
        cx = tail[0] + dx * shaft_len / 2.0
        cy = tail[1] + dy * shaft_len / 2.0
        # quad long axis is local +y -> rotate so +y points along the arrow
        blocks.append(quad(f"ci-arrow-{i}-shaft", cx, cy, -0.020,
                           SHAFT_W, shaft_len, 45.0 - 90.0, BLACK))
        left = (base[0] + nx * HEAD_HALF_W, base[1] + ny * HEAD_HALF_W)
        right = (base[0] - nx * HEAD_HALF_W, base[1] - ny * HEAD_HALF_W)
        blocks.append(tri(f"ci-arrow-{i}-head", left, right, tip, -0.020, BLACK))
        meta["arrows"].append({"tail": [tail[0], tail[1]], "dir": 45.0,
                               "headBase": [base[0], base[1]]})

    # ---- variant A: small center ring OVER the middle shaft -----------------
    # (red cutout hides the shaft inside the circle = interrupted, like the art)
    blocks.append(disc("ci-center-ring-outer", 0, 0, -0.024, 1.78, BLACK))
    blocks.append(disc("ci-center-ring-cutout", 0, 0, -0.026, 1.34, RED_BG))

    # ---- deployment rig (intro-only hardware; the timeline hides it at rest) --
    # Signature device: a two-segment radar sweep arm that unfolds from the hub
    # and "paints" the emblem into existence, plus the C-clamp halves that slam
    # together into the center ring and the arc slabs the ring is painted from.
    meta["rig"] = {}

    # hub cap: heavy plate over the center while the rig works (+4 bolt studs)
    blocks.append(disc("ci-rig-hub-cap", 0, 0, -0.058, 1.15, GUNMETAL))
    for i in range(4):
        a = math.radians(45.0 + i * 90.0)
        blocks.append(quad(f"ci-rig-hub-bolt-{i}", 0.38 * math.cos(a),
                           0.38 * math.sin(a), -0.059, 0.14, 0.14,
                           45.0 + i * 90.0, GUNMETAL_DARK))
    blocks.append(quad("ci-rig-hub-lamp", 0, 0, -0.060, 0.16, 0.16, 45.0,
                       STATUS_AMBER))

    # sweep arm: boom (root at hub) + blade (root at boom tip) + red edge.
    # Authored at rest POINTING UP (+y); the fold groups articulate it.
    BOOM_LEN, BOOM_W = 1.35, 0.42
    BLADE_LEN, BLADE_W = 1.25, 0.30
    blocks.append(quad("ci-rig-boom", 0, 0.35 + BOOM_LEN / 2, -0.056,
                       BOOM_W, BOOM_LEN, 0.0, GUNMETAL))
    blocks.append(quad("ci-rig-boom-seam", 0, 0.35 + BOOM_LEN - 0.06, -0.057,
                       BOOM_W * 0.92, 0.07, 0.0, GUNMETAL_DARK))
    blade_root = 0.35 + BOOM_LEN
    blocks.append(quad("ci-rig-blade", 0, blade_root + BLADE_LEN / 2, -0.056,
                       BLADE_W, BLADE_LEN, 0.0, GUNMETAL))
    blocks.append(quad("ci-rig-blade-edge", 0.5 * (BLADE_W + 0.09),
                       blade_root + BLADE_LEN / 2, -0.057, 0.09,
                       BLADE_LEN, 0.0, HAZARD_RED))
    meta["rig"]["hub"] = [0.0, 0.0]
    meta["rig"]["bladeRoot"] = [0.0, blade_root]
    meta["rig"]["armTipRadius"] = blade_root + BLADE_LEN

    # C-clamp halves: chunky half-octagon arcs that slam into the center ring.
    # Arc radius matches the ring centerline (d 1.78/1.34 -> r ~0.78).
    CLAMP_R, CLAMP_W = 0.78, 0.34
    for side, start_deg in (("L", 90.0), ("R", -90.0)):
        for k in range(4):
            a0 = math.radians(start_deg + k * 45.0 + 22.5)
            seg_len = 2.0 * CLAMP_R * math.sin(math.radians(22.5))
            blocks.append(quad(
                f"ci-clamp-{side}-{k}",
                CLAMP_R * math.cos(a0), CLAMP_R * math.sin(a0), -0.055,
                seg_len * 1.06, CLAMP_W,
                math.degrees(a0) + 90.0, GUNMETAL))
        stud_a = math.radians(start_deg + 90.0)
        blocks.append(quad(
            f"ci-clamp-{side}-stud",
            (CLAMP_R + 0.24) * math.cos(stud_a),
            (CLAMP_R + 0.24) * math.sin(stud_a), -0.055,
            0.22, 0.22, math.degrees(stud_a), GUNMETAL_DARK))

    # ring slabs: six 60-degree chords the white ring is painted from
    # (they swap to the true ring once the first sweep completes).
    SLAB_R = (5.0 + 4.35) / 4.0            # ring centerline radius ~2.34
    SLAB_LEN = 2.0 * SLAB_R * math.sin(math.radians(30.0))
    slab_angles = []
    for k in range(6):
        a = math.radians(90.0 - k * 60.0)   # clockwise from the top
        deg = math.degrees(a)
        slab_angles.append(deg % 360.0)
        blocks.append(quad(
            f"ci-slab-{k}",
            SLAB_R * math.cos(a), SLAB_R * math.sin(a), -0.011,
            SLAB_LEN * 1.04, 0.42, deg + 90.0, WHITE))
    meta["rig"]["slabAngles"] = slab_angles
    meta["rig"]["slabRadius"] = SLAB_R

    # ---- variant B: the converted plague daisy ------------------------------
    import_daisy(blocks, meta)

    # ---- variant B: motto TextToys around the wheel --------------------------
    step = TEXT_ARC_DEG / (len(MOTTO) - 1)
    idx = 0
    for ch_i, ch in enumerate(MOTTO):
        polar = TEXT_START_DEG - ch_i * step   # clockwise, spaces keep a slot
        if ch == " ":
            continue
        rad = math.radians(polar)
        x, y = TEXT_R * math.cos(rad), TEXT_R * math.sin(rad)
        # authored z = pure in-plane angle (upright letter facing outward =
        # polar - 90); the runtime rig adds the readable-side 180Y flip
        blocks.append(text(f"ci-text-{idx:02d}-{ch if ch.isalnum() else 'q'}",
                           x, y, -0.110, polar - 90.0, ch, 30))
        idx += 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "chaos-insurgency.json"
    out.write_text(json.dumps({"RootObjectId": 0, "Blocks": blocks}, indent=1),
                   encoding="utf-8")
    meta_out = OUT_DIR / "chaos-insurgency.meta.json"
    meta_out.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(f"wrote {out} ({len(blocks)} blocks, {len(meta['petals'])} petals) "
          f"and {meta_out}")


if __name__ == "__main__":
    build()
