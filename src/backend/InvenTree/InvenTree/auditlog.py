"""Wrapper functions for the django-auditlog package."""

from django.conf import settings
from django.contrib.contenttypes.models import ContentType


def auditable_models():
    """Return a list of models which are registered for auditing."""
    from auditlog.registry import auditlog

    return auditlog.get_models()


def auditable_content_types():
    """Return a list of ContentType objects which are registered for auditing."""
    from auditlog.registry import auditlog

    return [ContentType.objects.get_for_model(model) for model in auditlog.get_models()]


def register_auditlog(model, **kwargs):
    """Wrapper function for the auditlog registry.

    If auditing is disabled, then this function will do nothing.
    """
    if not settings.AUDITLOG_ENABLED:
        return

    from auditlog.registry import auditlog

    auditlog.register(model, **kwargs)
