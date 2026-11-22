"""Rasterize the actual three-page print preview for final visual inspection."""
import json
from pathlib import Path
import sys

folder = Path(sys.argv[1]).resolve()
if (folder / ".render-deps").is_dir():
    sys.path.insert(0, str(folder / ".render-deps"))
import pymupdf

document = pymupdf.open(folder / "source-figures-print-preview.pdf")
assert len(document) == 3, f"Expected 3 A4 pages, got {len(document)}"
pages = []
for index, page in enumerate(document):
    image = f"print-page-{index + 1}.png"
    page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5)).save(folder / image)
    pages.append({"page": index + 1, "box_pt": list(page.rect),
                  "text_chars": len(page.get_text()), "screenshot": image})
(folder / "print-checks.json").write_text(json.dumps({"page_count": len(document), "pages": pages}, indent=2) + "\n")
print(json.dumps(pages))
