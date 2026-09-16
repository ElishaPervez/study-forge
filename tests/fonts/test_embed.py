from pathlib import Path

from backend.fonts.embed import FontSpec, font_face_css, inject_fonts

FONTS = Path("assets/fonts")
SPECS = [
    FontSpec("Instrument Serif", 400, "instrument-serif-400.woff2"),
    FontSpec("Geist", 600, "geist-600.woff2"),
    FontSpec("Geist Mono", 400, "geist-mono-400.woff2"),
]


def test_css_embeds_each_face_as_a_font_data_url() -> None:
    css = font_face_css(FONTS, SPECS)

    assert css.count("@font-face") == 3
    assert "data:font/woff2;base64," in css
    assert "fonts.googleapis" not in css


def test_injected_html_never_uses_a_data_url_on_a_tag() -> None:
    html = "<html><head><style>body{color:#2d3142}</style></head><body></body></html>"

    out = inject_fonts(html, font_face_css(FONTS, SPECS))

    assert "data:font/woff2;base64," in out
    # The checker rejects non-image data URLs on tags, so it must live in <style>.
    assert '<link href="data:font' not in out
    style = out[out.index("<style>"): out.index("</style>")]
    assert "@font-face" in style
    assert "body{color:#2d3142}" in style


def test_injection_is_idempotent() -> None:
    html = "<html><head><style></style></head><body></body></html>"
    css = font_face_css(FONTS, SPECS)

    once = inject_fonts(html, css)

    assert inject_fonts(once, css) == once
