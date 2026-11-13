"""Verify strip containment around holes and the complete exported cover."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

from shapely.geometry import Polygon
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import layered_emblem_to_mer as layered
import mer_ngon_decomposition as ngon
from mer_strip_cover import rectangle_candidates, rings_to_strip_blocks
from mer_validation import validate_schematic
from render_preview import collect_quads


class StripCoverTests(unittest.TestCase):
    def setUp(self):
        self.shape = Polygon([(4, 4), (68, 4), (68, 20), (38, 20), (38, 60), (4, 60)],
                             [[(12, 12), (20, 12), (20, 24), (12, 24)]])
        self.rings = layered.polys_to_unity([self.shape], 72, 72, 10)
        self.mask = layered.rasterize([self.shape], 72, 72)
        self.args = (self.rings, self.mask, 72, 72, 10, -0.04, "#FFFFFFFF", "strips")

    def test_rectangles_stay_out_of_holes_and_concavities(self):
        candidates = list(rectangle_candidates(self.shape, 0))
        self.assertTrue(candidates)
        self.assertTrue(all(self.shape.covers(p) for p in candidates))

    def test_complete_export_preserves_hole_and_does_not_increase_objects(self):
        baseline = ngon.rings_to_parallelogram_blocks(*self.args)
        blocks, next_id, stats, geometry = rings_to_strip_blocks(*self.args, tolerance_px=0)
        self.assertLessEqual(len(blocks), len(baseline[0]))
        self.assertEqual(next_id, max(b["ObjectId"] for b in blocks) + 1)
        self.assertEqual(stats.toys, len(blocks))
        self.assertEqual(geometry["source_triangles"], baseline[3]["source_triangles"])
        document = validate_schematic({"RootObjectId": 0, "Blocks": blocks})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "export.json"
            path.write_text(json.dumps(document))
            quads, _ = collect_quads(path)
        actual = unary_union([Polygon(q[2]) for q in quads])
        expected = Polygon(self.rings[0][0], self.rings[0][1:])
        self.assertLess(actual.symmetric_difference(expected).area / expected.area, 0.0001)

    def test_invalid_tolerance_is_rejected(self):
        for tolerance in (-1, float("nan"), float("inf")):
            with self.subTest(tolerance=tolerance), self.assertRaises(ValueError):
                rings_to_strip_blocks(*self.args, tolerance_px=tolerance)


if __name__ == "__main__":
    unittest.main()
