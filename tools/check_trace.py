"""QA a layered trace: rasterize the traced layers (antialiased, back-to-front
with holes revealing lower layers) and report seam coverage + colour agreement
against the source. Renders the SAME geometry trace_svg / layered_emblem_to_mer
emit, so it verifies the layer stack before you build the schematic.

Usage: python tools/check_trace.py IMAGE [CONFIG.json] [out_preview.png]
"""
import importlib.util
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import binary_fill_holes

spec = importlib.util.spec_from_file_location("trace_svg", Path(__file__).with_name("trace_svg.py"))
ts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ts)

ROOT = Path(__file__).resolve().parent.parent


def render(src_path, config_path, ss=3):
    im = np.array(Image.open(src_path).convert("RGB")).astype(float)
    H, W = im.shape[:2]
    centroids, background, layers = ts.load_config(im, config_path)
    blur = 1.2
    base = Image.new("RGB", (W * ss, H * ss), (0, 0, 0))
    for name, fill, _z, polys in ts.build_layers(im, centroids, background, layers, blur):
        col = tuple(int(fill[i:i+2], 16) for i in (1, 3, 5))
        # Composite each layer OVER the base with holes transparent (evenodd),
        # so a hole reveals whatever lower layer is behind it.
        lmask = Image.new("L", (W * ss, H * ss), 0)
        md = ImageDraw.Draw(lmask)
        for p in polys:
            geoms = p.geoms if p.geom_type == "MultiPolygon" else [p]
            for g in geoms:
                md.polygon([(x*ss, y*ss) for x, y in g.exterior.coords], fill=255)
                for interior in g.interiors:
                    md.polygon([(x*ss, y*ss) for x, y in interior.coords], fill=0)
        base.paste(col, (0, 0), lmask)
    return base.resize((W, H), Image.LANCZOS), centroids, background


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "nu22.png"
    config = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2].endswith(".json") else None
    out = sys.argv[3] if len(sys.argv) > 3 else str(src.with_suffix("")) + ".trace.png"
    ren, centroids, background = render(src, config)
    ren.save(out)
    a = np.array(ren)
    src_im = np.array(Image.open(src).convert("RGB")).astype(float)
    lab, names = ts.classify(src_im, centroids)
    bg = names.index(background)
    fg = lab != bg
    black_ren = a.sum(2) < 24
    seam = black_ren & fg
    print(f"wrote {out}")
    print(f"emblem interior px: {int(fg.sum())}")
    print(f"background gap px inside emblem (seams): {int(seam.sum())} "
          f"({100*seam.sum()/fg.sum():.2f}%)")
    lab_ren, _ = ts.classify(a.astype(float), centroids)
    print(f"colour agreement in interior: {100*((lab_ren==lab)&fg).sum()/fg.sum():.1f}%")


if __name__ == "__main__":
    main()
