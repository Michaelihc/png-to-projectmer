"""Check exact coverage where overlap-based optimization can hide small gaps."""
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import mer_ngon_decomposition as ngon
from mer_validation import validate_schematic
from render_preview import collect_quads
from trace_svg import layer_polys, nearest_palette


class GeometryCoverageTests(unittest.TestCase):
    def test_symmetric_cover_preserves_exported_area(self):
        for shear in (0, 0.4):
            with self.subTest(shear=shear):
                points = [(2 * math.cos(i * math.tau / 128)
                           + shear * math.sin(i * math.tau / 128),
                           2 * math.sin(i * math.tau / 128)) for i in range(128)]
                stats = ngon.NGonStats()
                emitter = ngon._Emitter("disc", 1, 0, 0, "#FFFFFFFF", stats)
                self.assertTrue(ngon._try_symmetric_cover(points, emitter))
                document = validate_schematic({"RootObjectId": 0, "Blocks": emitter.blocks})
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "disc.json"
                    path.write_text(json.dumps(document))
                    quads, _ = collect_quads(path)
                actual = unary_union([Polygon(q[2]) for q in quads])
                expected = Polygon(points)
                self.assertLess(actual.symmetric_difference(expected).area / expected.area, 0.0001)
                self.assertEqual(len(quads), 64)
                self.assertEqual(len(emitter.blocks), 64 if shear == 0 else 128)

    def test_asymmetric_polygon_is_not_approximated_as_symmetric(self):
        points = [(math.cos(i * math.tau / 8), math.sin(i * math.tau / 8)) for i in range(8)]
        points[0] = (1.01, 0)
        emitter = ngon._Emitter("shape", 1, 0, 0, "#FFFFFFFF", ngon.NGonStats())
        self.assertFalse(ngon._try_symmetric_cover(points, emitter))
        self.assertEqual(emitter.blocks, [])

    def test_border_touching_mask_has_closed_outline_and_hole(self):
        mask = np.ones((24, 24), dtype=bool)
        mask[8:16, 8:16] = False
        polygons = layer_polys(mask, 0, 0)
        self.assertEqual(len(polygons), 1)
        self.assertEqual(len(polygons[0].interiors), 1)
        self.assertEqual(polygons[0].bounds, (-0.5, -0.5, 23.5, 23.5))
        self.assertAlmostEqual(polygons[0].area, 24 * 24 - 8 * 8)

    def test_palette_batches_preserve_distances_and_tie_order(self):
        pixels = np.random.default_rng(0).integers(0, 256, (65539, 3)).astype(float)
        centers = np.array([[0, 0, 0], [0, 0, 0], [255, 255, 255]], dtype=float)
        expected = ((pixels[:, None] - centers[None]) ** 2).sum(2)
        labels, distances = nearest_palette(pixels, centers)
        np.testing.assert_array_equal(labels, expected.argmin(1))
        np.testing.assert_array_equal(distances, expected.min(1))


if __name__ == "__main__":
    unittest.main()
