"""A pylint plugin that teaches astroid what Wagtail's StreamField descriptor returns.

Wagtail installs a descriptor over every StreamField, so reading `page.body` on an
instance never gives back what was assigned to it: whatever you store — a list of
(block_type, value) pairs, a list of raw-data dictionaries, a JSON string — the
descriptor converts it into a StreamValue, whose children are bound blocks carrying
`.id`, `.block_type` and `.value`.

astroid cannot see that conversion. It infers `page.body` either as the StreamField
instance declared on the class (so iterating a body reports not-an-iterable) or, once
any module in the run has assigned a literal list to a body, as that literal (so
reading a child's `.value` reports no-member on tuple or dict). Both are false
positives, and they surface far away from the assignment that caused them — a test
that builds a page body makes the library's own code fail to lint.

The transform below overrides inference for exactly those attribute accesses whose
owner declares the attribute as a StreamField, and reports a StreamValue instead.
"""

from __future__ import annotations

from functools import cache
from typing import Iterator, Optional

from astroid import MANAGER, builder, inference_tip, nodes
from astroid.bases import Instance
from astroid.context import InferenceContext
from astroid.exceptions import AstroidError, InferenceError, UseInferenceDefault
from pylint.lint import PyLinter

# Wagtail's own StreamField, plus this library's DeclaredBlocksStreamField subclass and
# any other subclass a theme may declare: all of them hand back a StreamValue when read.
STREAM_FIELD_CLASS_NAME_SUFFIX = "StreamField"


@cache
def _stream_value() -> Instance:
    """An inferred instance of wagtail's StreamValue, built once and reused."""
    construction = builder.extract_node(
        "from wagtail.blocks.stream_block import StreamValue\nStreamValue(None, [])"
    )
    return next(construction.infer())


def _declares_stream_field(owner: nodes.ClassDef, attribute_name: str) -> bool:
    """Whether the class, or any of its ancestors, assigns `attribute_name = <a>StreamField(...)`."""
    for klass in [owner, *owner.ancestors()]:
        for assignment in klass.locals.get(attribute_name, []):
            statement = assignment.parent
            if not isinstance(statement, nodes.Assign) or not isinstance(
                statement.value, nodes.Call
            ):
                continue
            called = statement.value.func
            if isinstance(called, nodes.Attribute):
                called_name = called.attrname
            else:
                called_name = getattr(called, "name", "")
            if called_name.endswith(STREAM_FIELD_CLASS_NAME_SUFFIX):
                return True
    return False


def _infer_stream_field_access(
    node: nodes.Attribute, context: Optional[InferenceContext] = None
) -> Iterator[Instance]:
    try:
        owners = list(node.expr.infer(context=context))
    except (InferenceError, AstroidError) as inference_failure:
        raise UseInferenceDefault from inference_failure

    for owner in owners:
        declaring_class = getattr(owner, "_proxied", None)
        if isinstance(declaring_class, nodes.ClassDef) and _declares_stream_field(
            declaring_class, node.attrname
        ):
            return iter([_stream_value()])

    raise UseInferenceDefault


def register(linter: PyLinter) -> None:
    """Required by pylint's plugin protocol. The work is done by the transform below,
    which astroid picks up when this module is imported."""


MANAGER.register_transform(nodes.Attribute, inference_tip(_infer_stream_field_access))
