"""
Tests for underground_crm/legacy_html.py.

Deliberately plain unittest with no Django involved: legacy_html imports only
bs4, and keeping these tests settings-free means the decomposition can be
iterated on without a database.
"""

import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from underground_crm.image_alignment import DEFAULT_IMAGE_ALIGNMENT, ImageAlignment
from underground_crm.legacy_html import (
    BUTTON_BLOCK,
    IMAGE_BLOCK,
    RAW_HTML_BLOCK,
    RICH_TEXT_BLOCK,
    decompose_legacy_content,
    do_blocks_have_visible_content,
    get_body_soup,
    remove_duplicated_intro,
    strip_presentational_markup,
    summarise,
)

SAMPLE = Path(__file__).parent / "blog_post_sample.html"


def parse(markup: str) -> BeautifulSoup:
    return BeautifulSoup(markup, "html.parser")


def stub_resolver(counter=None):
    """An image resolver that pretends every image is already in the library."""
    seen = {}

    def resolve(source, alt):
        seen.setdefault(source, len(seen) + 1)
        if counter is not None:
            counter.append((source, alt))
        return seen[source]

    return resolve


HEADER_IMAGE = '<img alt="" src="https://example.org/uploads/rally.jpg" style="width: 100%;"/>'
OPENING_PARAGRAPH = "<p>Something has changed in Australian politics.</p>"
SECOND_PARAGRAPH = "<p>One Nation's result in South Australia was not a quirky state result.</p>"
THIRD_PARAGRAPH = "<p>A line has been crossed, and the old assumptions no longer hold.</p>"
CLOSING_PARAGRAPH = "<p>What we do next is up to all of us.</p>"


def html_block(markup: str) -> dict:
    return {"type": RAW_HTML_BLOCK, "value": markup}


def rich_text_block(*paragraphs: str) -> dict:
    return {"type": RICH_TEXT_BLOCK, "value": "".join(paragraphs)}


