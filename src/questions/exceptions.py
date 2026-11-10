import logging

from django.core.exceptions import PermissionDenied
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


def api_exception_handler(exc, context):
    """
    Give every API error the same shape:

        {"error": {"code": "...", "message": "...", "details": ... | null}}

    `code` is stable and machine-readable (e.g. "validation_error",
    "not_found", "throttled"); `details` carries per-field validation errors.
    """
    # DRF converts these Django exceptions too; normalise them up front so
    # we can read a code from them.
    if isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, PermissionDenied):
        exc = exceptions.PermissionDenied()

    response = exception_handler(exc, context)

    if response is None:
        # Unhandled error: log the traceback, but never leak it to clients.
        logger.exception("Unhandled API error", exc_info=exc)
        return Response(
            _error("server_error", "An unexpected error occurred."),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if isinstance(exc, exceptions.ValidationError):
        response.data = _error(
            "validation_error",
            "The request data is invalid.",
            details=response.data,
        )
    else:
        response.data = _error(exc.default_code, str(exc.detail))

    return response


def _error(code, message, details=None):
    return {"error": {"code": code, "message": message, "details": details}}
