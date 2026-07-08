# Emblem → ProjectMER schematic

[简体中文](README.md) · **English**

Turn a high-contrast emblem image (PNG/JPG/WEBP) into a **SCP:SL ProjectMER**
schematic built entirely from **vanilla quad primitives**. The output uses only
`BlockType 0` (Empty) and `BlockType 1` (Primitive), so it loads on **stock
ProjectMER** — no modded/fork plugin required.

Triangles are represented as sheared quads using the TRS-hierarchy trick
(a rotated child under a non-uniformly scaled empty parent produces a sheared
world matrix), and the `ngon` fill mode merges triangles into convex pieces so
organic art needs far fewer objects.

## Quick start — web UI

Double-click **`run-webapp.bat`**. On first run it installs the Python
dependencies, then opens `http://127.0.0.1:8731/`. Once deps are installed you
can use **`serve.bat`** to skip the check and start immediately.

1. Drop in an emblem image.
2. Pick the fill colour, what to trace (light/dark shapes), fill mode, and the
   detail (simplify) slider. Advanced options are tucked in a panel.
3. **Convert** — you get a live preview and the runtime object count.
4. **Download** the `<name>.zip`, then unzip into
   `LabAPI-beta/configs/ProjectMER/Schematics/` so you have
   `<name>/<name>.json`.

The UI has a **中文 / English toggle** (defaults to Chinese). Requires Python
3.10+ on PATH (the `py` launcher or `python`).

## Quick start — command line

```bash
py -m pip install -r requirements.txt

py tools/png_to_mer_schematic.py scarletking.png \
    --name scarletking-opt --output converted_mer \
    --fill-mode ngon --simplify 1.5 --min-area 8 \
    --foreground light --threshold 128 --width 10 \
    --color "#D0021BFF" --preview
```

`--preview` also writes `<name>.preview.png` / `.svg` next to the JSON.
Run `py tools/png_to_mer_schematic.py --help` for every option.

## Multi-colour / layered emblems

`png_to_mer_schematic.py` traces **one** silhouette, so it's built for single-
colour art (linework shows as gaps). For an emblem with **stacked colours** —
e.g. white figures on a green disc on a blue border — use the layered tool
instead. It splits the image into one region per colour, traces each as a
**smooth sub-pixel contour** (no pixel-grid staircase), and stacks the layers
by Z into one vanilla schematic.

```bash
py tools/layered_emblem_to_mer.py nu22.png \
    --config examples/nu22.layers.json \
    --name nu22-opt --output converted_mer --preview
```

Omit `--config` to auto-detect a palette and stack with k-means (quick, but a
hand-tuned config is smaller and cleaner). The bundled **`nu22`** MTF badge is a
worked example: 3 colours → **1,326** vanilla primitives, gap-free.

### Writing a layer config

See [`examples/nu22.layers.json`](examples/nu22.layers.json). Each layer is
`[fill "#RRGGBB", z_order, simplify_px, mode]`:

| Field | Meaning |
| --- | --- |
| `centroids` | RGB of each palette colour (measured from the source). |
| `background` | Which centroid is empty (not emitted). |
| `z_order` | `0` = back; higher = drawn in front. |
| `mode "region"` | Trace this colour's own area, holes preserved. Use for solid fills. |
| `mode "silhouette"` | Fill the **whole emblem** solid — no holes. |
| `layer_z` | Optional exact Z per layer (more negative = front-facing). |

**The primitive-saving trick:** put the colour that is mostly thin detail
(figures, stars, linework) as a **`silhouette` backing at `z_order` 0**, then
draw the big solid colours on top as `region` layers. The detail then appears
wherever the top layers have holes/gaps — at **zero geometry cost** — and no
seam can reveal the background, because there's always backing behind it. In
`nu22` this collapsed the white layer from ~250 traced vertices to a single
41-vertex blob.

### Verify before you build

`check_trace.py` rasterizes the layer stack (antialiased) and reports seam
coverage and colour agreement, so you can tune the config before generating the
schematic:

```bash
py tools/check_trace.py nu22.png examples/nu22.layers.json
# -> seams 0.09%, colour agreement 96.3%
```

### Key options

| Option | Meaning |
| --- | --- |
| `--fill-mode {triangle,ngon}` | `ngon` merges triangles into convex pieces — fewer objects on fills. |
| `--simplify PX` | Contour tolerance. Lower = more faithful & more objects; higher = smoother & cheaper. |
| `--foreground {light,dark}` | Trace the bright pixels or the dark pixels. |
| `--threshold 0-255` | Foreground/background cutoff. |
| `--color #RRGGBB[AA]` | Flat emblem colour. Linework stays as gaps (the wall shows through). |
| `--color-source {flat,image}` | `image` samples each primitive's colour from the source, preserving the original colours in one pass (web UI: "Keep original colours"). |
| `--min-area PX` | Ignore contours smaller than this (drop lower to keep fine detail). |
| `--width UNITS` | Final schematic width in Unity units. |
| `--border-cylinders` | Convert a detected circular border into 2 cheap cylinders. |
| `--trace-mode rectangle-first` | Stroke-based tracing (`--trace-source centerline` for skeletons). |

## Toolchain

Core pipeline in `tools/`:

- **`png_to_mer_schematic.py`** — CLI entry point: image → contours → triangles
  (or n-gon pieces) → vanilla quad-primitive schematic JSON, with SVG/PNG preview.
- **`mer_triangle_primitives.py`** — geometry: expands each triangle into
  standard MER quads via TRS-hierarchy shear (medial parallelograms, with a
  rectangle-tile fast path).
- **`mer_ngon_decomposition.py`** — merges earcut triangles into convex polygons
  and covers them with the fewest parallelograms (2D port of TriangleScpSl's
  NGonDecomposition).

Layered multi-colour front-end:

- **`layered_emblem_to_mer.py`** — CLI: split by colour → smooth per-layer trace
  → stack into one schematic. Config-driven (`examples/*.layers.json`).
- **`trace_svg.py`** — the smooth tracer: k-means palette, sub-pixel
  marching-squares contours, hole-nesting rebuild → layered SVG.
- **`check_trace.py`** — QA the layer stack (seam %, colour agreement) before building.

`webapp/` holds the local UI (`server.py`, standard-library only, + `index.html`).
`tools/circular_crop_tool.html` is a standalone helper for pre-cropping circular
logos. Converted schematics live in `converted_mer/<name>/`.

## Licensing

- **Source code:** MIT — see [LICENSE](LICENSE).
- **Example emblems** (`scarletking.png`, `nu22.png`, and their converted
  output): from the SCP Foundation, licensed **CC BY-SA 3.0**. See
  [NOTICE.md](NOTICE.md). Artwork you convert yourself stays under its own license.

> The fork-only `BlockType 11` triangle path and the one-off logo drivers were
> removed — this repo now targets vanilla ProjectMER exclusively.