class TestRemoveDuplicatedIntro(unittest.TestCase):
    """An intro is the start of the body, as a blog's list excerpted it."""

    def test_the_paragraphs_an_intro_quotes_are_cut_from_the_start_of_a_merged_rich_text_block(
        self,
    ):
        body = [rich_text_block(OPENING_PARAGRAPH, SECOND_PARAGRAPH, THIRD_PARAGRAPH)]
        intro = [rich_text_block(OPENING_PARAGRAPH, SECOND_PARAGRAPH)]
        remaining, _ = remove_duplicated_intro(body, intro)
        self.assertEqual(remaining, [rich_text_block(THIRD_PARAGRAPH)])

    def test_an_image_and_the_paragraphs_after_it_are_cut_across_block_boundaries(self):
        body = [html_block(HEADER_IMAGE), rich_text_block(OPENING_PARAGRAPH, SECOND_PARAGRAPH)]
        intro = [html_block(HEADER_IMAGE), rich_text_block(OPENING_PARAGRAPH)]
        remaining, _ = remove_duplicated_intro(body, intro)
        self.assertEqual(remaining, [rich_text_block(SECOND_PARAGRAPH)])

    def test_the_count_is_of_the_elements_cut(self):
        body = [html_block(HEADER_IMAGE), rich_text_block(OPENING_PARAGRAPH, SECOND_PARAGRAPH)]
        intro = [html_block(HEADER_IMAGE), rich_text_block(OPENING_PARAGRAPH)]
        _, cut = remove_duplicated_intro(body, intro)
        self.assertEqual(
            cut,
            len([HEADER_IMAGE, OPENING_PARAGRAPH]),
            "the image and the opening paragraph are the two elements the intro repeats",
        )

    def test_a_block_the_cut_ends_exactly_before_is_left_as_it_was(self):
        untouched = rich_text_block(SECOND_PARAGRAPH, "\n", THIRD_PARAGRAPH)
        remaining, _ = remove_duplicated_intro(
            [html_block(HEADER_IMAGE), untouched], [html_block(HEADER_IMAGE)]
        )
        self.assertEqual(remaining, [untouched])

    def test_blocks_after_the_cut_are_kept(self):
        closing = html_block("<iframe src='https://example.org/video'></iframe>")
        remaining, _ = remove_duplicated_intro(
            [rich_text_block(OPENING_PARAGRAPH, SECOND_PARAGRAPH), closing],
            [rich_text_block(OPENING_PARAGRAPH)],
        )
        self.assertEqual(remaining, [rich_text_block(SECOND_PARAGRAPH), closing])

    def test_a_body_the_intro_covers_entirely_is_left_empty(self):
        body = [rich_text_block(OPENING_PARAGRAPH, SECOND_PARAGRAPH)]
        remaining, _ = remove_duplicated_intro(body, body)
        self.assertEqual(remaining, [])

    def test_nothing_is_cut_when_the_body_does_not_begin_with_the_intro(self):
        body = [rich_text_block(SECOND_PARAGRAPH, THIRD_PARAGRAPH)]
        remaining, cut = remove_duplicated_intro(body, [rich_text_block(OPENING_PARAGRAPH)])
        self.assertEqual(remaining, body)
        self.assertEqual(cut, 0, "no element of the intro appears at the start of the body")

    def test_cutting_stops_at_the_first_element_that_differs(self):
        edited_second = "<p>One Nation's result in South Australia was a warning.</p>"
        body = [rich_text_block(OPENING_PARAGRAPH, edited_second, THIRD_PARAGRAPH)]
        intro = [rich_text_block(OPENING_PARAGRAPH, SECOND_PARAGRAPH, THIRD_PARAGRAPH)]
        remaining, _ = remove_duplicated_intro(body, intro)
        self.assertEqual(
            remaining,
            [rich_text_block(edited_second, THIRD_PARAGRAPH)],
            "a repeat of the third paragraph after an edited second one is not part of the intro",
        )

    def test_a_paragraph_the_intro_only_partly_quotes_stays_in_full(self):
        truncated = "<p>One Nation's result in South Australia was not…</p>"
        body = [rich_text_block(OPENING_PARAGRAPH, SECOND_PARAGRAPH)]
        remaining, _ = remove_duplicated_intro(
            body, [rich_text_block(OPENING_PARAGRAPH, truncated)]
        )
        self.assertEqual(remaining, [rich_text_block(SECOND_PARAGRAPH)])

    def test_differences_in_whitespace_do_not_matter(self):
        body = [rich_text_block(OPENING_PARAGRAPH, SECOND_PARAGRAPH)]
        spaced_intro = [
            rich_text_block("<p>\n  Something has changed in   Australian politics.\n</p>")
        ]
        remaining, _ = remove_duplicated_intro(body, spaced_intro)
        self.assertEqual(remaining, [rich_text_block(SECOND_PARAGRAPH)])

    def test_identical_button_blocks_are_recognised(self):
        donate = {"type": BUTTON_BLOCK, "value": {"text": "Donate", "url": "/donate"}}
        remaining, _ = remove_duplicated_intro(
            [donate, rich_text_block(OPENING_PARAGRAPH)], [dict(donate)]
        )
        self.assertEqual(remaining, [rich_text_block(OPENING_PARAGRAPH)])

    def test_an_empty_intro_cuts_nothing(self):
        body = [rich_text_block(OPENING_PARAGRAPH)]
        self.assertEqual(remove_duplicated_intro(body, []), (body, 0))

    def test_the_arguments_are_not_modified(self):
        body = [rich_text_block(OPENING_PARAGRAPH, SECOND_PARAGRAPH)]
        intro = [rich_text_block(OPENING_PARAGRAPH)]
        remove_duplicated_intro(body, intro)
        self.assertEqual(body, [rich_text_block(OPENING_PARAGRAPH, SECOND_PARAGRAPH)])
        self.assertEqual(intro, [rich_text_block(OPENING_PARAGRAPH)])

    def test_raw_html_blocks_are_split_like_rich_text(self):
        body = [html_block(f"{HEADER_IMAGE}\n{CLOSING_PARAGRAPH}")]
        remaining, _ = remove_duplicated_intro(body, [html_block(HEADER_IMAGE)])
        self.assertEqual(remaining, [html_block(CLOSING_PARAGRAPH)])


