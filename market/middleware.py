"""Bound request bodies even when the client sends a chunked upload."""

from fastapi.responses import JSONResponse


class BodyLimitMiddleware:
    def __init__(self, app, max_bytes):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH", "DELETE"):
            return await self.app(scope, receive, send)
        response = JSONResponse(
            {"error": {"code": "body_too_large", "message": "Request body exceeds the limit"}},
            status_code=413,
            headers={"Cache-Control": "no-store"},
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
