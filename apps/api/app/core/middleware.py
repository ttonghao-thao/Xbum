import tempfile
from uuid import uuid4

from starlette.responses import JSONResponse


class BoundedRequestBody:
    """Bound chunked bodies before multipart parsing can spool unlimited bytes."""

    def __init__(self, app, maximum):
        self.app, self.maximum = app, maximum

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in {"GET", "HEAD", "OPTIONS"}:
            return await self.app(scope, receive, send)
        with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as stream:
            total = 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body = message.get("body", b"")
                total += len(body)
                if total > self.maximum:
                    response = JSONResponse(
                        {
                            "code": "http_413",
                            "message": "请求文件超过大小限制",
                            "details": [],
                            "request_id": uuid4().hex,
                        },
                        status_code=413,
                        headers={"Cache-Control": "no-store"},
                    )
                    return await response(scope, receive, send)
                stream.write(body)
                if not message.get("more_body", False):
                    break
            stream.seek(0)
            replayed = False

            async def replay():
                nonlocal replayed
                if replayed:
                    return await receive()
                block = stream.read(64 * 1024)
                more = stream.tell() < total
                if not more:
                    replayed = True
                return {"type": "http.request", "body": block, "more_body": more}

            await self.app(scope, replay, send)
