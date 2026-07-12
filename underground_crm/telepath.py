import uuid

from telepath import BaseAdapter, StringNode
from wagtail.telepath import register as register_telepath_adapter


class UUIDTelepathAdapter(BaseAdapter):
    """
    Pack UUIDs as their canonical string form when Wagtail serializes block
    definitions for the page editor. Telepath has no built-in adapter for
    uuid.UUID, so without this, opening the editor crashes as soon as any
    chooser block has a default instance of a UUID-keyed model (the library
    convention for primary keys): the chooser's form state carries the
    instance's raw UUID primary key.

    Registered from UndergroundCrmConfig.ready() so it applies from startup,
    rather than whenever Wagtail happens to first load hook modules.
    """

    def build_node(self, obj: uuid.UUID, context) -> StringNode:
        return StringNode(str(obj))


def register_adapters() -> None:
    register_telepath_adapter(UUIDTelepathAdapter(), uuid.UUID)
