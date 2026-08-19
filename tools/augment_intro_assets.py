"""Augment the converted GOC / Strike-Team emblems for the cinematic intros.

Idempotent (re-runnable) additions:
  * strike-team.json: "PHYSICS Division - Assessment and Strike Teams"
    caption as per-letter BT8 TextToys under the logo (world text; the intro
    timeline pops it in character by character), plus a massive HDR backdrop
    wall (the skybox is untunable, so intro background color comes from an
    emissive wall behind the emblem).
  * global-occult-coalition.json: the same backdrop wall.
  * both: the intro palette recolor (see INTRO PALETTE below).

The Chaos emblem gets its walls from tools/build_chaos_emblem.py directly.

Usage: python tools/augment_intro_assets.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRIKE_JSON = ROOT / "converted_mer" / "strike-team" / "strike-team.json"
GOC_JSON = (ROOT / "converted_mer" / "global-occult-coalition"
            / "global-occult-coalition.json")

# ---------------------------------------------------------------------------
# INTRO PALETTE (2026-08-19, round 2)
#
# The intro reads as bright blue artwork on a WHITE backdrop instead of dark
# blue on near-black. The recolor lives HERE and not in goc_to_mer.py /
# strike_team_to_mer.py because those constants mirror the SOURCE artwork
# palette their classifiers match pixels against; this module is the
# intro-specific dressing pass, so a re-trace + re-augment still lands on the
# intro palette. Everything below is authored ABSOLUTELY (never scaled off the
# current value), so re-running is a no-op.
#
# Round 2 uses the two CANONICAL GOC artwork hues literally as the base hex.
# They are mid-tones, so the in-game brightness does NOT come from the hex: it
# comes from the emissive raw ColorRgba, which bake_intro_clips.py lifts past
# 1.0 for these two families (see its EMISSIVE_GAIN). The hex stays the SDR
# fallback the offline preview renders.
#
# On white, luminance ordering has to INVERT: everything that used to be black
# (the knock-out discs/bars that turn filled cylinders into rings) becomes the
# paper colour, and every glyph flips from pale blue to a readable blue ink.
PAPER = "#F2F5F8FF"        # backdrop wall + every knock-out disc / separator
# exactly what bake_intro_clips.apply_emissive_colors() derives from PAPER, so
# the authored wall and the derived knock-outs land on the same white
PAPER_HDR = {"r": 0.9758, "g": 0.9879, "b": 1.0, "a": 1.0}
STAR_BLUE = "#265192FF"    # canonical GOC pentagram hue (user-specified)
GLOBE_BLUE = "#5990DFFF"   # canonical GOC globe/world-map hue (user-specified)
TITLE_INK = "#3A78D6FF"    # motto ring + division caption (3.97:1 on PAPER)
SUB_INK = "#4A86D8FF"      # subtitle line, one step lighter (3.37:1 on PAPER)

# Traced source colours -> intro palette. Which of the two hues a blue block
# takes is decided by its NAME, not by its old colour, so the mapping holds no
# matter which generation of the palette the file is currently carrying.
#   * globe family: the world map, the seal's graticule (rings + meridians) and
#     the strike card's outer frame ring (its original light tier),
#   * star family: the pentagram and ALL strike-card linework (hexagon,
#     compass rays, six-point star, markers, centre ring).
KNOCKOUT_SOURCE = "#000000FF"
BLUE_SOURCES = ("#399AF9FF", "#21519BFF",   # goc traced light / dark
                "#5F95E6FF", "#22529BFF",   # strike traced light / dark
                "#22B0FFFF", "#0B7BE8FF")   # round-1 azure pair (legacy)
GLOBE_NAMES = ("map-tri", "laurel-tri", "goc-ring-", "goc-globe-meridian",
               "strike-outer-circle-outer")
# TMP <color=#RRGGBB> tags inside BT8 TextToys (the GOC motto ring).
TEXT_RECOLOR = {"#21519B": TITLE_INK[:7],   # traced motto ink
                "#0A3F91": TITLE_INK[:7]}   # round-1 ink (legacy)

# ---------------------------------------------------------------------------
# TEXT SIZING (2026-08-19, round 2): every glyph is authored 1.8x bigger.
#
# EmblemIntroClip spawns a TextToy at primitive.Scale * merScale * TextScale, so
# the block Scale is the asset-side lever and a uniform bump grows glyph + TMP
# display box together (no re-wrapping, DisplaySize untouched). The runtime
# TextScale is deliberately NOT compensated -- the asset is the single source.
TEXT_SCALE_FACTOR = 1.8
AUTHORED_GLYPH_SCALE = 0.08
GLYPH_SCALE = round(AUTHORED_GLYPH_SCALE * TEXT_SCALE_FACTOR, 5)

# The motto ring cannot simply scale with the glyphs (it would leave the seal),
# so its radius is pushed out just far enough to keep the arc's letter spacing
# clear of the bigger glyphs. Authored absolutely; every motto letter sits on
# one exact radius already.
MOTTO_RADIUS = 2.06

# Two-line caption lockup under the logo: a big bold division name and a
# lighter subtitle, both popped in character by character by the timeline.
# Line 1 holds its y; the per-char slots and the line gap take the full 1.8x so
# the bigger glyphs keep their original relative tracking and leading.
CAPTION_Y1 = -4.92
CAPTION_LINE_GAP = round(0.60 * TEXT_SCALE_FACTOR, 5)
CAPTION_LINES = [
    # (text, y, per-char slot, TMP size, color)
    ("PHYSICS DIVISION", CAPTION_Y1,
     round(0.46 * TEXT_SCALE_FACTOR, 5), 56, TITLE_INK[:7]),
    ("Assessment and Strike Teams", round(CAPTION_Y1 - CAPTION_LINE_GAP, 5),
     round(0.27 * TEXT_SCALE_FACTOR, 5), 32, SUB_INK[:7]),
]

WALL_W, WALL_H = 60.0, 34.0
WALL_COLOR = PAPER          # clean near-white paper behind the emblem
WALL_HDR = dict(PAPER_HDR)


def recolor(block: dict) -> None:
    """Map one traced block onto the intro palette (idempotent)."""
    props = block.get("Properties")
    if not props:
        return
    old = props.get("Color")
    if old == KNOCKOUT_SOURCE:
        props["Color"] = PAPER
    elif old in BLUE_SOURCES:
        globe = any(k in block["Name"] for k in GLOBE_NAMES)
        props["Color"] = GLOBE_BLUE if globe else STAR_BLUE
    text = props.get("Text")
    if text:
        props["Text"] = re.sub(
            r"<color=(#[0-9A-Fa-f]{6})>",
            lambda m: f"<color={TEXT_RECOLOR.get(m.group(1).upper(), m.group(1))}>",
            text)


def wall_block(name: str, object_id: int, center, scale: float) -> dict:
    return {
        "Name": name, "ObjectId": object_id, "ParentId": 0,
        "Position": {"x": center[0], "y": center[1], "z": 0.0},
        "Rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
        "Scale": {"x": WALL_W * scale, "y": WALL_H * scale, "z": 1.0},
        "BlockType": 1,
        "Properties": {"PrimitiveType": 5, "PrimitiveFlags": 2,
                       "Color": WALL_COLOR, "ColorRgba": dict(WALL_HDR),
                       "Static": True},
    }


def augment(path: Path, wall_name: str, center, wall_scale: float,
            add_caption: bool) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    blocks = [b for b in doc["Blocks"]
              if not b["Name"].startswith("strike-caption-")
              and b["Name"] != wall_name]
    next_id = max(b["ObjectId"] for b in blocks) + 1

    # Normalize arc motto letters to the in-game text convention: authored z is
    # the pure in-plane angle (upright letter = polar - 90, facing outward), and
    # pin them to MOTTO_RADIUS / GLYPH_SCALE. Every value is recomputed
    # absolutely (polar angle is preserved, radius and scale are set, never
    # multiplied), so re-running is safe.
    import math
    for b in blocks:
        if b.get("BlockType") != 8:
            continue
        dx = b["Position"]["x"] - center[0]
        dy = b["Position"]["y"] - center[1]
        if b["Name"].startswith("goc-text-"):
            # Re-place onto MOTTO_RADIUS along the letter's own polar angle. The
            # tolerance guard keeps this idempotent: rounding the new position to
            # 5 dp perturbs the angle slightly, so once a letter is on the radius
            # it is left alone rather than re-derived every run.
            if abs(math.hypot(dx, dy) - MOTTO_RADIUS) > 1e-4:
                theta = math.atan2(dy, dx)
                b["Position"]["x"] = round(center[0] + MOTTO_RADIUS * math.cos(theta), 5)
                b["Position"]["y"] = round(center[1] + MOTTO_RADIUS * math.sin(theta), 5)
                # re-read: the rotation below must match the FINAL position, or
                # the next run would settle it one rounding step further.
                dx = b["Position"]["x"] - center[0]
                dy = b["Position"]["y"] - center[1]
        polar = math.degrees(math.atan2(dy, dx))
        b["Rotation"]["z"] = round(polar - 90.0, 5)
        b["Scale"] = {"x": GLYPH_SCALE, "y": GLYPH_SCALE, "z": GLYPH_SCALE}

    # Intro palette: recompute absolutely from the traced colours, so this is
    # safe to re-run and survives a re-trace of the source artwork.
    for b in blocks:
        recolor(b)

    # z=0.0 sits BEHIND every emblem layer (all content z is negative =
    # toward the viewer), so the wall never occludes the logo.
    blocks.insert(0, wall_block(wall_name, next_id, center, wall_scale))
    next_id += 1

    glyphs = 0
    if add_caption:
        for text_line, y, spacing, tmp_size, color in CAPTION_LINES:
            half = (len(text_line) - 1) * spacing / 2.0
            for ch_i, ch in enumerate(text_line):
                if ch == " ":
                    continue
                x = -half + ch_i * spacing + center[0]
                blocks.append({
                    "Name": (f"strike-caption-{glyphs:02d}-"
                             f"{ch if ch.isalnum() else 'q'}"),
                    "ObjectId": next_id, "ParentId": 0,
                    "Position": {"x": round(x, 5), "y": y, "z": -0.05},
                    # authored z = pure in-plane angle (0 = upright); the
                    # runtime rig adds the readable-side 180Y flip
                    "Rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "Scale": {"x": GLYPH_SCALE, "y": GLYPH_SCALE,
                              "z": GLYPH_SCALE},
                    "BlockType": 8,
                    "Properties": {
                        "Text": (f"<align=center><size={tmp_size}><b>"
                                 f"<color={color}>{ch}</color></b>"
                                 f"</size></align>"),
                        "DisplaySize": {"x": 1.5, "y": 0.4},
                        "Static": True,
                    },
                })
                next_id += 1
                glyphs += 1

    doc["Blocks"] = blocks
    path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    print(f"wrote {path} (+wall{f', +{glyphs} caption glyphs' if glyphs else ''})")


def main() -> None:
    augment(STRIKE_JSON, "strike-bg-wall", (-0.0167, 0.0167), 1.0,
            add_caption=True)
    # GOC emblem is ~60% the strike emblem's size; scale its wall to match
    # so both fill the frame identically at the same cinematic framing.
    augment(GOC_JSON, "goc-bg-wall", (0.00145, 0.00374), 0.6,
            add_caption=False)


if __name__ == "__main__":
    main()
