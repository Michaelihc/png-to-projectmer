"""Build an animated intro preview (self-contained HTML+SVG+JS) for the GOC and
Strike-Team emblems.

The animation vocabulary is intentionally restricted to what AdminToys can do at
runtime in SCP:SL: per-block / per-group translate, rotate-about-pivot,
scale-about-pivot (optionally along a local axis), and color-alpha fades. Each
"group" maps 1:1 to an animation empty the blocks would be parented to in-game,
so an approved preview timeline can be baked into a runtime clip without
re-authoring.

Reference motion language (user-supplied videos):
  * arc/ring segments that sweep decelerating around the emblem,
  * bars that float in tilted, lengthen/shorten and fall into place,
  * strokes drawn tip-to-tip (pentagram drawn as one continuous line),
  * pop-in accents and a final whole-emblem settle pulse.

Usage: python tools/build_intro_animation_preview.py
Writes converted_mer/intro-anim-preview.html and per-emblem
<name>.intro-timeline.json next to the source schematics.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MER = ROOT / "converted_mer"

GOC_JSON = MER / "global-occult-coalition" / "global-occult-coalition.json"
STRIKE_JSON = MER / "strike-team" / "strike-team.json"
CHAOS_JSON = MER / "chaos-insurgency" / "chaos-insurgency.json"
CHAOS_META = MER / "chaos-insurgency" / "chaos-insurgency.meta.json"
OUT_HTML = MER / "intro-anim-preview.html"

TEXT_COLOR_RE = re.compile(r"<color=(#[0-9A-Fa-f]{6})>(.*?)</color>")
TEXT_SIZE_RE = re.compile(r"<size=(\d+)>")


def load(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["Blocks"]


def rot_pt(x: float, y: float, deg: float) -> tuple[float, float]:
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return (c * x - s * y, s * x + c * y)


def quad_pts(block: dict, parent: dict | None = None) -> list[tuple[float, float]]:
    p, r, s = block["Position"], block["Rotation"], block["Scale"]
    out = []
    for ux, uy in ((-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)):
        x, y = rot_pt(ux * s["x"], uy * s["y"], r["z"])
        x, y = x + p["x"], y + p["y"]
        if parent is not None:
            # shear-parent composition (TRS hierarchy from the triangle expander)
            pp, pr, ps = parent["Position"], parent["Rotation"], parent["Scale"]
            x, y = rot_pt(x * ps["x"], y * ps["y"], pr["z"])
            x, y = x + pp["x"], y + pp["y"]
        out.append((x, y))
    return out


def tri_pts(block: dict) -> list[tuple[float, float]]:
    pr = block["Properties"]
    return [(pr[k]["x"], pr[k]["y"]) for k in ("PointA", "PointB", "PointC")]


def block_color(block: dict) -> tuple[str, float]:
    raw = (block.get("Properties") or {}).get("Color", "#FFFFFFFF")
    hexpart = raw.lstrip("#")
    rgb = "#" + hexpart[0:6]
    alpha = int(hexpart[6:8], 16) / 255.0 if len(hexpart) >= 8 else 1.0
    return rgb, alpha


def shape_for(block: dict, by_id: dict | None = None) -> dict | None:
    """World-space drawable for one block (kind, geometry, fill, z)."""
    bt = block["BlockType"]
    z = block["Position"]["z"]
    if bt == 1:
        fill, alpha = block_color(block)
        pt = block["Properties"].get("PrimitiveType")
        parent = None
        if by_id is not None:
            cand = by_id.get(block.get("ParentId", 0))
            if cand is not None and cand.get("BlockType") == 0:
                parent = cand
                z = cand["Position"]["z"] + block["Position"]["z"]
        if pt == 2:  # flat cylinder disc facing the viewer
            p, s = block["Position"], block["Scale"]
            return {"kind": "circle", "cx": p["x"], "cy": p["y"], "d": s["x"],
                    "fill": fill, "alpha": alpha, "z": z}
        if pt == 5:  # quad
            return {"kind": "poly", "pts": quad_pts(block, parent),
                    "fill": fill, "alpha": alpha, "z": z}
        return None
    if bt == 11:  # triangle
        fill, alpha = block_color(block)
        return {"kind": "poly", "pts": tri_pts(block), "fill": fill,
                "alpha": alpha, "z": z}
    if bt == 8:  # TextToy letter
        pr = block["Properties"]
        m = TEXT_COLOR_RE.search(pr.get("Text", ""))
        fill = m.group(1) if m else "#FFFFFF"
        char = m.group(2) if m else "?"
        sm = TEXT_SIZE_RE.search(pr.get("Text", ""))
        size = int(sm.group(1)) * 0.0075 if sm else 0.15
        return {"kind": "text", "x": block["Position"]["x"],
                "y": block["Position"]["y"], "size": round(size, 4),
                # TMP toys face the viewer flipped; +180 recovers the upright arc
                # orientation seen in-game / in the map-zoom render.
                "rot": block["Rotation"]["z"] + 180.0,
                "char": char, "fill": fill, "alpha": 1.0, "z": z}
    return None


def centroid(pts: list[tuple[float, float]]) -> tuple[float, float]:
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def build_emblem(blocks: list[dict], group_of, groups_meta: dict,
                 tweens: list[dict], duration: float, name: str) -> dict:
    by_id = {b["ObjectId"]: b for b in blocks}
    shapes = []
    for b in blocks:
        s = shape_for(b, by_id)
        if s is None:
            continue
        s["id"] = b["Name"]
        s["group"] = group_of(b["Name"])
        shapes.append(s)
    # Draw order: LESS negative z first (back), more negative z on top (viewer side).
    shapes.sort(key=lambda s: -s["z"])
    return {"name": name, "shapes": shapes, "groups": groups_meta,
            "tweens": tweens, "duration": duration}


# --------------------------------------------------------------------------
# Strike-Team choreography
# --------------------------------------------------------------------------

def build_strike() -> dict:
    blocks = load(STRIKE_JSON)
    C = (-0.0167, 0.0167)  # emblem center

    def tris_starting(prefix: str) -> list[tuple[float, float]]:
        pts: list[tuple[float, float]] = []
        for b in blocks:
            if b["BlockType"] == 11 and b["Name"].startswith(prefix):
                pts.extend(tri_pts(b))
        return pts

    def group_of(bname: str) -> str:
        if bname.startswith("strike-outer-circle"):
            return "outer"
        if bname.startswith("strike-center-ring"):
            return "centerRing"
        m = re.match(r"strike-ring-separator-(\d)", bname)
        if m:
            return f"sep{m.group(1)}"
        m = re.match(r"strike-hex-side-(\d)", bname)
        if m:
            return f"hex{m.group(1)}"
        if bname.startswith("strike-star-point"):
            return "star"
        m = re.match(r"strike-compass-ray-(\d)", bname)
        if m:
            return f"ray{m.group(1)}"
        m = re.match(r"strike-marker-(\d)", bname)
        if m:
            return f"marker{m.group(1)}"
        return "misc"

    groups: dict[str, dict] = {
        "outer": {"pivot": C},
        "centerRing": {"pivot": C},
        "star": {"pivot": C},
        "misc": {"pivot": C},
    }
    for i in range(3):
        groups[f"sep{i}"] = {"pivot": C}
        gpts = tris_starting(f"strike-marker-{i}-")
        groups[f"marker{i}"] = {"pivot": centroid(gpts)}
    # hex sides: pivot = side centroid, axis = edge direction, normal = outward
    for i in range(6):
        gpts = tris_starting(f"strike-hex-side-{i}-")
        cen = centroid(gpts)
        best = (0.0, (0.0, 0.0))
        for a in gpts:
            for b2 in gpts:
                d2 = (a[0] - b2[0]) ** 2 + (a[1] - b2[1]) ** 2
                if d2 > best[0]:
                    best = (d2, (b2[0] - a[0], b2[1] - a[1]))
        axis = math.degrees(math.atan2(best[1][1], best[1][0]))
        nx, ny = cen[0] - C[0], cen[1] - C[1]
        nl = math.hypot(nx, ny)
        groups[f"hex{i}"] = {"pivot": cen, "axis": axis,
                             "normal": (nx / nl, ny / nl)}
    # spikes: the three long needles (rays 0-2) EXPAND IN -- anchored at their
    # outer tip on the hexagon corner, lengthening inward toward the center.
    # The three short ticks (rays 3-5) grow outward from their inner end.
    for i in range(6):
        gpts = tris_starting(f"strike-compass-ray-{i}")
        near = min(gpts, key=lambda p: (p[0] - C[0]) ** 2 + (p[1] - C[1]) ** 2)
        far = max(gpts, key=lambda p: (p[0] - C[0]) ** 2 + (p[1] - C[1]) ** 2)
        span = math.hypot(far[0] - near[0], far[1] - near[1])
        anchor, tip = (far, near) if span > 1.5 else (near, far)
        axis = math.degrees(math.atan2(tip[1] - anchor[1], tip[0] - anchor[0]))
        groups[f"ray{i}"] = {"pivot": anchor, "axis": axis}

    tweens: list[dict] = []

    # Phase 1 -- ring ignition: center ring pops from nothing, its three gap
    # separators spin decelerating around it (the reference "arc spinner").
    tweens.append({"group": "centerRing", "t0": 0.0, "dur": 0.85,
                   "ease": "outBack:1.4", "kind": "scale", "from": 0.0, "to": 1.0})
    for i in range(3):
        tweens.append({"group": f"sep{i}", "t0": 0.10, "dur": 1.9,
                       "ease": "outQuint", "kind": "rotate",
                       "from": 210.0 + i * 35.0, "to": 0.0, "pivot": C})

    # Phase 2 -- hexagon assembly: each side floats outside, tilted and short;
    # it lengthens, un-tilts and falls into place (clockwise, staggered).
    for order, i in enumerate(range(6)):
        t0 = 0.55 + order * 0.17
        g = groups[f"hex{i}"]
        nx, ny = g["normal"]
        tweens += [
            {"group": f"hex{i}", "t0": t0, "dur": 0.78, "ease": "outCubic",
             "kind": "translate", "from": [nx * 1.5, ny * 1.5], "to": [0, 0]},
            {"group": f"hex{i}", "t0": t0, "dur": 0.78, "ease": "outBack:1.2",
             "kind": "rotate", "from": 26.0, "to": 0.0},
            {"group": f"hex{i}", "t0": t0, "dur": 0.78, "ease": "outCubic",
             "kind": "scaleAxis", "from": 0.15, "to": 1.0},
            {"group": f"hex{i}", "t0": t0, "dur": 0.24, "ease": "outCubic",
             "kind": "opacity", "from": 0.0, "to": 1.0},
        ]

    # Phase 3 -- outer perimeter contracts into fit around the hexagon.
    tweens += [
        {"group": "outer", "t0": 2.10, "dur": 1.0, "ease": "outCubic",
         "kind": "scale", "from": 1.32, "to": 1.0},
        {"group": "outer", "t0": 2.10, "dur": 0.35, "ease": "outCubic",
         "kind": "opacity", "from": 0.0, "to": 1.0},
    ]

    # Phase 4 -- spikes expand in: the long needles thrust inward from the
    # hexagon corners toward the core, then the short ticks flick outward.
    for order, i in enumerate([0, 1, 2]):
        t0 = 2.85 + order * 0.15
        tweens += [
            {"group": f"ray{i}", "t0": t0, "dur": 0.55, "ease": "outQuint",
             "kind": "scaleAxis", "from": 0.0, "to": 1.0},
            {"group": f"ray{i}", "t0": t0, "dur": 0.15, "ease": "outCubic",
             "kind": "opacity", "from": 0.0, "to": 1.0},
        ]
    for order, i in enumerate([3, 4, 5]):
        t0 = 3.45 + order * 0.12
        tweens += [
            {"group": f"ray{i}", "t0": t0, "dur": 0.4, "ease": "outCubic",
             "kind": "scaleAxis", "from": 0.0, "to": 1.0},
            {"group": f"ray{i}", "t0": t0, "dur": 0.15, "ease": "outCubic",
             "kind": "opacity", "from": 0.0, "to": 1.0},
        ]

    # Phase 5 -- corner markers pop in sequence (status lights).
    for order, i in enumerate([0, 1, 2]):
        t0 = 3.90 + order * 0.18
        tweens.append({"group": f"marker{i}", "t0": t0, "dur": 0.32,
                       "ease": "outBack:1.8", "kind": "scale",
                       "from": 0.0, "to": 1.0})

    # Phase 6 -- star ignition: the core star spins up out of nothing.
    tweens += [
        {"group": "star", "t0": 4.45, "dur": 0.9, "ease": "outBack:1.35",
         "kind": "scale", "from": 0.0, "to": 1.0},
        {"group": "star", "t0": 4.45, "dur": 0.9, "ease": "outQuint",
         "kind": "rotate", "from": -120.0, "to": 0.0},
    ]

    return build_emblem(blocks, group_of, groups, tweens, 5.7,
                        "GOC Physics Strike Division")


# --------------------------------------------------------------------------
# Global Occult Coalition choreography
# --------------------------------------------------------------------------

def build_goc() -> dict:
    blocks = load(GOC_JSON)
    C = (0.00145, 0.00374)  # emblem center
    LAUREL_PIVOT = (0.00145, -2.1)

    letters = [b["Name"] for b in blocks if b["BlockType"] == 8]

    def group_of(bname: str) -> str:
        m = re.match(r"goc-ring-(\d)", bname)
        if m:
            return f"ring{m.group(1)}"
        m = re.match(r"goc-globe-meridian-(\d)", bname)
        if m:
            return f"mer{m.group(1)}"
        m = re.match(r"goc-star-bar-(\d)", bname)
        if m:
            return f"bar{m.group(1)}"
        if bname.startswith("goc-star-cap"):
            return "caps"
        if bname.startswith("goc-text-"):
            return "text"
        if "map-tri" in bname:
            return "map"
        return "laurel"  # split L/R below by geometry

    # laurel needs a geometric split -> patch group ids after shape build
    tri_centroids = {}
    meridian_axis = {}
    bar_meta = {}
    for b in blocks:
        if "laurel-tri" in b["Name"]:
            tri_centroids[b["Name"]] = centroid(tri_pts(b))
        m = re.match(r"goc-globe-meridian-(\d)", b["Name"])
        if m:
            # quad long axis = local +y rotated by rot.z
            meridian_axis[f"mer{m.group(1)}"] = b["Rotation"]["z"] + 90.0
        m = re.match(r"goc-star-bar-(\d)", b["Name"])
        if m:
            p, th = b["Position"], math.radians(b["Rotation"]["z"])
            half = b["Scale"]["y"] / 2.0
            dx, dy = -math.sin(th) * half, math.cos(th) * half
            # stroke chain runs start->end; bars 0..4 are already tip-to-tip
            bar_meta[f"bar{m.group(1)}"] = {
                "pivot": (p["x"] - dx, p["y"] - dy),
                "axis": b["Rotation"]["z"] + 90.0,
            }

    groups: dict[str, dict] = {
        "map": {"pivot": C},
        "caps": {"pivot": C},
        "text": {"pivot": C},
        "laurelL": {"pivot": LAUREL_PIVOT},
        "laurelR": {"pivot": LAUREL_PIVOT},
    }
    for i in range(3):
        groups[f"ring{i}"] = {"pivot": C}
    for gid, axis in meridian_axis.items():
        groups[gid] = {"pivot": C, "axis": axis}
    for gid, meta in bar_meta.items():
        groups[gid] = {"pivot": meta["pivot"], "axis": meta["axis"]}

    tweens: list[dict] = []

    # Phase 1 -- broadcast rings radiate from the center, inner first.
    for order, i in enumerate([2, 1, 0]):
        tweens.append({"group": f"ring{i}", "t0": 0.0 + order * 0.25,
                       "dur": 0.8, "ease": "outCubic", "kind": "scale",
                       "from": 0.0, "to": 1.0})

    # Phase 2 -- graticule draws through the center; the world map resolves.
    for order, gid in enumerate(["mer0", "mer2", "mer1", "mer3"]):
        t0 = 0.85 + order * 0.15
        tweens += [
            {"group": gid, "t0": t0, "dur": 0.6, "ease": "inOutCubic",
             "kind": "scaleAxis", "from": 0.0, "to": 1.0},
            {"group": gid, "t0": t0, "dur": 0.2, "ease": "outCubic",
             "kind": "opacity", "from": 0.0, "to": 1.0},
        ]
    tweens += [
        {"group": "map", "t0": 1.55, "dur": 1.0, "ease": "outCubic",
         "kind": "scale", "from": 0.86, "to": 1.0},
        {"group": "map", "t0": 1.55, "dur": 1.0, "ease": "outCubic",
         "kind": "opacity", "from": 0.0, "to": 1.0},
    ]

    # Phase 3 -- laurels swing up around the emblem like honors being raised.
    tweens += [
        {"group": "laurelL", "t0": 2.30, "dur": 1.15, "ease": "outBack:1.1",
         "kind": "rotate", "from": -55.0, "to": 0.0},
        {"group": "laurelL", "t0": 2.30, "dur": 0.30, "ease": "outCubic",
         "kind": "opacity", "from": 0.0, "to": 1.0},
        {"group": "laurelR", "t0": 2.48, "dur": 1.15, "ease": "outBack:1.1",
         "kind": "rotate", "from": 55.0, "to": 0.0},
        {"group": "laurelR", "t0": 2.48, "dur": 0.30, "ease": "outCubic",
         "kind": "opacity", "from": 0.0, "to": 1.0},
    ]

    # Phase 4 -- the pentagram is drawn as one continuous stroke, tip to tip
    # (bars 0..4 chain start->end), then the point caps snap on.
    for i in range(5):
        t0 = 3.65 + i * 0.28
        tweens += [
            {"group": f"bar{i}", "t0": t0, "dur": 0.31, "ease": "inOutCubic",
             "kind": "scaleAxis", "from": 0.0, "to": 1.0},
            {"group": f"bar{i}", "t0": t0, "dur": 0.05, "ease": "outCubic",
             "kind": "opacity", "from": 0.0, "to": 1.0},
        ]
    tweens.append({"group": "caps", "t0": 5.05, "dur": 0.45,
                   "ease": "outBack:1.6", "kind": "scale", "from": 0.0,
                   "to": 1.0})

    # Phase 5 -- motto letters cascade around the seal.
    tweens.append({"group": "text", "t0": 5.30, "dur": 0.20, "ease": "outBack:1.5",
                   "kind": "scale", "from": 0.0, "to": 1.0, "pivot": "block",
                   "stagger": {"per": 0.022, "order": letters}})
    tweens.append({"group": "text", "t0": 5.30, "dur": 0.15, "ease": "outCubic",
                   "kind": "opacity", "from": 0.0, "to": 1.0,
                   "stagger": {"per": 0.022, "order": letters}})

    emblem = build_emblem(blocks, group_of, groups, tweens, 6.9,
                          "Global Occult Coalition")
    # split laurel group by centroid x
    for s in emblem["shapes"]:
        if s["group"] == "laurel":
            cx = tri_centroids[s["id"]][0]
            s["group"] = "laurelL" if cx < C[0] else "laurelR"
    # per-letter pivots for the stagger pop
    for s in emblem["shapes"]:
        if s["kind"] == "text":
            s["pivot"] = [s["x"], s["y"]]
    return emblem


# --------------------------------------------------------------------------
# Chaos Insurgency choreography: variant A build, then transition to the
# plague daisy (variant B) in place.
# --------------------------------------------------------------------------

def build_chaos() -> dict:
    blocks = load(CHAOS_JSON)
    meta = json.loads(CHAOS_META.read_text(encoding="utf-8"))
    C = (0.0, 0.0)

    tri_to_petal = {}
    for k, petal in enumerate(meta["petals"]):
        for num in petal["tris"]:
            tri_to_petal[num] = k

    letters = [b["Name"] for b in blocks if b["BlockType"] == 8]

    def group_of(bname: str) -> str:
        if bname == "ci-bg-a":
            return "bgA"
        if bname == "ci-bg-b":
            return "bgB"
        if bname.startswith("ci-white-ring"):
            return "whiteRing"
        if bname.startswith("ci-center-ring"):
            return "centerRing"
        m = re.match(r"ci-arrow-(\d)-shaft", bname)
        if m:
            return f"arrow{m.group(1)}"
        m = re.match(r"ci-arrow-(\d)-head", bname)
        if m:
            return f"head{m.group(1)}"
        m = re.match(r"ci-plague-petal-tri-(\d+)-", bname)
        if m:
            return f"petal{tri_to_petal.get(m.group(1), 0)}"
        if bname == "ci-plague-red-dot":
            return "dot"
        if bname.startswith("ci-plague"):
            return "wheelChrome"
        if bname.startswith("ci-text-"):
            return "text"
        return "misc"

    groups: dict[str, dict] = {
        "bgA": {"pivot": C}, "bgB": {"pivot": C},
        "whiteRing": {"pivot": C}, "centerRing": {"pivot": C},
        "arrowsRoot": {"pivot": C}, "wheelRoot": {"pivot": C},
        "wheelChrome": {"pivot": C}, "dot": {"pivot": C},
        "text": {"pivot": C}, "misc": {"pivot": C},
    }
    for i, arrow in enumerate(meta["arrows"]):
        groups[f"arrow{i}"] = {"pivot": arrow["tail"], "axis": arrow["dir"],
                               "parent": "arrowsRoot"}
        groups[f"head{i}"] = {"pivot": arrow["headBase"],
                              "parent": f"arrow{i}"}
    for k, petal in enumerate(meta["petals"]):
        groups[f"petal{k}"] = {"pivot": petal["tip"], "axis": petal["chord"],
                               "parent": "wheelRoot"}

    tweens: list[dict] = []

    # ---- variant A build -----------------------------------------------------
    tweens.append({"group": "bgA", "t0": 0.0, "dur": 0.25, "ease": "outCubic",
                   "kind": "opacity", "from": 0.0, "to": 1.0})
    tweens.append({"group": "whiteRing", "t0": 0.25, "dur": 0.7,
                   "ease": "outBack:1.3", "kind": "scale", "from": 0.0, "to": 1.0})
    tweens.append({"group": "centerRing", "t0": 0.8, "dur": 0.5,
                   "ease": "outBack:1.6", "kind": "scale", "from": 0.0, "to": 1.0})
    # arrows fired along their own 45-deg axis, middle first; each streaks in
    # (translate + lengthen from the tail), then its head snaps on at arrival.
    fire = math.radians(45.0)
    off = (-4.8 * math.cos(fire), -4.8 * math.sin(fire))
    for order, i in enumerate([1, 0, 2]):
        t0 = 1.3 + order * 0.3
        tweens += [
            {"group": f"arrow{i}", "t0": t0, "dur": 0.6, "ease": "outQuint",
             "kind": "translate", "from": [off[0], off[1]], "to": [0, 0]},
            {"group": f"arrow{i}", "t0": t0, "dur": 0.6, "ease": "outQuint",
             "kind": "scaleAxis", "from": 0.1, "to": 1.0},
            {"group": f"arrow{i}", "t0": t0, "dur": 0.12, "ease": "outCubic",
             "kind": "opacity", "from": 0.0, "to": 1.0},
            {"group": f"head{i}", "t0": t0 + 0.45, "dur": 0.22,
             "ease": "outBack:2.2", "kind": "scale", "from": 0.0, "to": 1.0},
        ]

    # ---- transition A -> B ----------------------------------------------------
    # arrows spin up around the center and collapse into it...
    tweens += [
        {"group": "arrowsRoot", "t0": 5.3, "dur": 1.9, "ease": "inOutCubic",
         "kind": "rotate", "from": 0.0, "to": 540.0, "pivot": C},
        {"group": "arrowsRoot", "t0": 6.55, "dur": 0.7, "ease": "inOutCubic",
         "kind": "scale", "from": 1.0, "to": 0.0, "pivot": C},
    ]
    for i in range(3):
        tweens.append({"group": f"arrow{i}", "t0": 6.7, "dur": 0.5,
                       "ease": "outCubic", "kind": "opacity",
                       "from": 1.0, "to": 0.0})
        tweens.append({"group": f"head{i}", "t0": 6.7, "dur": 0.5,
                       "ease": "outCubic", "kind": "opacity",
                       "from": 1.0, "to": 0.0})
    # ...while the field bleaches from red to parchment and the rings dissolve
    tweens += [
        {"group": "whiteRing", "t0": 6.5, "dur": 0.6, "ease": "outCubic",
         "kind": "opacity", "from": 1.0, "to": 0.0},
        {"group": "centerRing", "t0": 6.6, "dur": 0.5, "ease": "outCubic",
         "kind": "opacity", "from": 1.0, "to": 0.0},
        {"group": "bgB", "t0": 6.5, "dur": 0.9, "ease": "inOutCubic",
         "kind": "opacity", "from": 0.0, "to": 1.0},
    ]
    # the daisy spins in decelerating; each petal expands in from the rim
    tweens += [
        {"group": "wheelRoot", "t0": 6.9, "dur": 2.0, "ease": "outQuint",
         "kind": "rotate", "from": -300.0, "to": 0.0, "pivot": C},
        {"group": "wheelChrome", "t0": 6.9, "dur": 0.5, "ease": "outCubic",
         "kind": "opacity", "from": 0.0, "to": 1.0},
        {"group": "wheelChrome", "t0": 6.9, "dur": 0.9, "ease": "outCubic",
         "kind": "scale", "from": 1.22, "to": 1.0},
    ]
    petal_order = sorted(range(len(meta["petals"])),
                         key=lambda k: -((meta["petals"][k]["angle"] - 90) % 360))
    for order, k in enumerate(petal_order):
        t0 = 7.05 + order * 0.09
        tweens += [
            {"group": f"petal{k}", "t0": t0, "dur": 0.5, "ease": "outCubic",
             "kind": "scaleAxis", "from": 0.0, "to": 1.0},
            {"group": f"petal{k}", "t0": t0, "dur": 0.15, "ease": "outCubic",
             "kind": "opacity", "from": 0.0, "to": 1.0},
        ]
    tweens.append({"group": "dot", "t0": 8.6, "dur": 0.4,
                   "ease": "outBack:1.8", "kind": "scale", "from": 0.0,
                   "to": 1.0})
    # motto cascades around the wheel (per-letter TextToys, like the GOC seal)
    tweens.append({"group": "text", "t0": 8.95, "dur": 0.20,
                   "ease": "outBack:1.5", "kind": "scale", "from": 0.0,
                   "to": 1.0, "pivot": "block",
                   "stagger": {"per": 0.028, "order": letters}})
    tweens.append({"group": "text", "t0": 8.95, "dur": 0.15, "ease": "outCubic",
                   "kind": "opacity", "from": 0.0, "to": 1.0,
                   "stagger": {"per": 0.028, "order": letters}})

    emblem = build_emblem(blocks, group_of, groups, tweens, 11.2,
                          "Chaos Insurgency — classic → plague daisy")
    for s in emblem["shapes"]:
        if s["kind"] == "text":
            s["pivot"] = [s["x"], s["y"]]
    return emblem


# --------------------------------------------------------------------------
# HTML emission
# --------------------------------------------------------------------------

HTML_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>GOC / Strike Division — Intro Animation Preview</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; background: #06080d; color: #cdd6e4;
         font: 14px/1.5 "Segoe UI", system-ui, sans-serif; }
  header { padding: 14px 22px 4px; }
  header h1 { font-size: 17px; margin: 0; letter-spacing: .04em; }
  header p { margin: 4px 0 0; color: #7d8aa0; font-size: 12.5px; }
  .row { display: flex; flex-wrap: wrap; gap: 18px; padding: 14px 22px 26px; }
  .card { background: #0b0e15; border: 1px solid #1b2434; border-radius: 10px;
          padding: 14px 16px 12px; flex: 1 1 460px; max-width: 720px; }
  .card h2 { font-size: 14px; margin: 0 0 8px; color: #9fb6d8;
             letter-spacing: .06em; text-transform: uppercase; }
  svg { width: 100%; height: auto; display: block; background: #000;
        border-radius: 6px; }
  .controls { display: flex; align-items: center; gap: 10px; margin-top: 10px; }
  button { background: #16233a; color: #cfe0f5; border: 1px solid #2c4266;
           border-radius: 6px; padding: 5px 14px; cursor: pointer; font: inherit; }
  button:hover { background: #1d2f4e; }
  input[type=range] { flex: 1; accent-color: #4d82c4; }
  .t { min-width: 76px; text-align: right; font-variant-numeric: tabular-nums;
       color: #8fa3bf; font-size: 12.5px; }
  select { background: #16233a; color: #cfe0f5; border: 1px solid #2c4266;
           border-radius: 6px; padding: 4px 6px; font: inherit; }
  .phase { margin-top: 6px; font-size: 12px; color: #6f80a8; min-height: 16px; }
</style>
</head>
<body>
<header>
  <h1>Intro Animation Preview — GOC &amp; Physics Strike Division emblems</h1>
  <p>All motion is AdminToy-implementable (group translate / rotate / scale about
     pivots + color-alpha). Groups match the animation empties the in-game build
     would parent blocks to.</p>
</header>
<div class="row" id="cards"></div>
<script>
const EMBLEMS = __DATA__;

// ---- easing ----------------------------------------------------------------
function ease(name, t) {
  t = Math.min(1, Math.max(0, t));
  if (name === "linear") return t;
  if (name === "outCubic") return 1 - Math.pow(1 - t, 3);
  if (name === "inOutCubic") return t < .5 ? 4*t*t*t : 1 - Math.pow(-2*t+2,3)/2;
  if (name === "outQuint") return 1 - Math.pow(1 - t, 5);
  if (name.startsWith("outBack")) {
    const k = parseFloat(name.split(":")[1] || "1.7");
    const u = t - 1;
    return 1 + u*u*((k+1)*u + k);
  }
  if (name === "pulse") return Math.sin(Math.PI * t); // 0 -> 1 -> 0
  return t;
}

// ---- 2x3 matrices (SVG order a,b,c,d,e,f) ----------------------------------
const I = [1,0,0,1,0,0];
function mul(m, n) {
  return [ m[0]*n[0]+m[2]*n[1], m[1]*n[0]+m[3]*n[1],
           m[0]*n[2]+m[2]*n[3], m[1]*n[2]+m[3]*n[3],
           m[0]*n[4]+m[2]*n[5]+m[4], m[1]*n[4]+m[3]*n[5]+m[5] ];
}
function T(x,y){ return [1,0,0,1,x,y]; }
function R(deg){ const r=deg*Math.PI/180, c=Math.cos(r), s=Math.sin(r);
                 return [c,s,-s,c,0,0]; }
function S(x,y){ return [x,0,0,y,0,0]; }
function aboutPivot(m, px, py){ return mul(T(px,py), mul(m, T(-px,-py))); }

// ---- tween evaluation -------------------------------------------------------
function tweenValue(tw, t, extraDelay) {
  const t0 = tw.t0 + (extraDelay || 0);
  if (tw.kind === "scale" && tw.ease === "pulse") {
    if (t < t0) return tw.from;
    const k = ease("pulse", (t - t0) / tw.dur);
    return tw.from + (tw.to - tw.from) * k;
  }
  const k = t <= t0 ? 0 : ease(tw.ease, (t - t0) / tw.dur);
  return tw.from + (tw.to - tw.from) * k;
}

function tweenMatrix(tw, v, pivot, axis) {
  if (tw.kind === "translate") {
    // v interpolates each component
    return T(v[0], v[1]);
  }
  if (tw.kind === "rotate") return aboutPivot(R(v), pivot[0], pivot[1]);
  if (tw.kind === "scale")  return aboutPivot(S(v, v), pivot[0], pivot[1]);
  if (tw.kind === "scaleAxis") {
    const m = mul(R(axis), mul(S(v, 1), R(-axis)));
    return aboutPivot(m, pivot[0], pivot[1]);
  }
  return I;
}

// ---- per-emblem player ------------------------------------------------------
function makePlayer(emblem, svg, elements) {
  const groups = emblem.groups;
  const groupTweens = {};   // gid -> tweens without stagger
  const blockTweens = {};   // blockId -> [{tw, delay}]
  for (const tw of emblem.tweens) {
    if (tw.stagger) {
      tw.stagger.order.forEach((bid, i) => {
        (blockTweens[bid] = blockTweens[bid] || []).push(
          { tw, delay: i * tw.stagger.per });
      });
    } else {
      (groupTweens[tw.group] = groupTweens[tw.group] || []).push(tw);
    }
  }

  function groupState(gid, t) {
    let m = I, op = 1;
    for (const tw of (groupTweens[gid] || [])) {
      if (tw.kind === "opacity") { op *= tweenValue(tw, t); continue; }
      const g = groups[gid] || {};
      let pivot = tw.pivot && tw.pivot !== "block" ? tw.pivot
                : (g.pivot || [0, 0]);
      let v;
      if (tw.kind === "translate") {
        const k = t <= tw.t0 ? 0 : ease(tw.ease, (t - tw.t0) / tw.dur);
        v = [ tw.from[0] + (tw.to[0]-tw.from[0])*k,
              tw.from[1] + (tw.to[1]-tw.from[1])*k ];
      } else {
        v = tweenValue(tw, t);
      }
      m = mul(m, tweenMatrix(tw, v, pivot, g.axis || 0));
    }
    return { m, op };
  }

  function render(t) {
    const rootState = groupState("__root__", t);
    const cache = {};
    function chainState(gid) {
      if (cache[gid]) return cache[gid];
      const own = groupState(gid, t);
      const parent = (groups[gid] || {}).parent;
      let m = own.m, op = own.op;
      if (parent) {
        const ps = chainState(parent);
        m = mul(ps.m, own.m);
        op *= ps.op;
      }
      return cache[gid] = { m, op };
    }
    for (const el of elements) {
      const s = el.shape;
      const gs = chainState(s.group);
      let m = mul(rootState.m, gs.m);
      let op = rootState.op * gs.op;
      for (const bt of (blockTweens[s.id] || [])) {
        const { tw, delay } = bt;
        if (tw.kind === "opacity") {
          op *= tweenValue(tw, t, delay);
          continue;
        }
        const pivot = tw.pivot === "block" && s.pivot ? s.pivot
                    : (groups[s.group] || {}).pivot || [0, 0];
        const v = tweenValue(tw, t, delay);
        m = mul(m, tweenMatrix(tw, v, pivot, 0));
      }
      if (s.kind === "text") m = mul(m, el.staticM);
      el.node.setAttribute("transform", `matrix(${m.join(" ")})`);
      const finalOp = op * s.alpha;
      el.node.setAttribute("opacity", finalOp.toFixed(3));
    }
  }
  return { render };
}

// ---- build DOM --------------------------------------------------------------
const players = {};
for (const emblem of EMBLEMS) {
  // bounds
  let minx=1e9, maxx=-1e9, miny=1e9, maxy=-1e9;
  for (const s of emblem.shapes) {
    let pts = [];
    if (s.kind === "poly") pts = s.pts;
    else if (s.kind === "circle")
      pts = [[s.cx-s.d/2, s.cy-s.d/2],[s.cx+s.d/2, s.cy+s.d/2]];
    else pts = [[s.x-0.2, s.y-0.2],[s.x+0.2, s.y+0.2]];
    for (const p of pts) {
      minx=Math.min(minx,p[0]); maxx=Math.max(maxx,p[0]);
      miny=Math.min(miny,p[1]); maxy=Math.max(maxy,p[1]);
    }
  }
  const pad = 0.55;
  minx-=pad; maxx+=pad; miny-=pad; maxy+=pad;

  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `<h2>${emblem.name}</h2>`;
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `${minx} ${-maxy} ${maxx-minx} ${maxy-miny}`);
  card.appendChild(svg);
  // y-flip container: geometry stays in world coords (y up)
  const flip = document.createElementNS(NS, "g");
  flip.setAttribute("transform", "scale(1,-1)");
  svg.appendChild(flip);

  const elements = [];
  for (const s of emblem.shapes) {
    let node, staticM = null;
    if (s.kind === "poly") {
      node = document.createElementNS(NS, "polygon");
      node.setAttribute("points", s.pts.map(p => p.join(",")).join(" "));
    } else if (s.kind === "circle") {
      node = document.createElementNS(NS, "circle");
      node.setAttribute("cx", s.cx); node.setAttribute("cy", s.cy);
      node.setAttribute("r", s.d / 2);
    } else {
      node = document.createElementNS(NS, "text");
      node.textContent = s.char;
      node.setAttribute("font-size", (s.size || 0.15).toString());
      node.setAttribute("font-family", "Arial, sans-serif");
      node.setAttribute("font-weight", "bold");
      node.setAttribute("text-anchor", "middle");
      node.setAttribute("dominant-baseline", "central");
      // static local placement composed after animated matrices
      staticM = mul(T(s.x, s.y), mul(R(s.rot), S(1,-1)));
    }
    node.setAttribute("fill", s.fill);
    flip.appendChild(node);
    elements.push({ shape: s, node, staticM });
  }

  const controls = document.createElement("div");
  controls.className = "controls";
  controls.innerHTML = `
    <button class="replay">Replay</button>
    <button class="pause">Pause</button>
    <input type="range" min="0" max="${emblem.duration}" step="0.01" value="0">
    <span class="t">0.00 s</span>
    <select><option value="1">1×</option><option value="0.5">0.5×</option>
      <option value="0.25">0.25×</option></select>`;
  card.appendChild(controls);
  const phase = document.createElement("div");
  phase.className = "phase";
  card.appendChild(phase);
  document.getElementById("cards").appendChild(card);

  const player = makePlayer(emblem, svg, elements);
  const slider = controls.querySelector("input");
  const tlabel = controls.querySelector(".t");
  const speedSel = controls.querySelector("select");
  let t = 0, playing = true, last = performance.now();

  function show(tt) {
    t = Math.min(emblem.duration, Math.max(0, tt));
    player.render(t);
    slider.value = t;
    tlabel.textContent = t.toFixed(2) + " s";
  }
  controls.querySelector(".replay").onclick = () => { t = 0; playing = true; };
  controls.querySelector(".pause").onclick = (e) => {
    playing = !playing; e.target.textContent = playing ? "Pause" : "Play";
  };
  slider.oninput = () => { playing = false; show(parseFloat(slider.value)); };

  function tick(now) {
    const dt = (now - last) / 1000; last = now;
    if (playing && t < emblem.duration)
      show(t + dt * parseFloat(speedSel.value));
    requestAnimationFrame(tick);
  }
  show(0);
  requestAnimationFrame(tick);

  players[emblem.name] = { seek: (tt) => { playing = false; show(tt); },
                           play: () => { playing = true; } };
}
window.__seek = (name, t) => players[name].seek(t);
window.__seekAll = (t) => Object.values(players).forEach(p => p.seek(t));
</script>
</body>
</html>
"""


def main() -> None:
    emblems = [build_strike(), build_goc(), build_chaos()]
    for emblem, src in zip(emblems, (STRIKE_JSON, GOC_JSON, CHAOS_JSON)):
        out = src.with_name(src.stem + ".intro-timeline.json")
        out.write_text(json.dumps(
            {"format": "emblem-intro-timeline", "version": 1,
             "source": src.name, "duration": emblem["duration"],
             "groups": emblem["groups"], "tweens": emblem["tweens"]},
            indent=1), encoding="utf-8")
        print(f"wrote {out}")
    html = HTML_TEMPLATE.replace("__DATA__", json.dumps(emblems))
    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"wrote {OUT_HTML} ({OUT_HTML.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
