"""Bound request bytes before FastAPI/Pydantic or JSON parsing."""

from starlette.responses import JSONResponse


class BoundedBodyMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        maximum = 65536 if path in {"/api/v1/messages", "/api/v1/sms/messages"} else 1048576
        lengths = [v for k, v in scope.get("headers", []) if k.lower() == b"content-length"]
        declared = None
        if lengths:
            if len(lengths) != 1 or not lengths[0].isdigit() or len(lengths[0]) > 12:
                return await JSONResponse({"detail": "invalid_content_length"}, 400)(
                    scope, receive, send
                )
            declared = int(lengths[0])
            if declared > maximum:
                return await JSONResponse({"detail": "request_body_too_large"}, 413)(
                    scope, receive, send
                )
        body = bytearray()
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            if event["type"] != "http.request":
                continue
            chunk = event.get("body", b"")
            if len(body) + len(chunk) > maximum:
                return await JSONResponse({"detail": "request_body_too_large"}, 413)(
                    scope, receive, send
                )
            body.extend(chunk)
            if not event.get("more_body", False):
                break
        if declared is not None and declared != len(body):
            return await JSONResponse({"detail": "content_length_mismatch"}, 400)(
                scope, receive, send
            )
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)
