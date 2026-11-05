"""Regression checks for panel-edge components.

A rounded panel's antialiased rim survives the residual mask as a thin ring
whose bounding box equals the panel.  It must not become an icon/photo crop:
that crop would cover the panel and cause api.py to drop the panel's OCR text.
A solid avatar/photo can share a panel's box, so the ring test must keep it.
"""

from __future__ import annotations

import unittest

import numpy as np

from distill_svg.images import _is_panel_edge_component
from distill_svg.panels import Panel

SCALE = 2


def _panel(x: float = 10.0) -> Panel:
    return Panel(x=x, y=10.0, w=100.0, h=60.0, rx=8.0, fill="#94ec68")


def _cc() -> np.ndarray:
    # Panel box in analysis pixels: x=5, y=5, w=50, h=30.
    return np.zeros((60, 80), dtype=np.int32)


class PanelEdgeComponentTest(unittest.TestCase):
    def test_border_ring_matching_panel_is_skipped(self):
        cc = _cc()
        cc[5:35, 5:55] = 1
        cc[7:33, 7:53] = 0  # hollow out the panel fill, leaving a 2px rim
        self.assertTrue(
            _is_panel_edge_component(cc, 1, 5, 5, 50, 30, SCALE, [_panel()])
        )

    def test_solid_content_sharing_panel_box_is_kept(self):
        cc = _cc()
        cc[5:35, 5:55] = 1  # flat blue avatar / photo detected as a panel
        self.assertFalse(
            _is_panel_edge_component(cc, 1, 5, 5, 50, 30, SCALE, [_panel()])
        )

    def test_small_icon_inside_panel_is_kept(self):
        cc = _cc()
        cc[15:23, 15:23] = 1  # 8x8 icon, not panel-sized
        self.assertFalse(
            _is_panel_edge_component(cc, 1, 15, 15, 8, 8, SCALE, [_panel()])
        )

    def test_ring_not_matching_panel_is_kept(self):
        cc = _cc()
        cc[5:35, 5:55] = 1
        cc[7:33, 7:53] = 0
        self.assertFalse(
            _is_panel_edge_component(cc, 1, 5, 5, 50, 30, SCALE, [_panel(x=30.0)])
        )


if __name__ == "__main__":
    unittest.main()
