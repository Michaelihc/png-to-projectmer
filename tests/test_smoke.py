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
        self.assertIn("1. Split layers", html)
        self.assertIn("2. Triangulate", html)
        self.assertIn("3. Export", html)

    def test_quality_is_monotonic(self):
        self.assertGreater(
            layered_emblem_to_mer.quality_scale(20),
            layered_emblem_to_mer.quality_scale(70),
        )
        self.assertGreater(
            layered_emblem_to_mer.quality_scale(70),
            layered_emblem_to_mer.quality_scale(100),
        )

    def test_background_comes_from_the_boundary(self):
        image = np.full((40, 40, 3), 250, dtype=float)
        image[8:32, 8:16] = (20, 60, 130)
        image[8:32, 16:24] = (100, 160, 80)
        image[8:32, 24:32] = (210, 50, 60)
        config = trace_svg.auto_config(image, k=4)
        background = config["centroids"][config["background"]]
        self.assertGreater(sum(background), 700)

    def test_two_colour_art_creates_one_foreground_layer(self):
        image = np.full((30, 30, 3), 255, dtype=float)
        image[8:22, 8:22] = (180, 20, 30)
        config = trace_svg.auto_config(image, k=4)
        self.assertEqual(len(config["layers"]), 1)

    def test_export_names_are_safe(self):
        self.assertEqual(server.clean_name("  my badge / 01  "), "my-badge-01")


if __name__ == "__main__":
    unittest.main()