class TestBlocksHaveContent(unittest.TestCase):
    def test_no_blocks_have_no_content(self):
        self.assertFalse(do_blocks_have_visible_content([]))

    def test_a_rich_text_block_of_blank_paragraphs_has_no_content(self):
        self.assertFalse(
            do_blocks_have_visible_content([rich_text_block("<p>&nbsp;</p>", "<p> </p>")])
        )

    def test_a_paragraph_of_text_is_content(self):
        self.assertTrue(do_blocks_have_visible_content([rich_text_block(OPENING_PARAGRAPH)]))

    def test_a_raw_html_block_with_only_an_image_is_content(self):
        self.assertTrue(do_blocks_have_visible_content([html_block(HEADER_IMAGE)]))

    def test_a_button_is_content(self):
        button = {"type": BUTTON_BLOCK, "value": {"text": "Donate", "url": "/donate"}}
        self.assertTrue(do_blocks_have_visible_content([button]))


class TestStripPresentationalMarkup(unittest.TestCase):

    def test_font_weight_400_span_is_unwrapped(self):
        """The Google Docs / legacy CMS artefact goes, its children stay put."""
        soup = parse('<p><span style="font-weight: 400;">Hello <em>there</em></span></p>')
        strip_presentational_markup(soup)
        self.assertEqual(str(soup), "<p>Hello <em>there</em></p>")

    def test_nested_spans_are_all_unwrapped(self):
        soup = parse(
            '<p><span style="font-weight: 400;"><span style="font-family: Arial; '
            'font-size: 11pt;">Deeply nested</span></span></p>'
        )
        strip_presentational_markup(soup)
        self.assertEqual(str(soup), "<p>Deeply nested</p>")

    def test_bare_span_is_unwrapped(self):
        soup = parse("<p>Before<span> </span><strong>after</strong></p>")
        strip_presentational_markup(soup)
        self.assertEqual(str(soup), "<p>Before <strong>after</strong></p>")

    def test_bold_expressed_as_css_becomes_strong(self):
        """font-weight: 700 is markup the legacy editor wrote as CSS, not noise."""
        soup = parse('<p><span style="font-weight: 700;">Reply to them.</span></p>')
        strip_presentational_markup(soup)
        self.assertEqual(str(soup), "<p><strong>Reply to them.</strong></p>")

    def test_italic_expressed_as_css_becomes_em(self):
        soup = parse('<p><span style="font-style: italic;">Sincerely</span></p>')
        strip_presentational_markup(soup)
        self.assertEqual(str(soup), "<p><em>Sincerely</em></p>")

    def test_underline_expressed_as_css_becomes_u(self):
        """
        text-decoration: underline is the legacy editor's own underline
        button, not Google Docs noise, so it survives as <u> — a registered
        Draftail feature (see register_underline_feature in wagtail_hooks.py)
        — rather than pushing the paragraph into a raw HTML block.
        """
        soup = parse(
            '<p><span style="text-decoration: underline;">'
            "If you are a member, now is the time to act like it matters."
            "</span></p>"
        )
        strip_presentational_markup(soup)
        self.assertEqual(
            str(soup),
            "<p><u>If you are a member, now is the time to act like it matters.</u></p>",
        )

    def test_underline_combines_with_bold(self):
        soup = parse(
            '<p><strong><span style="text-decoration: underline;">' "Act now" "</span></strong></p>"
        )
        strip_presentational_markup(soup)
        self.assertEqual(str(soup), "<p><strong><u>Act now</u></strong></p>")

    def test_css_underline_on_a_paragraph_wraps_its_contents(self):
        soup = parse('<p style="text-decoration: underline;">Take note</p>')
        strip_presentational_markup(soup)
        self.assertEqual(str(soup), "<p><u>Take note</u></p>")

    def test_meaningful_span_is_kept(self):
        """A colour cannot be expressed as a block, so the span stays to be preserved verbatim."""
        soup = parse('<p><span style="color: #ff0000; font-size: 11pt;">Careful</span></p>')
        strip_presentational_markup(soup)
        self.assertEqual(str(soup), '<p><span style="color: #ff0000">Careful</span></p>')

    def test_noise_is_dropped_from_block_tags_without_unwrapping_them(self):
        soup = parse('<p style="font-weight: 400; line-height: 1.5;">Plain</p>')
        strip_presentational_markup(soup)
        self.assertEqual(str(soup), "<p>Plain</p>")

    def test_css_bold_on_a_paragraph_wraps_its_contents(self):
        soup = parse('<p style="font-weight: bold;">Shouting</p>')
        strip_presentational_markup(soup)
        self.assertEqual(str(soup), "<p><strong>Shouting</strong></p>")

    def test_image_style_is_left_alone(self):
        """_image_alignment still needs the width, and no img style reaches a block."""
        soup = parse('<p><img src="a.png" style="width: 60%; font-family: Arial;"/></p>')
        strip_presentational_markup(soup)
        self.assertIn("width: 60%", str(soup))


