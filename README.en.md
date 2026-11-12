# Image to PMer

**English** · [简体中文](README.md)

Turn an emblem image into a stock SCP:SL ProjectMER schematic: **split layers → tune each layer → preview the clean result → export combined or separate ZIPs**.

![Image to PMer's complete three-step browser workflow](docs/screenshots/workflow.png)

![A close view of the real source triangulation](docs/screenshots/triangulation.png)

The tool runs locally and uses no frontend framework. By default the output uses only vanilla ProjectMER empty objects and quad primitives (`BlockType 0` and `BlockType 1`), so it does not require a forked plugin; [native triangles](#native-triangles-scpsl-150) are opt-in.

## Start in 30 seconds

### Windows

1. Install [Python 3.10+](https://www.python.org/downloads/).
2. Download and extract the Windows release, or clone this repository.
3. Double-click `run-webapp.bat`.

The first launch creates an isolated `.venv`, installs the Python packages, and opens `http://127.0.0.1:8731/`. Later launches do not reinstall them.

Maintainers can rebuild the Windows release with `powershell -ExecutionPolicy Bypass -File package-release.ps1`.

### macOS / Linux

```bash
python3 -m pip install -r requirements.txt
python3 webapp/server.py
```

Then open `http://127.0.0.1:8731/`.

## Use it

1. **Split layers:** choose a PNG, JPG, WEBP, or BMP. Every detected visible colour becomes a normal region layer with its holes preserved; transparent pixels do not become a layer. Uncheck any colours you do not need.
2. **Tune each layer:** every detected colour gets its own detail slider, real triangulation preview, source-triangle count, actual export cost, and **Include in result** checkbox. Export cost shows both visible quad primitives and total ProjectMER objects. Lower values create cheaper geometry; higher values preserve more contour detail. Unchecking a layer excludes it from the combined result and exports without rebuilding geometry.
3. **Preview combined:** inspect the clean stacked ProjectMER result without construction triangles, plus totals for source triangles, quad primitives, and objects across all included layers. Changing any slider immediately hides stale output and shows **Loading…** until every preview is current.
4. **Export:** choose one combined schematic or a ZIP containing one independently loadable schematic folder per layer. Extract either into:

```text
LabAPI-beta/configs/ProjectMER/Schematics/
```

Exports are validated before writing and again before download. Invalid cached or generated schematics show a compatibility warning: **Accept** rebuilds from the source image, and **Ignore** cancels the export. After rebuilding, inspect the preview and export again. The command-line converters stop with a validation error rather than writing an invalid schematic.

Each layer can spend geometry where it matters. A detailed figure layer can stay near 95 while a smooth backing sits near 20. The tool merges source triangles into convex regions and covers them with vanilla parallelogram primitives, reducing the final runtime object count.

Everything stays on your computer. The server binds to `127.0.0.1` by default and does not upload images to a third party.

## Command line

Automatic layered conversion:

```bash
python tools/layered_emblem_to_mer.py nu22.png \
  --name nu22 \
  --output converted_mer \
  --layer-quality c0=20 \
  --layer-quality c1=70 \
  --layer-quality c2=95 \
  --preview \
  --layer-previews
```

Use a hand-tuned palette and layer order when you need exact art direction:

```bash
python tools/layered_emblem_to_mer.py nu22.png \
  --config examples/nu22.layers.json \
  --name nu22 \
  --output converted_mer \
  --preview
```

Single-colour silhouettes can use the lower-level converter:

```bash
python tools/png_to_mer_schematic.py scarletking.png \
  --name scarletking \
  --output converted_mer \
  --fill-mode ngon \
  --simplify 1.5 \
  --preview
```

Run either script with `--help` for all options.

For rectangle-strip fitting, add `--fill-strategy strips --strip-tolerance 0.25`
to the layered converter command. It fits overlapping rectangles along boundary
edges and fills the remaining regions, comparing several covers against the
ordinary n-gon export by total object count, including Empty parents. The
tolerance is measured in input-image pixels; larger values can roughen edges or
narrow small cutouts. This optional search takes longer and can increase visible
quad count even when it reduces total objects. Inspect both counts and the preview.
The default converter and browser workflow continue to use n-gon filling.

## Native triangles (SCP:SL 15.0)

SCP:SL 15.0 adds triangle primitives (`Triangle90` is a right isosceles triangle, `Triangle90Solid` its two-sided variant). They need a ProjectMER port that knows these types, so quads stay the default and the export is opt-in:

```bash
python tools/layered_emblem_to_mer.py nu22.png --name nu22 --quality 70   --native-triangles split --triangle-sides solid --preview
```

- `--native-triangles split` cuts every source triangle along its altitude into two right triangles, each one `Triangle90` with non-uniform scale and no helper empties (2 primitives, 2 objects per triangle; 1 if it already has a right angle).
- `--native-triangles shear` emits one `Triangle90` under a sheared Empty parent (1 primitive, 2 objects per triangle).
- `--triangle-sides one-sided` (default) matches the quads, which show only one face; `solid` writes the two-sided `Triangle90Solid`.

Native modes triangulate with the same earcut step as the quads and skip convex merging and the parallelogram cover. The converter always prints and stores both costs (`<name>.stats.json` for quads, `<name>.native-stats.json` for both triangle modes) so the browser UI shows them next to each other; its **Output primitives** selector picks what is exported.

Everything the tool assumes about the 15.0 meshes is in [`tools/mer_native_triangle_calibration.py`](tools/mer_native_triangle_calibration.py): the type names written to the schematic (as strings; set `WRITE_AS = "number"` and fill `NUMERIC_VALUES` if the port needs numbers), the unit mesh vertices and which side is visible. Correct that table after inspecting the real mesh; the geometry and tests follow it.

## How it works

```text
image
  → boundary-aware palette detection
  → smooth sub-pixel contours per colour layer
  → earcut triangulation
  → convex region merging
  → parallelogram cover
  → vanilla ProjectMER JSON
```

Stock ProjectMER has no triangle primitive. The default quad output represents non-parallelogram pieces with a transform-hierarchy shear: a rotated child quad under a non-uniformly scaled empty parent produces the required world-space shape. The n-gon path reduces runtime objects by merging adjacent triangles before emitting those quads.

The main modules are:

- `webapp/server.py` — local API, per-layer/combined previews, both ZIP exports, and static page server.
- `webapp/index.html` — the complete browser UI.
- `tools/trace_svg.py` — palette detection and smooth layered contours.
- `tools/layered_emblem_to_mer.py` — layer stack, per-layer quality mapping, clean previews, and schematic build.
- `tools/mer_ngon_decomposition.py` — triangulation, convex merging, and parallelogram emission.

## Layer configuration

[`examples/nu22.layers.json`](examples/nu22.layers.json) documents the config format. Each layer is:

```json
"green": ["#6D9A57", 2, 0.8, "region"]
```

The values are fill colour, back-to-front order, contour tolerance in pixels, and mode. `region` traces that colour naturally. `silhouette` fills the entire emblem and is useful as a cheap backing layer: detail can show through holes in front layers without requiring its own geometry.

## Development

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m py_compile webapp/server.py tools/*.py
```

Contributions and reproducible bug reports are welcome. Please include the source image or a minimal synthetic replacement, each layer's detail value, and the generated object counts.

## License

Source code is [MIT licensed](LICENSE). The bundled SCP-derived example emblems and their converted outputs are available under **CC BY-SA 3.0**; see [NOTICE.md](NOTICE.md). Images you convert keep their original licenses.
