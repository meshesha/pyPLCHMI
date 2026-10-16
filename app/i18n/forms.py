"""A ``FlaskForm`` that shows its texts in the user's language.

All forms of the application derive from :class:`FlaskForm` defined here
(instead of ``flask_wtf.FlaskForm``).  When a form is created it translates
the labels, descriptions, ``render_kw`` placeholders/titles and the labels of
static select choices; validation errors are translated as they are added.
The English texts written in the form classes are the message keys.
"""

from __future__ import annotations

from flask_wtf import FlaskForm as _BaseForm

from app.i18n.catalog import gettext


def translate_choices(choices):
    """Translate the labels of select choices.

    Args:
        choices: List of ``(value, label)`` tuples (or values), a dict of
            groups, or ``None``.

    Returns:
        The choices with translated labels (same structure).
    """
    if choices is None:
        return None
    if isinstance(choices, dict):
        return {gettext(group): translate_choices(items) for group, items in choices.items()}
    result = []
    for choice in choices:
        if isinstance(choice, (tuple, list)) and len(choice) >= 2 and isinstance(choice[1], str):
            result.append((choice[0], gettext(choice[1]), *choice[2:]))
        else:
            result.append(choice)
    return result


def translate_field(field) -> None:
    """Translate one bound field in place.

    Args:
        field: A bound WTForms field.
    """
    if getattr(field, "label", None) is not None and isinstance(field.label.text, str):
        field.label.text = gettext(field.label.text)
    if isinstance(getattr(field, "description", None), str) and field.description:
        field.description = gettext(field.description)
    kw = getattr(field, "render_kw", None)
    if kw:
        field.render_kw = {k: (gettext(v) if k in ("placeholder", "title", "aria-label") and isinstance(v, str) else v)
                           for k, v in kw.items()}
    if field.type in ("SelectField", "SelectMultipleField", "RadioField", "MultiCheckboxField") \
            and getattr(field, "choices", None) is not None and not callable(field.choices):
        field.choices = translate_choices(field.choices)
    if field.type == "SubmitField" and isinstance(field.label.text, str):
        pass  # label already translated above


class FlaskForm(_BaseForm):
    """``flask_wtf.FlaskForm`` with translated texts (see the module docstring)."""

    def __init__(self, *args, **kwargs):
        """Create the form and translate its static texts.

        Args:
            *args: Passed to ``flask_wtf.FlaskForm``.
            **kwargs: Passed to ``flask_wtf.FlaskForm``.
        """
        super().__init__(*args, **kwargs)
        for field in self:
            translate_field(field)

    def validate(self, extra_validators=None) -> bool:
        """Validate, then translate every error message.

        Args:
            extra_validators: Passed to ``flask_wtf.FlaskForm.validate``.

        Returns:
            ``True`` when valid.
        """
        ok = super().validate(extra_validators=extra_validators)
        self.translate_errors()
        return ok

    def translate_errors(self) -> None:
        """Translate the error messages collected so far (exact English texts only)."""
        for field in self:
            if field.errors and isinstance(field.errors, list):
                field.errors[:] = [gettext(e) if isinstance(e, str) else e for e in field.errors]
