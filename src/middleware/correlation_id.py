"""
Correlation ID middleware.

Reads X-Correlation-ID from the incoming request.
If absent, generates a new UUID v4 and injects it.
The ID is echoed back in the response and stored in request.state
so routers and services can include it in log records.

Always search Cloud Logging with the correlation ID to trace a request end-to-end.
"""

import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

CORRELATION_ID_HEADER = "X-Correlation-ID"


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        correlation_id = request.headers.get(CORRELATION_ID_HEADER) or str(uuid.uuid4())

        # Validate that an externally provided ID is a valid UUID (prevents log injection)
        try:
            uuid.UUID(correlation_id)
        except ValueError:
            correlation_id = str(uuid.uuid4())

        request.state.correlation_id = correlation_id

        response = await call_next(request)
        response.headers[CORRELATION_ID_HEADER] = correlation_id
        return response
