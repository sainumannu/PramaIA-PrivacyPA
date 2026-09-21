"""
FastAPI middleware for request/response logging.
"""

import time
from typing import Callable
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from backend.utils.logging import get_logger, sanitize_headers
from backend.utils.request_context import get_request_id

logger = get_logger("middleware")


class LoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware to log all HTTP requests and responses.
    
    Logs:
    - Request method, path, query params
    - Response status code
    - Request duration
    - Client IP
    - Errors (with stack trace)
    
    Excludes:
    - Health check endpoint
    - Static files
    """
    
    # Endpoints to skip logging (too verbose)
    SKIP_PATHS = {"/api/health", "/health", "/docs", "/openapi.json", "/redoc"}
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip logging for specified paths
        if request.url.path in self.SKIP_PATHS:
            return await call_next(request)
        
        # Extract request info
        method = request.method
        path = request.url.path
        query = str(request.query_params) if request.query_params else ""
        client_ip = request.client.host if request.client else "unknown"
        
        # Log request
        request_info = f"{method} {path}"
        if query:
            request_info += f"?{query}"
        
        logger.info(f"→ {request_info} | client={client_ip} | request_id={get_request_id()}")
        
        # Debug: log headers (sanitized)
        if logger.isEnabledFor(10):  # DEBUG level
            sanitized = sanitize_headers(dict(request.headers))
            logger.debug(f"  Headers: {sanitized}")
        
        # Process request and measure time
        start_time = time.time()
        
        try:
            response = await call_next(request)
            duration_ms = (time.time() - start_time) * 1000
            
            # Log response
            level = 20 if response.status_code < 400 else 40  # INFO or ERROR
            logger.log(
                level,
                f"← {method} {path} | {response.status_code} | {duration_ms:.0f}ms"
            )
            
            return response
            
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(
                f"✗ {method} {path} | Error: {type(e).__name__}: {str(e)} | {duration_ms:.0f}ms",
                exc_info=True
            )
            raise
