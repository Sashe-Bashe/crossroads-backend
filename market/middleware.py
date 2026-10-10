"""Bound request bodies even when the client sends a chunked upload."""

from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse


class ResourceCORSMiddleware(CORSMiddleware):
    """CORS preflight keeps its protocol headers and uses the JSON contract too."""

    def preflight_response(self, request_headers):
        response = super().preflight_response(request_headers)
        if response.status_code >= 400:
            body = {
                "error": {
                    "code": "cors_rejected",
                    "message": response.body.decode("utf-8"),
                    "details": None,
                },
                "status_code": response.status_code,
            }
        else:
            body = {"cors": {"allowed": True}, "status_code": response.status_code}
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"content-type", "content-length"}
        }
        headers["Cache-Control"] = "no-store"
        return JSONResponse(body, status_code=response.status_code, headers=headers)


class BodyLimitMiddleware:
    def __init__(self, app, max_bytes):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH", "DELETE"):
            return await self.app(scope, receive, send)
        response = JSONResponse(
            {
                "error": {
                    "code": "body_too_large",
                    "message": "Request body exceeds the limit",
                    "details": None,
                },
                "status_code": 413,
            },
            status_code=413,
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )
        length = dict(scope.get("headers", [])).get(b"content-length")
        if length and (not length.isdigit() or int(length) > self.max_bytes):
            return await response(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_bytes:
                return await response(scope, receive, send)
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        pending = True

        async def replay():
            nonlocal pending
            if pending:
                pending = False
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
