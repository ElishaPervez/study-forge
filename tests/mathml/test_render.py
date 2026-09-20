from __future__ import annotations

import xml.etree.ElementTree

import pytest

from backend.mathml.render import _escape_raw_markup, render_math, to_mathml


def test_inline_math_becomes_native_inline_mathml() -> None:
    rendered = render_math(r"<p>Water has an unusually high \(c\).</p>")

    assert rendered.startswith("<p>Water has an unusually high <math")
    assert 'display="inline"' in rendered
    assert rendered.endswith(".</p>")
    assert r"\(" not in rendered


def test_display_math_becomes_a_block_mathml_element() -> None:
    rendered = render_math(r"<div>\[c = \dfrac{Q}{m\,\Delta T}\]</div>")

    assert 'display="block"' in rendered
    assert "<mfrac>" in rendered
    assert r"\[" not in rendered


def test_surrounding_markup_is_copied_byte_for_byte() -> None:
    html = r'<p class="rule" data-note="\(not math\)">See \(x\) then stop.</p>'

    rendered = render_math(html)

    assert 'class="rule"' in rendered
    # An attribute is not text: only the one delimiter pair in the paragraph renders.
    assert r'data-note="\(not math\)"' in rendered
    assert rendered.count("<math") == 1


def test_offsets_survive_html_entities_before_the_math() -> None:
    rendered = render_math(r"<p>a &amp; b &lt; c \(x = 1\) d</p>")

    assert rendered.startswith(r"<p>a &amp; b &lt; c <math")
    assert rendered.endswith(" d</p>")


def test_multiple_expressions_in_one_text_run() -> None:
    rendered = render_math(r"<p>\(a\) then \(b\)</p>")

    assert rendered.count("<math") == 2


def test_line_breaks_around_the_math_survive() -> None:
    rendered = render_math("<p>first\n  second \\(x\\)\n  third</p>")

    assert "first\n  second <math" in rendered
    assert "\n  third</p>" in rendered


def test_trailing_text_without_a_closing_tag_is_still_converted() -> None:
    rendered = render_math(r"<p>\(x\)")

    assert "<math" in rendered
    assert r"\(" not in rendered


@pytest.mark.parametrize("text", ["<p>It costs $5 and $10.</p>", "<p>$5-$10 per unit</p>"])
def test_dollar_amounts_are_not_math(text: str) -> None:
    assert render_math(text) == text


def test_dollar_math_with_an_unmistakable_signal_is_recognised() -> None:
    rendered = render_math(r"<p>so $Q = mc\Delta T$ holds</p>")

    assert "<math" in rendered
    assert "$" not in rendered


def test_escaped_dollar_is_not_a_delimiter() -> None:
    html = r"<p>the price is \$5</p>"

    assert render_math(html) == html


@pytest.mark.parametrize("tag", ["script", "style", "svg", "code", "pre", "title"])
def test_verbatim_elements_are_left_alone(tag: str) -> None:
    html = rf"<{tag}>\(x\)</{tag}>"

    assert render_math(html) == html


def test_already_rendered_mathml_is_not_converted_again() -> None:
    html = '<math display="block"><mrow><mi>c</mi></mrow></math>'

    assert render_math(html) == html


def test_render_math_is_idempotent() -> None:
    html = r"<p>Heat \(Q = mc\Delta T\), so \[c = \frac{Q}{mT}\]</p>"

    once = render_math(html)

    assert once != html
    assert render_math(once) == once


def test_a_realistic_physics_paragraph_renders_every_expression() -> None:
    html = (
        r"<p>Water is stubborn: \(c = 4190\ \mathrm{J\,kg^{-1}\,K^{-1}}\), so "
        r"\(Q = mc\Delta T\) at \(30^\circ\text{C}\).</p>"
        r"<div>\[\rho = \frac{m}{V} \qquad v = \sqrt{2gh}\]</div>"
        r"<p>\(2H_2 + O_2 \rightarrow 2H_2O\)</p>"
    )

    rendered = render_math(html)

    assert rendered.count("<math") == 5
    assert "\\" not in rendered
    assert "mathvariant" in rendered
    assert "<msqrt>" in rendered
    assert "&#x00394;" in rendered


