import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "webapp"))

import layered_emblem_to_mer
import server
import trace_svg


class SmokeTests(unittest.TestCase):
    def test_ui_contains_no_css(self):
        html = (ROOT / "webapp" / "index.html").read_text("utf-8").lower()
        self.assertNotIn("<style", html)
        self.assertNotIn("style=", html)
        self.assertNotIn("stylesheet", html)

    def test_ui_has_the_three_step_workflow(self):
        html = (ROOT / "webapp" / "index.html").read_text("utf-8")
        self.assertIn("1. Choose image", html)
        self.assertIn("2. Preview combined schematic", html)
        self.assertIn("3. Export", html)
        self.assertIn('id="preview-loading"', html)
        self.assertIn('id="status" role="status" aria-live="polite" hidden', html)
        self.assertIn('id="download-combined"', html)
        self.assertIn('id="download-separated"', html)
        self.assertIn("changed_layer: changedLayer", html)
        self.assertIn("source_triangles", html)
        self.assertIn("quad_primitives", html)
        self.assertIn("layer_counts", html)
        self.assertIn('include.type = "checkbox"', html)
        self.assertIn("range.setPointerCapture", html)
        self.assertNotIn('range.addEventListener("change"', html)
        queue_start = html.index("async function runQueuedPreview()")
        queue_end = html.index("function loadFile(file)")
        self.assertNotIn("setControlsDisabled", html[queue_start:queue_end])

    def test_quality_is_monotonic(self):
        self.assertGreater(
            layered_emblem_to_mer.quality_scale(20),
            layered_emblem_to_mer.quality_scale(70),
        )
        self.assertGreater(
            layered_emblem_to_mer.quality_scale(70),
            layered_emblem_to_mer.quality_scale(100),
        )

    def test_named_layer_quality_overrides_global_quality(self):
        layers = {
            "back": ["#FFFFFF", 0, 1.0, "silhouette"],
            "front": ["#FF0000", 1, 1.0, "region"],
        }
        adjusted = layered_emblem_to_mer.layers_at_quality(
            layers, quality=20, layer_qualities={"front": 100}
        )
        self.assertGreater(adjusted["back"][2], adjusted["front"][2])

    def test_layer_quality_cli_parser(self):
        self.assertEqual(
            layered_emblem_to_mer.parse_layer_qualities(["back=20", "front=95"]),
            {"back": 20, "front": 95},
        )

    def test_auto_config_keeps_every_colour_as_a_region(self):
        image = np.full((40, 40, 3), 250, dtype=float)
        image[8:32, 8:16] = (20, 60, 130)
        image[8:32, 16:24] = (100, 160, 80)
        image[8:32, 24:32] = (210, 50, 60)
        config = trace_svg.auto_config(image, k=4)
        self.assertNotIn("background", config)
        self.assertEqual(len(config["layers"]), 4)
        self.assertTrue(all(layer[3] == "region" for layer in config["layers"].values()))

    def test_two_colour_art_keeps_both_colours(self):
        image = np.full((30, 30, 3), 255, dtype=float)
        image[8:22, 8:22] = (180, 20, 30)
        config = trace_svg.auto_config(image, k=4)
        self.assertEqual(len(config["layers"]), 2)

    def test_transparency_is_not_a_layer_but_opaque_black_is(self):
        image = np.zeros((30, 30, 4), dtype=np.uint8)
        image[5:25, 5:25] = (255, 255, 255, 255)
        image[10:20, 10:20] = (0, 0, 0, 255)
        config = trace_svg.auto_config(image, k=4)
        colours = set(config["centroids"].values())
        self.assertEqual(colours, {(0, 0, 0), (255, 255, 255)})
        self.assertTrue(all(layer[3] == "region" for layer in config["layers"].values()))

        layers = list(trace_svg.build_layers(
            image, config["centroids"], config.get("background"), config["layers"], blur=0.5
        ))
        polygons_by_fill = {fill: polygons for _name, fill, _order, polygons in layers}
        self.assertTrue(polygons_by_fill["#000000"])
        white_polygons = polygons_by_fill["#FFFFFF"]
        self.assertTrue(any(len(p.interiors) for p in white_polygons))

    def test_export_names_are_safe(self):
        self.assertEqual(server.clean_name("  my badge / 01  "), "my-badge-01")


if __name__ == "__main__":
    unittest.main()
