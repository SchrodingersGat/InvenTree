"""Custom field validators for InvenTree."""

import re
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.core.validators import BaseValidator, URLValidator
from django.utils.translation import gettext_lazy as _

from moneyed import CURRENCIES

import common.models


class EmptyURLValidator(URLValidator):
    """Validator for filed with url - that can be empty."""

    def __call__(self, value):
        """Make sure empty values pass."""
        value = str(value).strip()

        if len(value) == 0:
            pass

        else:
            super().__call__(value)


class ReferenceRegexValidator(BaseValidator):
    """Class for validating a 'reference' field against a regex pattern.

    The regex pattern is determined by providing a global setting value to the class when instantiating.

    e.g. ReferenceRegexValidator('BUILDORDER_REFERENCE_REGEX')
    """

    def __init__(self, setting):
        """Initialize the validator class with the name of a global setting"""
        self.setting = setting

        if setting not in common.models.InvenTreeSetting.SETTINGS:
            raise ValueError(f'{setting} is not a valid global setting key')

    def __call__(self, value):
        """Run regex validation against the provided value"""

        pattern = common.models.InvenTreeSetting.get_setting(self.setting)

        if pattern:
            # Compile the pattern to make sure it is valid (ignore if not)
            try:
                re.compile(pattern)
            except Exception:
                return

            match = re.search(pattern, value)

            if match is None:
                raise ValidationError(_('Reference must match pattern {pattern}').format(pattern=pattern))


def validate_currency_code(code):
    """Check that a given code is a valid currency code."""
    if code not in CURRENCIES:
        raise ValidationError(_('Not a valid currency code'))


def allowable_url_schemes():
    """Return the list of allowable URL schemes.

    In addition to the default schemes allowed by Django,
    the install configuration file (config.yaml) can specify
    extra schemas
    """
    # Default schemes
    schemes = ['http', 'https', 'ftp', 'ftps']

    extra = settings.EXTRA_URL_SCHEMES

    for e in extra:
        if e.lower() not in schemes:
            schemes.append(e.lower())

    return schemes


def validate_part_name(value):
    """Prevent some illegal characters in part names."""
    for c in ['|', '#', '$', '{', '}']:
        if c in str(value):
            raise ValidationError(
                _('Invalid character in part name')
            )


def validate_part_ipn(value):
    """Validate the Part IPN against regex rule."""
    pattern = common.models.InvenTreeSetting.get_setting('PART_IPN_REGEX')

    if pattern:
        match = re.search(pattern, value)

        if match is None:
            raise ValidationError(_('IPN must match regex pattern {pat}').format(pat=pattern))


def validate_reference_regex(value):
    """Validate regex pattern for a 'reference' field.

    - Must be a valid regex pattern string
    - Must have either zero or one matching groups
    """

    value = value.strip()

    # Ignore empty values
    if not value:
        return

    # Check that the reference field compiles to a valid regex pattern
    try:
        regex = re.compile(value)
    except Exception:
        raise ValidationError(_('Must be a valid regular expression pattern'))

    if regex.groups > 1:
        raise ValidationError(_('Cannot have more than one matching group'))


def validate_tree_name(value):
    """Prevent illegal characters in tree item names."""
    for c in "!@#$%^&*'\"\\/[]{}<>,|+=~`\"":  # noqa: P103
        if c in str(value):
            raise ValidationError(_('Illegal character in name ({x})'.format(x=c)))


def validate_overage(value):
    """Validate that a BOM overage string is properly formatted.

    An overage string can look like:

    - An integer number ('1' / 3 / 4)
    - A decimal number ('0.123')
    - A percentage ('5%' / '10 %')
    """
    value = str(value).lower().strip()

    # First look for a simple numerical value
    try:
        i = Decimal(value)

        if i < 0:
            raise ValidationError(_("Overage value must not be negative"))

        # Looks like a number
        return True
    except (ValueError, InvalidOperation):
        pass

    # Now look for a percentage value
    if value.endswith('%'):
        v = value[:-1].strip()

        # Does it look like a number?
        try:
            f = float(v)

            if f < 0:
                raise ValidationError(_("Overage value must not be negative"))
            elif f > 100:
                raise ValidationError(_("Overage must not exceed 100%"))

            return True
        except ValueError:
            pass

    raise ValidationError(
        _("Invalid value for overage")
    )


def validate_part_name_format(self):
    """Validate part name format.

    Make sure that each template container has a field of Part Model
    """
    jinja_template_regex = re.compile('{{.*?}}')
    field_name_regex = re.compile('(?<=part\\.)[A-z]+')
    for jinja_template in jinja_template_regex.findall(str(self)):
        # make sure at least one and only one field is present inside the parser
        field_names = field_name_regex.findall(jinja_template)
        if len(field_names) < 1:
            raise ValidationError({
                'value': 'At least one field must be present inside a jinja template container i.e {{}}'
            })

        # Make sure that the field_name exists in Part model
        from part.models import Part

        for field_name in field_names:
            try:
                Part._meta.get_field(field_name)
            except FieldDoesNotExist:
                raise ValidationError({
                    'value': f'{field_name} does not exist in Part Model'
                })

    return True