class TestGetBodySoup(unittest.TestCase):

    def test_content_class_div_is_preferred_over_inner_content_id_div(self):
        soup = parse(
            '<main id="content">'
            '<div id="content" class="container">'
            '<section class="byline">By someone</section>'
            '<div id="intro" class="intro"><div class="content"><p>Body</p></div></div>'
            "</div>"
            "</main>"
        )
        container = get_body_soup(soup)
        self.assertEqual(container.get("class"), ["content"])
        self.assertNotIn("byline", str(container))

    def test_inner_content_div_is_preferred(self):
        soup = parse(
            '<main id="content"><div id="headline"></div><div id="content"><p>Body</p></div></main>'
        )
        container = get_body_soup(soup)
        self.assertEqual(container.get("id"), "content")
        self.assertEqual(container.name, "div")

    def test_container_div_is_the_fallback(self):
        soup = parse('<main id="content"><div class="container"><p>Body</p></div></main>')
        self.assertEqual(get_body_soup(soup).get("class"), ["container"])

    def test_content_element_itself_is_the_last_resort(self):
        soup = parse('<main id="content"><p>Body</p></main>')
        self.assertIs(get_body_soup(soup), soup.find(id="content"))

    def test_the_content_element_may_be_passed_instead_of_the_document(self):
        """Callers that have already extracted the region must get the same narrowing."""
        soup = parse(
            '<html><body><nav>Menu</nav><main id="content">'
            '<section class="byline">By Priya Raman</section>'
            '<div class="content"><p>Body</p></div>'
            "</main></body></html>"
        )
        content_element = soup.find(id="content")
        self.assertIs(
            get_body_soup(content_element),
            get_body_soup(soup),
            "Narrowing the extracted region must find the same article as narrowing the document.",
        )
        self.assertEqual(get_body_soup(content_element).get("class"), ["content"])

    def test_a_page_without_a_content_region_has_no_body(self):
        soup = parse("<html><body><nav>Menu</nav><footer>Contact us</footer></body></html>")
        self.assertIsNone(get_body_soup(soup))


