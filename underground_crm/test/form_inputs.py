"""Helpers for building the input blocks of a FormPage's body in tests."""

from typing import Any


def form_input(
    label: str, default: Any = "", *, help_text: str = "", required: bool = False
) -> dict[str, Any]:
    """
    The StreamField value of one input block (see FORM_INPUT_BLOCKS in
    models/pages.py): the question shown to the visitor, optional guidance,
    whether it must be answered, and its pre-filled value. A checkbox's
    default is a bool, and every other input's is its pre-filled text.
    """
    return {"label": label, "help_text": help_text, "required": required, "default": default}
