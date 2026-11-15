import fcntl
import html
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api import albums, auth, collections, enhancements, library, memories, system
from app.core.config import settings
from app.core.middleware import BoundedRequestBody


@asynccontextmanager
async def lifespan(app):
    with open(settings.data_dir / "service.lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        yield


app = FastAPI(
    title="Xbum · 家庭记忆",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts.split(","))
app.add_middleware(BoundedRequestBody, maximum=(settings.max_upload_mb + 1) * 1024 * 1024)
logger = logging.getLogger("xbum.api")


def error(request, status, message, details=None):
    return JSONResponse(
        {
            "code": f"http_{status}",
            "message": message,
            "details": details or [],
            "request_id": getattr(request.state, "request_id", ""),
        },
        status_code=status,
    )


@app.exception_handler(StarletteHTTPException)
async def http_error(request, exc):
    return error(request, exc.status_code, str(exc.detail))


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Never echo passwords, images, recordings, or free-form input in errors.
    details = [{"field": ".".join(str(p) for p in e["loc"]), "type": e["type"]} for e in exc.errors()]
    return error(request, 422, "输入不符合要求，请检查字段", details)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request.state.request_id = uuid4().hex
    started = time.monotonic()
    maximum = (settings.max_upload_mb + 1) * 1024 * 1024
    if int(request.headers.get("content-length", 0)) > maximum:
        return error(request, 413, "请求文件超过大小限制")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if request.headers.get("sec-fetch-site") == "cross-site" or (
            origin and urlparse(origin).hostname not in settings.trusted_hosts.split(",")
        ):
            return error(request, 403, "不允许跨站点修改数据")
    lock = open(settings.data_dir / "maintenance.lock", "a+")
    await run_in_threadpool(fcntl.flock, lock, fcntl.LOCK_SH)
    try:
        response = await call_next(request)
    except Exception:
        logger.error(json.dumps({"event": "request_failed", "request_id": request.state.request_id}))
        response = error(request, 500, "服务器处理失败，请检查服务和磁盘状态")
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'"
    )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, private"
    logger.info(
        json.dumps(
            {
                "event": "request",
                "request_id": request.state.request_id,
                "status": response.status_code,
                "duration_ms": int((time.monotonic() - started) * 1000),
            }
        )
    )
    return response


for router in (auth.router, library.router, memories.router, system.router, albums.router, collections.router, enhancements.router):
    app.include_router(router, prefix="/api/v1")


@app.get("/api/docs", response_class=HTMLResponse, include_in_schema=False)
def api_documentation():
    schema = app.openapi()
    entries = []
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            content = {k: operation[k] for k in ["parameters", "requestBody", "responses"] if k in operation}
            entries.append(
                f"<details><summary><b>{html.escape(method.upper())}</b> {html.escape(path)} "
                f"<small>{html.escape(operation.get('summary', ''))}</small></summary>"
                f"<pre>{html.escape(json.dumps(content, ensure_ascii=False, indent=2))}</pre></details>"
            )
    return (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Xbum OpenAPI</title>'
        "<style>body{max-width:1100px;margin:40px auto;padding:20px;font:14px system-ui;background:#f8f5ef;color:#453b30}"
        "details{padding:15px;border-bottom:1px solid #ddd1bc}summary{cursor:pointer}b{display:inline-block;min-width:70px;color:#a9583e}"
        "small{margin-left:20px;color:#91836c}pre{overflow:auto;font-size:12px;padding:20px;background:#eee7da}</style>"
        "<h1>Xbum · OpenAPI</h1><p>由当前代码自动生成，完全离线可读。修改请求需要会话 Cookie 和 X-CSRF-Token。</p>"
        '<p><a href="/api/openapi.json">下载 OpenAPI JSON</a> · <a href="/">家庭相册</a></p>'
        + "".join(entries)
        + "<h2>数据契约</h2><pre>"
        + html.escape(json.dumps(schema.get("components", {}), ensure_ascii=False, indent=2))
        + "</pre></html>"
    )


DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


@app.get("/{path:path}", include_in_schema=False)
def frontend(path: str):
    if path.startswith("api/"):
        raise HTTPException(404, "接口不存在")
    candidate = (DIST / path).resolve()
    if not candidate.is_relative_to(DIST.resolve()):
        raise HTTPException(404, "页面不存在")
    if candidate.is_file():
        return FileResponse(candidate)
    if (DIST / "index.html").is_file():
        return FileResponse(DIST / "index.html")
    raise HTTPException(503, "前端尚未构建，请运行 make build")
