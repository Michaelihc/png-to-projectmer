"""Augment the converted GOC / Strike-Team emblems for the cinematic intros.

Idempotent (re-runnable) additions:
  * strike-team.json: "PHYSICS Division - Assessment and Strike Teams"
    caption as per-letter BT8 TextToys under the logo (world text; the intro
    timeline pops it in character by character), plus a massive dark HDR
    backdrop wall (the skybox is untunable, so intro background color comes
    from an emissive wall behind the emblem).
  * global-occult-coalition.json: the same dark backdrop wall.

The Chaos emblem gets its walls from tools/build_chaos_emblem.py directly.

Usage: python tools/augment_intro_assets.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRIKE_JSON = ROOT / "converted_mer" / "strike-team" / "strike-team.json"
GOC_JSON = (ROOT / "converted_mer" / "global-occult-coalition"
            / "global-occult-coalition.json")

CAPTION = "PHYSICS Division - Assessment and Strike Teams"
CAPTION_COLOR = "#5F95E6"   # matches the emblem's outer-circle blue
CAPTION_Y = -4.85           # just under the outer circle (r ~ 4.07)
CAPTION_SPACING = 0.19      # per-character slot width
TMP_SIZE = 26

WALL_W, WALL_H = 60.0, 34.0
WALL_COLOR = "#05070CFF"    # near-black; HDR kept dim so the emblem pops
WALL_HDR = {"r": 0.02, "g": 0.025, "b": 0.045, "a": 1.0}


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

    # z=0.0 sits BEHIND every emblem layer (all content z is negative =
    # toward the viewer), so the wall never occludes the logo.
    blocks.insert(0, wall_block(wall_name, next_id, center, wall_scale))
    next_id += 1

    glyphs = 0
    if add_caption:
        half = (len(CAPTION) - 1) * CAPTION_SPACING / 2.0
        for ch_i, ch in enumerate(CAPTION):
            if ch == " ":
                continue
            x = -half + ch_i * CAPTION_SPACING + center[0]
            blocks.append({
                "Name": (f"strike-caption-{glyphs:02d}-"
                         f"{ch if ch.isalnum() else 'q'}"),
                "ObjectId": next_id, "ParentId": 0,
                "Position": {"x": round(x, 5), "y": CAPTION_Y, "z": -0.05},
                # upright, facing the viewer (same TextToy facing convention
                # as the GOC seal letters: world orientation = rot_z + 180)
                "Rotation": {"x": 0.0, "y": 0.0, "z": 180.0},
                "Scale": {"x": 0.08, "y": 0.08, "z": 0.08},
                "BlockType": 8,
                "Properties": {
                    "Text": (f"<align=center><size={TMP_SIZE}><b>"
                             f"<color={CAPTION_COLOR}>{ch}</color></b>"
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