class TestDecomposition(unittest.TestCase):

    def test_consecutive_paragraphs_merge_into_one_rich_text_block(self):
        soup = parse('<div id="content"><p>One</p><p>Two</p><h2>Three</h2></div>')
        blocks = decompose_legacy_content(soup)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["type"], RICH_TEXT_BLOCK)
        self.assertEqual(blocks[0]["value"], "<p>One</p><p>Two</p><h2>Three</h2>")

    def test_attributes_are_dropped_but_links_keep_their_href(self):
        soup = parse(
            '<div id="content"><p class="mt-2" style="text-align: center;">'
            'See <a class="text-reset" href="/join" target="_blank">joining</a></p></div>'
        )
        blocks = decompose_legacy_content(soup)
        self.assertEqual(blocks[0]["value"], '<p>See <a href="/join">joining</a></p>')

    def test_body_h1_is_demoted_to_h2(self):
        soup = parse('<div id="content"><h1>What happens next</h1></div>')
        blocks = decompose_legacy_content(soup)
        self.assertEqual(blocks[0]["value"], "<h2>What happens next</h2>")

    def test_h5_and_h6_are_demoted_to_the_lowest_available_heading(self):
        soup = parse('<div id="content"><h5>Small</h5><h6>Smaller</h6></div>')
        blocks = decompose_legacy_content(soup)
        self.assertEqual(blocks[0]["value"], "<h4>Small</h4><h4>Smaller</h4>")

    def test_empty_paragraphs_are_dropped(self):
        soup = parse('<div id="content"><p>Real</p><p> </p><p> </p></div>')
        blocks = decompose_legacy_content(soup)
        self.assertEqual(blocks[0]["value"], "<p>Real</p>")

    def test_unsupported_inline_markup_falls_back_for_that_node_only(self):
        """The whole point: one <code> costs its own paragraph, not the article."""
        soup = parse(
            '<div id="content"><p>Before</p>'
            "<p>Published at <code>/membership</code></p>"
            "<p>After</p></div>"
        )
        blocks = decompose_legacy_content(soup)
        self.assertEqual(
            [block["type"] for block in blocks],
            [RICH_TEXT_BLOCK, RAW_HTML_BLOCK, RICH_TEXT_BLOCK],
        )
        self.assertIn("<code>", blocks[1]["value"])
        self.assertEqual(blocks[0]["value"], "<p>Before</p>")
        self.assertEqual(blocks[2]["value"], "<p>After</p>")

    def test_custom_html_falls_back_and_neighbouring_prose_does_not(self):
        soup = parse(
            '<div id="content"><p>Before</p>'
            '<div class="embedded-widget"><iframe src="https://example.test/embed"></iframe></div>'
            "<p>After</p></div>"
        )
        blocks = decompose_legacy_content(soup)
        self.assertEqual(
            [block["type"] for block in blocks],
            [RICH_TEXT_BLOCK, RAW_HTML_BLOCK, RICH_TEXT_BLOCK],
        )
        self.assertIn("<iframe", blocks[1]["value"])

    def test_consecutive_unsupported_nodes_merge_into_one_raw_html_block(self):
        soup = parse(
            '<div id="content"><div class="widget-a"><iframe src="a"></iframe></div>'
            '<div class="widget-b"><iframe src="b"></iframe></div></div>'
        )
        blocks = decompose_legacy_content(soup)
        self.assertEqual([block["type"] for block in blocks], [RAW_HTML_BLOCK])
        self.assertIn("widget-a", blocks[0]["value"])
        self.assertIn("widget-b", blocks[0]["value"])

    def test_layout_wrappers_are_descended_through(self):
        soup = parse(
            '<div id="content"><div class="mb-2"><div class="row"><p>Inside</p></div></div></div>'
        )
        blocks = decompose_legacy_content(soup)
        self.assertEqual([block["type"] for block in blocks], [RICH_TEXT_BLOCK])
        self.assertEqual(blocks[0]["value"], "<p>Inside</p>")

    def test_single_cell_layout_table_is_descended_through(self):
        soup = parse(
            '<div id="content"><table border="0" width="100%"><tbody><tr><td align="left">'
            "<p>Framed prose</p></td></tr></tbody></table></div>"
        )
        blocks = decompose_legacy_content(soup)
        self.assertEqual([block["type"] for block in blocks], [RICH_TEXT_BLOCK])
        self.assertEqual(blocks[0]["value"], "<p>Framed prose</p>")

    def test_real_table_is_preserved_as_raw_html(self):
        soup = parse(
            '<div id="content"><table><tbody>'
            "<tr><td>VIC</td><td>800</td></tr><tr><td>NSW</td><td>900</td></tr>"
            "</tbody></table></div>"
        )
        blocks = decompose_legacy_content(soup)
        self.assertEqual([block["type"] for block in blocks], [RAW_HTML_BLOCK])
        self.assertIn("<table>", blocks[0]["value"])

    def test_extraneous_elements_are_removed(self):
        soup = parse(
            '<main id="content"><div class="container">'
            '<div id="headline"><div class="byline">Posted by someone</div></div>'
            '<div id="content"><p>Article</p></div>'
            '<div id="comments"><p>Great news!</p></div>'
            "</div></main>"
        )
        blocks = decompose_legacy_content(soup)
        self.assertEqual([block["type"] for block in blocks], [RICH_TEXT_BLOCK])
        self.assertEqual(blocks[0]["value"], "<p>Article</p>")

    def test_nothing_decomposable_returns_no_blocks(self):
        soup = parse(
            '<div id="content"><div id="comments"><p>Only extraneous content</p></div></div>'
        )
        self.assertEqual(decompose_legacy_content(soup), [])

    def test_a_fragment_without_a_content_region_is_taken_as_the_article(self):
        """A blog post's excerpt is handed over on its own, not as part of a page."""
        excerpt = parse("<div><p>Preselection nominations open on Monday.</p></div>").find("div")
        blocks = decompose_legacy_content(excerpt)
        self.assertEqual([block["type"] for block in blocks], [RICH_TEXT_BLOCK])
        self.assertEqual(blocks[0]["value"], "<p>Preselection nominations open on Monday.</p>")

    def test_horizontal_rules_and_blockquotes_stay_rich_text(self):
        soup = parse(
            '<div id="content"><p>Before</p><hr/>'
            "<blockquote>A <em>quoted</em> remark</blockquote></div>"
        )
        blocks = decompose_legacy_content(soup)
        self.assertEqual([block["type"] for block in blocks], [RICH_TEXT_BLOCK])
        self.assertEqual(
            blocks[0]["value"],
            "<p>Before</p><hr/><blockquote>A <em>quoted</em> remark</blockquote>",
        )

    def test_a_list_holding_an_image_is_preserved_whole(self):
        """A list cannot be split around a picture without losing the list."""
        soup = parse(
            '<div id="content"><ul><li>Look: <img src="https://a.test/b.png"/></li></ul></div>'
        )
        blocks = decompose_legacy_content(soup, image_resolver=stub_resolver())
        self.assertEqual([block["type"] for block in blocks], [RAW_HTML_BLOCK])
        self.assertIn("<li>", blocks[0]["value"])

    def test_raw_html_keeps_inline_whitespace_exactly(self):
        """Prettifying a fallback would pad the inside of its inline elements."""
        soup = parse('<div id="content"><p>at <code>/membership</code> each month</p></div>')
        blocks = decompose_legacy_content(soup)
        self.assertEqual(blocks[0]["value"], "<p>at <code>/membership</code> each month</p>")

    def test_bare_text_between_blocks_becomes_a_paragraph(self):
        soup = parse('<div id="content">Loose sentence.<p>Proper one.</p></div>')
        blocks = decompose_legacy_content(soup)
        self.assertEqual(blocks[0]["value"], "<p>Loose sentence.</p><p>Proper one.</p>")


