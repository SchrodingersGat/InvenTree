"""Wrapper functions for the django-auditlog package."""

from django.conf import settings


def register_auditlog(model, **kwargs):
    """Wrapper function for the auditlog registry.

    If auditing is disabled, then this function will do nothing.
    """
    if not settings.AUDITLOG_ENABLED:
        return

    from auditlog.registry import auditlog

    auditlog.register(model, **kwargs)