def test_a_failed_conversion_keeps_the_original_source(monkeypatch) -> None:
    def explode(*_args, **_kwargs):
        raise ValueError("unrenderable")

    monkeypatch.setattr("backend.mathml.render.latex2mathml.converter.convert", explode)
    html = r"<p>mass \(m\) and charge \(q\)</p>"

    assert render_math(html) == html


def test_documents_without_delimiters_are_returned_unchanged() -> None:
    html = "<p>plain prose</p>"

    assert render_math(html) is html


@pytest.mark.parametrize(
    "latex",
    [
        r"c = \frac{Q}{m\,\Delta T}",
        r"4190\ \mathrm{J\,kg^{-1}\,K^{-1}}",
        r"2H_2 + O_2 \rightarrow 2H_2O",
        r"A = \begin{pmatrix} a & b \\ c & d \end{pmatrix}",
        r"\sqrt[3]{\frac{V}{\pi}}",
        r"\left(\frac{1}{2}\right)mv^2",
    ],
)
def test_rendered_expressions_are_well_formed_mathml(latex: str) -> None:
    converted = to_mathml(latex)

    assert converted is not None
    assert xml.etree.ElementTree.fromstring(converted).tag.endswith("math")


@pytest.mark.parametrize("latex", ["", "   ", "\n\t"])
def test_blank_expressions_are_not_rendered(latex: str) -> None:
    assert to_mathml(latex) is None


def test_real_tags_and_entities_survive_the_raw_markup_escape() -> None:
    markup = '<math><mfrac><mi>a</mi><mo>&#x0003D;</mo><mspace width="0.167em" /></mfrac></math>'

    assert _escape_raw_markup(markup) == markup


def test_text_copied_out_of_latex_is_escaped() -> None:
    # latex2mathml copies "<" and "&" straight out of \text{...}, which would end the
    # sentence in a browser; the raw markup escape has to catch them.
    converted = to_mathml(r"\text{a<b & c}")

    assert converted is not None
    xml.etree.ElementTree.fromstring(converted)
    assert "&lt;" in converted
    assert "&amp;" in converted


def test_escaping_helper_escapes_only_raw_markup() -> None:
    assert _escape_raw_markup("<mtext>a<b & c>d</mtext>") == "<mtext>a&lt;b &amp; c>d</mtext>"


@pytest.mark.parametrize("latex", [r"25^\circ\text{C}", r"25^{\circ}\text{C}"])
def test_a_superscript_degree_is_tight_instead_of_an_infix_ring_operator(latex: str) -> None:
    # U+2218 (what \circ maps to) is an infix operator, so the browser pads it and the guide
    # typeset "25 ° C". A degree sign is U+00B0 and carries no padding.
    rendered = render_math(rf"<p>\({latex}\)</p>")

    assert "&#x02218;" not in rendered
    assert "\u00b0" in rendered


def test_function_composition_keeps_its_ring_operator() -> None:
    # Only a superscript \circ means degrees; a bare one is still an operator.
    rendered = render_math(r"<p>\(f \circ g\)</p>")

    assert "&#x02218;" in rendered


@pytest.mark.parametrize("latex", [r"\mathrm{J}", r"\mathrm{kg}", r"\mathrm{\Delta}"])
def test_units_are_upright_whatever_their_length(latex: str) -> None:
    # latex2mathml drops mathvariant="normal" when \mathrm{} wraps one character, which is
    # why "33750 J" was italic while "33.75 kJ" was upright in the same sentence.
    converted = to_mathml(latex)

    assert converted is not None
    assert 'mathvariant="normal"' in converted
    assert "<mrow />" not in converted
