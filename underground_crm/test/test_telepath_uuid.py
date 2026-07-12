import logging

import django.test
from wagtail.blocks import StreamBlock
from wagtail.snippets.blocks import SnippetChooserBlock
from wagtail.telepath import JSContext

from underground_crm.models import Tag

logger = logging.getLogger(__name__)


class UUIDTelepathAdapterTest(django.test.TestCase):
    """
    Every model in this library uses a UUID primary key, but telepath (the
    serializer Wagtail uses to send block definitions to the page editor)
    has no built-in adapter for uuid.UUID. underground_crm.telepath provides
    one that packs UUIDs as their canonical string form, registered from
    UndergroundCrmConfig.ready(); these tests pin that down.
    """

    def test_uuid_packs_as_its_string_form(self):
        tag = Tag.objects.create(name="Quarterly newsletter")
        self.assertEqual(
            JSContext().pack(tag.pk),
            str(tag.pk),
            msg="A UUID must pack to the same string a chooser widget stores "
            "in stream data, so the editor can match them up",
        )

    def test_stream_block_with_uuid_keyed_chooser_default_serializes(self):
        # This is the failure mode that motivated the adapter: a StreamField
        # body containing a chooser block whose default is an instance of a
        # UUID-keyed model. StreamBlockAdapter.js_args packs each child
        # block's default form state, which for a chooser carries the default
        # instance's raw primary key — without the adapter, opening any page
        # with such a body raises ValueError("Error while serializing block
        # definition: don't know how to pack object: UUID(...)").
        tag = Tag.objects.create(name="Quarterly newsletter")
        body_blocks = StreamBlock(
            [("tag_to_apply", SnippetChooserBlock("underground_crm.Tag", default=tag))]
        )
        packed = JSContext().pack(body_blocks)
        self.assertIn(
            str(tag.pk),
            str(packed),
            msg="The packed block definition must reference the chooser's "
            "default instance by its primary key, in string form",
        )
