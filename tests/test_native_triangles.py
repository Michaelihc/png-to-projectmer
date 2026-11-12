import copy
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import mer_native_triangle_calibration as calibration
import mer_native_triangles as native
from mer_validation import ExportValidationError, validate_schematic

TOLERANCE = 2e-4


def random_triangle(rng, spread=8.0):
    while True:
        tri = tuple((rng.uniform(-spread, spread), rng.uniform(-spread, spread)) for _ in range(3))
        a, b, c = tri
        area = 0.5 * abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
        if area > 0.05:
            return tri


def area(tri):
    a, b, c = tri
    return 0.5 * abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))


def barycentric(p, tri):
    (ax, ay), (bx, by), (cx, cy) = tri
    d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    u = ((by - cy) * (p[0] - cx) + (cx - bx) * (p[1] - cy)) / d
    v = ((cy - ay) * (p[0] - cx) + (ax - cx) * (p[1] - cy)) / d
    return u, v, 1 - u - v


def emit(tri, mode, solid, z=-0.06):
    stats = native.NativeTriangleStats()
    blocks, _ = native.triangle_to_blocks("t", 1, 0, z, "#FF0000FF", tri, mode, solid, stats)
    return blocks, stats


def world_triangles(blocks):
    return [verts[:, :2] for _block, verts in native.native_triangle_world_vertices(blocks)], \
           [verts for _block, verts in native.native_triangle_world_vertices(blocks)]


def same_vertex_set(found, expected, tol=TOLERANCE):
    remaining = [np.array(p, float) for p in expected]
    for vertex in found:
        distances = [np.linalg.norm(vertex - p) for p in remaining]
        index = int(np.argmin(distances))
        if distances[index] > tol:
            return False
        del remaining[index]
    return not remaining


def facing_ok(verts3, spec):
    """Visible face must look toward world -Z, like the quads."""
    normal_z = np.cross(verts3[1] - verts3[0], verts3[2] - verts3[0])[2]
    if spec.visible_local_z == -1:
        return normal_z > 0
    if spec.visible_local_z == 1:
        return normal_z < 0
    return True


