# Textbook figure exercises: exact static SVGs

`tools/build_source_practice_svgs.py` draws three multiframe SVGs using only Python's standard library. They contain vector paths, text and endpoint circles, with no raster image. `source-definitions.json` freezes the three source stems and the original figure hashes that were visually checked.

The actual source IDs are `TB-FIG-CH08-Q12`, `TB-FIG-CH09-Q3`, and `TB-FIG-CH09-Q5`. They were not in the accepted catalog when these candidates were generated. Figure review remains pending independently of question review. This directory does not authorize publication or update the media manifest.

Run `python3 tools/build_source_practice_svgs.py --out OUT --sources SOURCES_JSON --catalog CATALOG_JSON` from the repository. The builder checks the frozen source stem, writes `fig-8-26.svg`, `fig-9-8.svg`, `fig-9-9.svg`, `bindings.json`, `math-checks.json` and `preview.html`. Omit `--sources` and `--catalog` only to reproduce unbound candidate diagrams from the frozen definitions.

Run `node examples/practice_source_svgs/check-render.cjs OUT` for actual Chromium screenshots, vector/text bounds checks and a three-page A4 print preview. On this Mac it uses the installed Edge browser; otherwise it uses Playwright Chromium. It waits for fonts and two paint frames before capture. The initial immediate screenshots showed stale compositor tiles; the paint wait eliminates that artifact.

Run `python3 examples/practice_source_svgs/render-print.py OUT` with PyMuPDF available to inspect the actual PDF as three page PNGs. On this task host, a local PyMuPDF dependency was installed under `OUT/.render-deps` because the system Poppler binary has a missing library. That hidden dependency folder is not a media asset and should not be packaged.

The mathematical check compares emitted SVG coordinates and endpoint fills to the diagram data, tests 4,042 interior/exterior domain points and 14 excluded boundary samples, and checks 726 signal samples plus 50 endpoint conventions against the definitions. This is a local implementation check, not independent mathematical acceptance.

Figure conventions:

- Domain boundaries are dashed and excluded. Their arrows mean infinite extension, not contour orientation. The open channels have no closing boundary at the plot edge. The displayed lengths are illustrative, with only the stated inequalities imposed.
- For the delayed unit-slope ramp, the ordinate is `f₁/E` with ticks `T/E` and `2T/E`; the drawing does not silently set `T=E`.
- Rectangular-pulse breakpoints are labelled `a=t₁/T`, `b=(t₁+τ₁)/T`, `c=t₂/T`, and `d=(t₂+τ₂)/T`. Their horizontal spacing is illustrative, and the displayed condition includes `a=0`.
- Figure 9.8(4) says explicitly “按本题补充条件绘制” and identifies `3E/4`, `E/4` as public added values. These are not claimed to be original labels.
- The half-wave rectifier retains its actual amplitude `A` and period `P=2π/ω`, so its axes are `f₂/A` and `t/P`. It does not assume `A=E` or `P=T`.
- The sinusoidal burst has `ρ=τ/T`, with `0<ρ<1`; the diagram explicitly states that `ρ=2/3` is the display example. Every periodic panel shows two full periods and the beginning of a third.

Once the questions are accepted, rerun the builder against the current catalog. It binds `catalog_content_sha256` only when the exact stem matches, the accepted review matches `content_digest`, and `validate_catalog` passes. A changed stem stays unbound and requires renewed figure inspection. An independent figure reviewer must still approve the SVG before the main process adds `kind: svg` media attachments.