class TestImages(unittest.TestCase):

    def test_image_paragraph_becomes_an_image_block(self):
        soup = parse(
            '<div id="content"><p><span style="font-weight: 400;">'
            '<img alt="A banner" src="https://assets.example.test/banner.png" width="60%"/>'
            "</span></p></div>"
        )
        blocks = decompose_legacy_content(soup, image_resolver=stub_resolver())
        self.assertEqual([block["type"] for block in blocks], [IMAGE_BLOCK])
        self.assertEqual(
            blocks[0]["value"], {"image": 1, "caption": "", "alignment": ImageAlignment.HALF_WIDTH}
        )

    def test_full_width_image(self):
        soup = parse('<div id="content"><p><img src="a.png" style="width: 100%;"/></p></div>')
        blocks = decompose_legacy_content(soup, image_resolver=stub_resolver())
        self.assertEqual(blocks[0]["value"]["alignment"], DEFAULT_IMAGE_ALIGNMENT)

    def test_floated_image_keeps_its_side(self):
        soup = parse('<div id="content"><p><img src="a.png" style="float: right;"/></p></div>')
        blocks = decompose_legacy_content(soup, image_resolver=stub_resolver())
        self.assertEqual(blocks[0]["value"]["alignment"], ImageAlignment.RIGHT)

    def test_alt_text_is_passed_to_the_resolver(self):
        seen = []
        soup = parse(
            '<div id="content"><p><img alt="A banner" src="https://a.test/b.png"/></p></div>'
        )
        decompose_legacy_content(soup, image_resolver=stub_resolver(seen))
        self.assertEqual(seen, [("https://a.test/b.png", "A banner")])

    def test_unresolvable_image_falls_back_to_raw_html(self):
        soup = parse(
            '<div id="content"><p>Before</p><p><img src="image.png"/></p><p>After</p></div>'
        )
        blocks = decompose_legacy_content(soup, image_resolver=lambda source, alt: None)
        self.assertEqual(
            [block["type"] for block in blocks],
            [RICH_TEXT_BLOCK, RAW_HTML_BLOCK, RICH_TEXT_BLOCK],
        )
        self.assertIn('src="image.png"', blocks[1]["value"])

    def test_no_resolver_leaves_every_image_as_raw_html(self):
        soup = parse('<div id="content"><p><img src="https://a.test/b.png"/></p></div>')
        blocks = decompose_legacy_content(soup)
        self.assertEqual([block["type"] for block in blocks], [RAW_HTML_BLOCK])

    def test_prose_around_an_inline_image_keeps_its_reading_order(self):
        soup = parse(
            '<div id="content"><p>Words before '
            '<img src="https://a.test/b.png"/> and words after.</p></div>'
        )
        blocks = decompose_legacy_content(soup, image_resolver=stub_resolver())
        self.assertEqual(
            [block["type"] for block in blocks],
            [RICH_TEXT_BLOCK, IMAGE_BLOCK, RICH_TEXT_BLOCK],
        )
        self.assertEqual(blocks[0]["value"], "<p>Words before </p>")
        self.assertEqual(blocks[2]["value"], "<p> and words after.</p>")

    def test_linked_image_is_preserved_whole(self):
        """Lifting the image out would drop the link, so the paragraph stays as markup."""
        soup = parse(
            '<div id="content"><p><a href="/join"><img src="https://a.test/b.png"/></a></p></div>'
        )
        blocks = decompose_legacy_content(soup, image_resolver=stub_resolver())
        self.assertEqual([block["type"] for block in blocks], [RAW_HTML_BLOCK])
        self.assertIn('href="/join"', blocks[0]["value"])

    def test_identical_sources_resolve_once(self):
        seen = []
        soup = parse(
            '<div id="content"><p><img src="https://a.test/b.png"/></p>'
            '<p><img src="https://a.test/b.png"/></p></div>'
        )
        blocks = decompose_legacy_content(soup, image_resolver=stub_resolver(seen))
        self.assertEqual([block["value"]["image"] for block in blocks], [1, 1])
        self.assertEqual(len(seen), 2, msg="The resolver itself owns the caching")