class NativeTriangleGeometryTests(unittest.TestCase):
    def check_mode(self, mode, solid, count=300, seed=7):
        rng = random.Random(seed)
        spec = calibration.right_triangle_type(solid)
        for _ in range(count):
            tri = random_triangle(rng)
            if rng.random() < 0.5:  # exercise both windings
                tri = (tri[0], tri[2], tri[1])
            blocks, stats = emit(tri, mode, solid)
            polys2d, polys3 = world_triangles(blocks)
            self.assertEqual(len(polys2d), stats.visible)
            for verts3 in polys3:
                self.assertTrue(facing_ok(verts3, spec), (tri, mode))
                self.assertAlmostEqual(verts3[0][2], -0.06, delta=TOLERANCE)
            if mode == "shear":
                self.assertEqual(len(polys2d), 1)
                self.assertTrue(same_vertex_set(polys2d[0], tri), (tri, polys2d))
                continue
            # split: halves lie inside the source, fill its area and tile it exactly.
            self.assertAlmostEqual(sum(area(p) for p in polys2d), area(tri), delta=TOLERANCE * 10)
            for poly in polys2d:
                for vertex in poly:
                    self.assertTrue(all(b > -TOLERANCE for b in barycentric(vertex, tri)))
            for _ in range(20):
                u, v = rng.random(), rng.random()
                if u + v > 1:
                    u, v = 1 - u, 1 - v
                point = (tri[0][0] + u * (tri[1][0] - tri[0][0]) + v * (tri[2][0] - tri[0][0]),
                         tri[0][1] + u * (tri[1][1] - tri[0][1]) + v * (tri[2][1] - tri[0][1]))
                hits = sum(all(b > 1e-6 for b in barycentric(point, tuple(map(tuple, poly))))
                           for poly in polys2d)
                near_seam = any(
                    min(barycentric(point, tuple(map(tuple, poly)))) < 1e-4 for poly in polys2d)
                if not near_seam:
                    self.assertEqual(hits, 1, (tri, point))  # no gap, no overlap

    def test_split_one_sided_reproduces_source(self):
        self.check_mode("split", solid=False)

    def test_split_solid_reproduces_source(self):
        self.check_mode("split", solid=True)

    def test_shear_one_sided_reproduces_source(self):
        self.check_mode("shear", solid=False)

    def test_shear_solid_reproduces_source(self):
        self.check_mode("shear", solid=True)

    def test_right_triangles_need_one_object(self):
        tri = ((1.0, 1.0), (4.0, 1.0), (1.0, 3.0))
        for mode in native.MODES:
            blocks, stats = emit(tri, mode, solid=False)
            self.assertEqual([b["BlockType"] for b in blocks], [1])
            self.assertEqual((stats.visible, stats.objects), (1, 1))
            polys2d, _ = world_triangles(blocks)
            self.assertTrue(same_vertex_set(polys2d[0], tri))

    def test_costs_and_plan_counts_match_emission(self):
        rng = random.Random(11)
        for _ in range(200):
            tri = random_triangle(rng)
            for mode, expect in (("split", (2, 2)), ("shear", (1, 2))):
                blocks, stats = emit(tri, mode, solid=False)
                self.assertEqual((stats.visible, stats.objects), expect)
                self.assertEqual(native.count_triangle(tri, mode), expect)
                self.assertEqual(sum(b["BlockType"] == 1 for b in blocks), stats.visible)
                self.assertEqual(len(blocks), stats.objects)

    def test_blocks_are_schematic_valid(self):
        rng = random.Random(3)
        blocks = []
        next_id = 1
        for _ in range(40):
            new, next_id = native.triangle_to_blocks(
                "t", next_id, 0, -0.06, "#00FF00FF", random_triangle(rng),
                rng.choice(native.MODES), bool(rng.getrandbits(1)))
            blocks.extend(new)
        validate_schematic({"RootObjectId": 0, "Blocks": blocks})
        names = {b["Properties"]["PrimitiveType"] for b in blocks if b["BlockType"] == 1}
        self.assertLessEqual(names, {"Triangle90", "Triangle90Solid"})

    def test_calibration_table_alone_drives_the_geometry(self):
        """A different measured mesh (other pivot, leg lengths, handedness and
        visible side) must only require editing the table."""
        original = calibration.TYPES.copy()
        try:
            calibration.TYPES["Triangle90"] = calibration.NativeTriangleType(
                "Triangle90",
                ((0.5, 0.5, 0.0), (-1.5, 0.5, 0.0), (0.5, -0.5, 0.0)),  # legs 2 along -X, 1 along -Y
                +1, 0)
            calibration.TYPES["Triangle90Solid"] = calibration.NativeTriangleType(
                "Triangle90Solid",
                ((0.0, 0.0, 0.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0)),  # legs listed Y-first
                0, 0)
            rng = random.Random(5)
            for solid in (False, True):
                spec = calibration.right_triangle_type(solid)
                for mode in native.MODES:
                    for _ in range(100):
                        tri = random_triangle(rng)
                        blocks, _ = emit(tri, mode, solid)
                        polys2d, polys3 = world_triangles(blocks)
                        for verts3 in polys3:
                            self.assertTrue(facing_ok(verts3, spec))
                        if mode == "shear":
                            self.assertTrue(same_vertex_set(polys2d[0], tri))
                        else:
                            self.assertAlmostEqual(sum(area(p) for p in polys2d), area(tri),
                                                   delta=TOLERANCE * 10)
        finally:
            calibration.TYPES.clear()
            calibration.TYPES.update(original)

    def test_numeric_write_mode(self):
        old_write, old_numbers = calibration.WRITE_AS, dict(calibration.NUMERIC_VALUES)
        try:
            calibration.WRITE_AS = "number"
            calibration.NUMERIC_VALUES["Triangle90"] = 9
            blocks, _ = emit(((0, 0), (3, 0), (1, 2)), "split", solid=False)
            self.assertTrue(all(b["Properties"]["PrimitiveType"] == 9
                                for b in blocks if b["BlockType"] == 1))
            validate_schematic({"RootObjectId": 0, "Blocks": blocks})
            polys2d, _ = world_triangles(blocks)
            self.assertEqual(len(polys2d), 2)
            calibration.WRITE_AS = "number"
            with self.assertRaises(ValueError):
                emit(((0, 0), (3, 0), (1, 2)), "split", solid=True)  # no number calibrated
        finally:
            calibration.WRITE_AS = old_write
            calibration.NUMERIC_VALUES.clear()
            calibration.NUMERIC_VALUES.update(old_numbers)

    def test_validation_rejects_unknown_primitive_names(self):
        blocks, _ = emit(((0, 0), (3, 0), (1, 2)), "split", solid=False)
        bad = copy.deepcopy(blocks)
        bad[0]["Properties"]["PrimitiveType"] = "Hexagon"
        with self.assertRaises(ExportValidationError):
            validate_schematic({"RootObjectId": 0, "Blocks": bad})


class NativeTriangleConversionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import layered_emblem_to_mer

        cls.convert_module = layered_emblem_to_mer
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        image = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse((10, 10, 150, 150), fill=(30, 70, 140, 255))
        draw.polygon([(40, 110), (80, 30), (122, 112)], fill=(240, 240, 240, 255))
        cls.image_path = root / "emblem.png"
        image.save(cls.image_path)
        cls.root = root

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def convert(self, name, **options):
        out = self.root / name
        path = self.convert_module.convert(self.image_path, None, name, out, False, 70, **options)
        stats = json.loads((out / f"{name}.stats.json").read_text())
        native_stats = json.loads((out / f"{name}.native-stats.json").read_text())
        return json.loads(path.read_text()), stats, native_stats

    def test_default_output_is_quads_and_stats_are_mode_independent(self):
        quad, quad_stats, quad_native = self.convert("quad")
        self.assertTrue(all(b["Properties"].get("PrimitiveType") == 5
                            for b in quad["Blocks"] if b["BlockType"] == 1))
        tri, tri_stats, tri_native = self.convert("tri", native_triangles="split")
        self.assertEqual(quad_stats, tri_stats)          # quad cost is reported in every mode
        self.assertEqual(quad_native, tri_native)        # so is triangle cost
        explicit, _, _ = self.convert("explicit", native_triangles=None)
        self.assertEqual(json.dumps(quad).replace("quad-", "x-"),
                         json.dumps(explicit).replace("explicit-", "x-"))

    def test_native_counts_match_emitted_blocks(self):
        for mode in native.MODES:
            for sides, name in (("one-sided", "Triangle90"), ("solid", "Triangle90Solid")):
                document, _, native_stats = self.convert(
                    f"{mode}-{sides}", native_triangles=mode, triangle_sides=sides)
                validate_schematic(document)
                blocks = document["Blocks"]
                visible = [b for b in blocks if b["BlockType"] == 1]
                self.assertEqual({b["Properties"]["PrimitiveType"] for b in visible}, {name})
                expected = native_stats["layers"]
                self.assertEqual(len(visible), sum(l[mode]["primitives"] for l in expected.values()))
                self.assertEqual(len(blocks), sum(l[mode]["objects"] for l in expected.values()))

    def test_native_triangles_tile_the_source_area(self):
        areas = {}
        for mode in native.MODES:
            document, _, _ = self.convert(f"area-{mode}", native_triangles=mode)
            areas[mode] = sum(
                area(tuple(map(tuple, verts[:, :2])))
                for _b, verts in native.native_triangle_world_vertices(document["Blocks"]))
        self.assertAlmostEqual(areas["split"], areas["shear"], delta=1e-3)


if __name__ == "__main__":
    unittest.main()
