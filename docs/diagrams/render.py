"""
Regenerates the README diagrams: every .mmd file in this folder becomes a PNG
with the same name. Edit the .mmd, run this, then commit both.

Usage (needs internet access to load Mermaid):
    python docs/diagrams/render.py                     # all diagrams
    python docs/diagrams/render.py screening-run.mmd   # just one

Requires playwright and pillow (both come with crawl4ai) and a Chromium, Chrome
or Edge browser.

Why the report matters: GitHub shrinks an image to fit the README column, so a
diagram's on-screen text size depends on its layout width, not just its font
size. If a diagram is reported as too small, make it narrower (wrap labels,
merge boxes) instead of raising the font, which widens the layout with it.
"""
import argparse
import io
import sys
from pathlib import Path

from PIL import Image, ImageChops
from playwright.sync_api import Error as PlaywrightError, sync_playwright

HERE = Path(__file__).resolve().parent
MERMAID_URL = "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js"
COLUMN_WIDTH = 800  # display width used in the README's <img> tags
MIN_TEXT_PX = 16    # same as GitHub's body text
SCALE = 3           # PNG pixels per layout pixel, keeps text sharp when zoomed in

PAGE = (
    '<!doctype html><html><body style="margin:0;background:#fff">'
    '<div id="host" style="display:inline-block;padding:16px;background:#fff"></div></body></html>'
)

RENDER_JS = """
async (source) => {
  mermaid.initialize({startOnLoad: false});
  const {svg} = await mermaid.render('diagram', source);
  const host = document.getElementById('host');
  host.innerHTML = svg;

  // Mermaid sizes the SVG to its container; pin it to its natural layout size.
  const el = host.querySelector('svg');
  const box = el.viewBox.baseVal;
  el.removeAttribute('style');
  el.setAttribute('width', box.width);
  el.setAttribute('height', box.height);
  el.style.background = '#ffffff';

  // Mermaid paints sequence-diagram lifelines over the labels. Lift the labels
  // on top and give them a white outline so lines never cut through the text.
  el.querySelectorAll('text.messageText, text.loopText').forEach((label) => {
    el.appendChild(label);
    [label, ...label.querySelectorAll('tspan')].forEach((node) => {
      node.style.setProperty('paint-order', 'stroke', 'important');
      node.style.setProperty('stroke', '#ffffff', 'important');
      node.style.setProperty('stroke-width', '7px', 'important');
      node.style.setProperty('stroke-linejoin', 'round', 'important');
    });
  });

  // Only elements that directly hold text; containers report misleading sizes.
  const fontSizes = [...el.querySelectorAll('text, tspan, span, p, div')]
    .filter((node) => !node.children.length && node.textContent.trim())
    .map((node) => parseFloat(getComputedStyle(node).fontSize));
  return Math.min(...fontSizes);
}
"""


def launch_browser(playwright):
    """Playwright's bundled browser goes missing whenever the package is
    upgraded, so fall back to a browser that's already installed."""
    # LCD text smoothing adds coloured fringes to otherwise black text.
    args = ["--disable-lcd-text"]
    for options in ({}, {"channel": "chromium"}, {"channel": "chrome"}, {"channel": "msedge"}):
        try:
            return playwright.chromium.launch(args=args, **options)
        except PlaywrightError:
            continue
    sys.exit("No browser found. Run: playwright install chromium")


def colour_tint(image: Image.Image) -> int:
    """0 means every pixel is pure grey, i.e. nothing but black on white."""
    r, g, b = image.split()
    return max(
        ImageChops.difference(r, g).getextrema()[1],
        ImageChops.difference(g, b).getextrema()[1],
    )


def render(page, source_path: Path) -> None:
    page.set_content(PAGE)
    page.add_script_tag(url=MERMAID_URL)
    smallest_font = page.evaluate(RENDER_JS, source_path.read_text(encoding="utf-8"))

    shot = Image.open(io.BytesIO(page.locator("#host").screenshot())).convert("RGB")
    tint = colour_tint(shot)
    # Opaque greyscale: stays white in GitHub's dark mode, and keeps the file small.
    output_path = source_path.with_suffix(".png")
    shot.convert("L").save(output_path, optimize=True)

    layout_width = round(shot.width / SCALE)
    display_width = min(layout_width, COLUMN_WIDTH)
    shown_px = round(smallest_font * display_width / layout_width, 1)

    print(f'{output_path.name}: text shows at {shown_px}px on GitHub with width="{display_width}" in the README')
    if shown_px < MIN_TEXT_PX:
        print(f"  WARNING: under {MIN_TEXT_PX}px, make this diagram narrower ({layout_width}px wide now)")
    if tint:
        print(f"  WARNING: not pure black on white (colour tint {tint}), check for emoji or colour settings")


def main():
    parser = argparse.ArgumentParser(description="Render the .mmd diagrams in this folder to PNG.")
    parser.add_argument("diagrams", nargs="*", help="Diagram file names. Omit to render all of them.")
    args = parser.parse_args()

    sources = [HERE / Path(name).name for name in args.diagrams] or sorted(HERE.glob("*.mmd"))

    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 3000, "height": 2000}, device_scale_factor=SCALE)
        for source_path in sources:
            render(page, source_path)
        browser.close()


if __name__ == "__main__":
    main()
