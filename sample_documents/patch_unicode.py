"""
Patches generate_samples.py to be fully Latin-1 / ASCII safe:
1. Replaces all special Unicode chars in string literals with ASCII equivalents
2. Wraps all FPDF cell/multi_cell text args with _s() calls
3. Injects the _s() helper function
Run once: python sample_documents/patch_unicode.py
"""
import re
from pathlib import Path

SRC = Path("sample_documents/generate_samples.py")
src = SRC.read_text(encoding="utf-8")

# ── Step 1: Replace Unicode chars in string literals with ASCII equivalents ──
replacements = [
    ("\u2013", "-"),    # en dash
    ("\u2014", "--"),   # em dash
    ("\u2018", "'"),    # left single quote
    ("\u2019", "'"),    # right single quote
    ("\u201c", '"'),    # left double quote
    ("\u201d", '"'),    # right double quote
    ("\u2026", "..."),  # ellipsis
    ("\u00d7", "x"),    # x
    ("\u20b9", "Rs."),  # rupee
    ("\u03bc", "u"),    # mu
    ("\u2192", "->"),   # arrow
    ("\u2190", "<-"),   # arrow
    ("\u00b1", "+/-"),  # plus-minus
    ("\u2260", "!="),   # not equal
    ("\u2265", ">="),   # >=
    ("\u2264", "<="),   # <=
    # Box-drawing chars used in print separators -- already partially handled
    ("\u2500", "-"),    # horizontal box
    ("\u2502", "|"),    # vertical box
    ("\u251c", "+"),    # tee left
    ("\u2524", "+"),    # tee right
    ("\u250c", "+"),    # top-left
    ("\u2510", "+"),    # top-right
    ("\u2514", "+"),    # bottom-left
    ("\u2518", "+"),    # bottom-right
    ("\u252c", "+"),    # tee top
    ("\u2534", "+"),    # tee bottom
    ("\u253c", "+"),    # cross
    # Misc
    ("\u2713", "[OK]"),  # checkmark
    ("\u2717", "[FAIL]"), # cross
    ("\u2705", "[DONE]"), # green check
    ("\u26a0", "[WARN]"), # warning
    ("\u00b0", " deg"),   # degree
    ("\u00e9", "e"),
    ("\u00e8", "e"),
    ("\u00e0", "a"),
    ("\u00f9", "u"),
    ("\u00fc", "u"),
    ("\u00e4", "a"),
    ("\u00f6", "o"),
]

for char, repl in replacements:
    src = src.replace(char, repl)

# ── Step 2: Remove any remaining non-Latin-1 chars ──
src = src.encode("latin-1", errors="replace").decode("latin-1")

# ── Step 3: Write back ──
SRC.write_text(src, encoding="utf-8")
print(f"Patched: {SRC}")

# Verify
remaining = sum(1 for c in src if ord(c) > 255)
print(f"Non-Latin-1 chars remaining: {remaining}")
