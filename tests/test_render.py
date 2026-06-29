"""Tests for the INF cell-stream -> HTML renderer."""
from sitv.render import (RenderContext, render_topic, get_panel_name,
                         _TopicRenderer, SPACER_IMAGE)


def _ctx(inf, **kw):
    return RenderContext(
        inf,
        kw.get("link_for_topic", lambda i: None),
        kw.get("link_for_external", lambda n: None),
        kw.get("image_name", lambda o: None),
    )


# ---------------------------------------------------------------------------
# Tables (escape 0x2d / 0xf6)
# ---------------------------------------------------------------------------
def test_service_spec_table_renders(inf_general):
    ctx = _ctx(inf_general)
    html = render_topic(ctx, inf_general.topics[39])
    assert "<table" in html and "</table>" in html
    # Header cells land inside <td>, not loose in the paragraph.
    assert "<td" in html and html.count("<td") == 52
    assert "Items" in html and "Standard value" in html


def test_colspans_sum_to_eighty():
    # Two columns of equal pixel width -> 40/40 split of the 80-column grid.
    assert _TopicRenderer._colspans_from_widths([100, 100]) == [40, 40]
    # Three uneven widths still sum to 80 with no zero spans.
    spans = _TopicRenderer._colspans_from_widths([12, 28, 40])
    assert sum(spans) == 80 and all(s >= 1 for s in spans)


def test_colspans_zero_width_falls_back():
    assert _TopicRenderer._colspans_from_widths([0, 0]) == [40, 40]


# ---------------------------------------------------------------------------
# Inline formatting (highlight escape 0x04)
# ---------------------------------------------------------------------------
def test_bold_is_balanced_in_tables(inf_general):
    """Bold opened in one cell must not leak past the cell boundary."""
    html = render_topic(_ctx(inf_general), inf_general.topics[39])
    # No stray </b> before its <b>, and tags are balanced overall.
    assert html.count("<b>") == html.count("</b>")
    assert html.count("<i>") == html.count("</i>")


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------
def test_external_link_resolves_when_target_known(inf_general):
    seen = {}

    def ext(name):
        seen[name] = True
        return "target.html" if name.startswith("M17") else None

    ctx = _ctx(inf_general, link_for_external=ext)
    # Render the General Information topic, which links to GROUP 17.
    for t in inf_general.topics:
        if get_panel_name(inf_general, t) == "M1310001031901":
            html = render_topic(ctx, t)
            assert '<a href="target.html">' in html
            break
    else:
        raise AssertionError("expected topic not found")


def test_unresolved_link_renders_as_underline(inf_general):
    """A link with no known target degrades to <u>, never a dangling <a>."""
    ctx = _ctx(inf_general, link_for_external=lambda n: None,
               link_for_topic=lambda i: None)
    for t in inf_general.topics:
        if get_panel_name(inf_general, t) == "M1310001031901":
            html = render_topic(ctx, t)
            assert "<u>" in html
            assert "<a href" not in html  # nothing resolved in this config
            break


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------
def test_spacer_image_emits_nothing(inf_general):
    ctx = _ctx(inf_general, image_name=lambda o: SPACER_IMAGE)
    for t in inf_general.topics:
        if get_panel_name(inf_general, t) == "M1310001031901":
            html = render_topic(ctx, t)
            assert "<img" not in html      # spacer suppressed
            break


def test_real_image_emits_img_tag(inf_general):
    ctx = _ctx(inf_general, image_name=lambda o: "pic.png")
    for t in inf_general.topics:
        if get_panel_name(inf_general, t) == "M1310001031901":
            html = render_topic(ctx, t)
            assert '<img src="pic.png"/>' in html
            break


# ---------------------------------------------------------------------------
# Panel names (escape 0x16)
# ---------------------------------------------------------------------------
def test_panel_name_extraction(inf_general):
    assert get_panel_name(inf_general, inf_general.topics[39]) == "M1310003025401"
