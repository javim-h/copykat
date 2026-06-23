"""Braille-rendered pixel-art icons for the recorded prompt.

The cat icon marks a shell running under ``copykat record`` so the user can
tell at a glance which terminals are being recorded. Pixels are defined in a
plain C-array layout (rows of 0/1) and rendered to Unicode braille (2×4 dots
per codepoint) so the icon fits in one prompt line.
"""

from __future__ import annotations

__all__ = ["CAT_ICON", "_pixels_to_braille"]

# Cat pixel art icon (8×4) in C array format:
#   const unsigned char cat_icon[4][8] = {
#       {0,1,0,0,0,0,1,0},  // ears
#       {1,1,1,1,1,1,1,1},  // head top
#       {1,0,1,1,1,1,0,1},  // eyes
#       {0,1,1,1,1,1,1,0},  // chin
#   };
_CAT_PIXELS = [
    [0, 1, 0, 0, 0, 0, 1, 0],
    [1, 1, 1, 1, 1, 1, 1, 1],
    [1, 0, 1, 1, 1, 1, 0, 1],
    [0, 1, 1, 1, 1, 1, 1, 0],
]

# Braille dot offsets within a 2×4 cell: (row, col, bit_value).
# Each braille char (U+2800..U+28FF) packs 2×4 pixels.
_BRAILLE_DOTS = [
    (0, 0, 0x01), (1, 0, 0x02), (2, 0, 0x04), (3, 0, 0x40),
    (0, 1, 0x08), (1, 1, 0x10), (2, 1, 0x20), (3, 1, 0x80),
]


def _pixels_to_braille(pixels: list[list[int]]) -> str:
    """Render a 2D 0/1 grid as Unicode braille characters (2×4 dots per char)."""
    rows = len(pixels)
    cols = len(pixels[0]) if pixels else 0
    chars: list[str] = []
    for block_row in range(0, rows, 4):
        for block_col in range(0, cols, 2):
            val = 0
            for dr, dc, bit in _BRAILLE_DOTS:
                r, c = block_row + dr, block_col + dc
                if r < rows and c < cols and pixels[r][c]:
                    val |= bit
            chars.append(chr(0x2800 + val))
    return "".join(chars)


# Pre-rendered cat icon (braille), for use in prompt strings.
CAT_ICON = _pixels_to_braille(_CAT_PIXELS)
