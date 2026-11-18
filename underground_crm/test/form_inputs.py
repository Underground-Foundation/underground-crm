"""Helpers for building the input blocks of a FormPage's body in tests."""

from typing import Any


def checkbox_input(
    label: str, *, ticked: bool = False, help_text: str = "", required: bool = False
) -> dict[str, Any]:
    """
    The StreamField value of one checkbox block (see FORM_INPUT_BLOCKS in
    models/pages.py): the question shown to the visitor, optional guidance,
    whether it must be ticked, and whether it starts ticked.
    """
    return {"label": label, "help_text": help_text, "required": required, "default": ticked}


def typed_input(
    label: str, *, placeholder: str = "", help_text: str = "", required: bool = False
) -> dict[str, Any]:
    """
    The StreamField value of one input the visitor types into (text, email,
    address, number or URL): the question, optional guidance, whether it must
    be answered, and example text shown while it is empty.
    """
    return {
        "label": label,
        "help_text": help_text,
        "required": required,
        "placeholder": placeholder,
    }


def picker_input(label: str, *, help_text: str = "", required: bool = False) -> dict[str, Any]:
    """
    The StreamField value of one date, time or datetime block, which the
    visitor answers with the browser's own picker: the question, optional
    guidance and whether it must be answered.
    """
    return {"label": label, "help_text": help_text, "required": required}


def choice_input(
    label: str, choices: list[str], *, help_text: str = "", required: bool = False
) -> dict[str, Any]:
    """
    The StreamField value of one radio_buttons, dropdown or checkboxes block:
    the question, optional guidance, whether it must be answered, and the
    choices offered (which the editor types one per line).
    """
    return {
        "label": label,
        "help_text": help_text,
        "required": required,
        "choices": "\n".join(choices),
    }