class TestButtons(unittest.TestCase):

    editor_button = (
        '<div id="content"><div class="nb-tmce-btn nb-btn" style="width: 100%; text-align: center;">'
        '<table border="0" style="background-color: #c926f2; width: 300px;"><tbody><tr>'
        '<td align="center" style="font-weight: bold;">'
        '<a href="https://www.fusionparty.org.au/join" style="position: absolute;"></a> '
        '<a href="https://www.fusionparty.org.au/join">Join Fusion Party Victoria</a>'
        "</td></tr></tbody></table></div></div>"
    )

    def test_editor_button_becomes_a_button_block(self):
        blocks = decompose_legacy_content(parse(self.editor_button))
        self.assertEqual([block["type"] for block in blocks], [BUTTON_BLOCK])
        self.assertEqual(
            blocks[0]["value"],
            {
                "text": "Join Fusion Party Victoria",
                "url": "https://www.fusionparty.org.au/join",
                "background_color": "#c926f2",
                # The 300px table is centred by its wrapper, not stretched.
                "width": "",
            },
        )

    def test_themed_anchor_becomes_a_button_block(self):
        soup = parse(
            '<div id="content"><p><a class="btn btn-info w-100" href="/donate">Donate</a></p></div>'
        )
        blocks = decompose_legacy_content(soup)
        self.assertEqual(blocks[0]["type"], BUTTON_BLOCK)
        self.assertEqual(blocks[0]["value"]["width"], "w-100")
        self.assertEqual(blocks[0]["value"]["text"], "Donate")

    def test_an_ordinary_link_in_prose_is_not_a_button(self):
        soup = parse('<div id="content"><p>Read the <a href="/bill">bill</a> yourself</p></div>')
        blocks = decompose_legacy_content(soup)
        self.assertEqual([block["type"] for block in blocks], [RICH_TEXT_BLOCK])

    def test_a_paragraph_that_is_only_a_plain_link_stays_rich_text(self):
        soup = parse('<div id="content"><p><a href="/bill">The bill</a></p></div>')
        blocks = decompose_legacy_content(soup)
        self.assertEqual([block["type"] for block in blocks], [RICH_TEXT_BLOCK])

    def test_two_links_to_different_places_are_not_a_button(self):
        soup = parse(
            '<div id="content"><div class="nb-btn">'
            '<a class="btn" href="/a">A</a><a class="btn" href="/b">B</a></div></div>'
        )
        blocks = decompose_legacy_content(soup)
        self.assertEqual([block["type"] for block in blocks], [RAW_HTML_BLOCK])


class TestSampleBlogPost(unittest.TestCase):
    """The whole pipeline over a page shaped like a real legacy CMS blog post."""

    @classmethod
    def setUpClass(cls):
        document = BeautifulSoup(SAMPLE.read_text(encoding="utf-8"), "html.parser")
        cls.content = document.find(id="content")
        cls.blocks = decompose_legacy_content(cls.content, image_resolver=stub_resolver())

    def test_block_sequence(self):
        self.assertEqual(
            [block["type"] for block in self.blocks],
            [
                RICH_TEXT_BLOCK,  # the two intro paragraphs, the h1-turned-h2, the list
                IMAGE_BLOCK,  # the announcement graphic
                BUTTON_BLOCK,  # the editor's join button
                RICH_TEXT_BLOCK,  # the prose inside the layout table
                IMAGE_BLOCK,  # the volunteers photo inside the layout table
                # The <code> paragraph, the iframe widget and the coloured
                # span are consecutive, so one raw HTML block carries all
                # three rather than three blocks carrying one each.
                RAW_HTML_BLOCK,
                RICH_TEXT_BLOCK,  # the closing paragraphs
            ],
        )

    def test_no_presentational_spans_survive_into_rich_text(self):
        for block in self.blocks:
            if block["type"] == RICH_TEXT_BLOCK:
                self.assertNotIn("<span", block["value"])
                self.assertNotIn("style=", block["value"])

    def test_css_bold_survives_as_strong(self):
        self.assertIn("<strong> Please reply to them.</strong>", self.blocks[0]["value"])

    def test_the_body_heading_is_demoted(self):
        self.assertIn("<h2>What happens next</h2>", self.blocks[0]["value"])
        self.assertNotIn("<h1>", self.blocks[0]["value"])

    def test_the_list_is_kept_as_rich_text(self):
        self.assertIn("<li>Check your inbox for a letter.</li>", self.blocks[0]["value"])

    def test_the_byline_and_comments_are_gone(self):
        rendered = "".join(str(block["value"]) for block in self.blocks)
        self.assertNotIn("Miles Whiticker", rendered)
        self.assertNotIn("Great news!", rendered)

    def test_the_announcement_image_keeps_its_width(self):
        self.assertEqual(self.blocks[1]["value"]["alignment"], ImageAlignment.HALF_WIDTH)

    def test_unsupported_neighbours_share_one_raw_html_block(self):
        fallback = self.blocks[5]["value"]
        self.assertIn("<code", fallback)
        self.assertIn("<iframe", fallback)

    def test_the_coloured_span_is_preserved_verbatim(self):
        self.assertIn("color: #ff0000", self.blocks[5]["value"])

    def test_raw_html_is_the_exception_rather_than_the_body(self):
        raw = [block for block in self.blocks if block["type"] == RAW_HTML_BLOCK]
        self.assertLess(len(raw), len(self.blocks) / 2)

    def test_the_source_document_is_not_modified(self):
        self.assertIn('style="font-weight: 400;"', str(self.content))

    def test_summarise(self):
        self.assertEqual(summarise(self.blocks), "1 × button, 1 × html, 2 × image, 3 × rich_text")


if __name__ == "__main__":
    unittest.main()
