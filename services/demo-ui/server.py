import json
import os
import ssl
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


APP_ROOT = Path(__file__).resolve().parent
DEFAULT_CONTROL_PLANE_PROXY_URL = "http://control-plane.ims-demo-lab.svc.cluster.local:8080"
DEFAULT_PORT = 8080
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _control_plane_proxy_url() -> str:
    return os.getenv("CONTROL_PLANE_PROXY_URL", DEFAULT_CONTROL_PLANE_PROXY_URL).rstrip("/")


def _upstream_url(path: str) -> str:
    parsed = urlsplit(path)
    if parsed.path == "/api":
        upstream_path = "/"
    elif parsed.path.startswith("/api/"):
        upstream_path = parsed.path.removeprefix("/api")
    else:
        raise ValueError(f"Unsupported proxy path: {parsed.path}")

    upstream = urlsplit(_control_plane_proxy_url())
    return urlunsplit((upstream.scheme, upstream.netloc, upstream_path or "/", parsed.query, ""))


def _ssl_context_for(url: str):
    if urlsplit(url).scheme != "https" or not _env_flag("CONTROL_PLANE_SKIP_TLS_VERIFY", False):
        return None
    return ssl._create_unverified_context()


class DemoUiHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(APP_ROOT), **kwargs)

    def do_GET(self):
        if self._should_proxy():
            self._proxy()
            return
        super().do_GET()

    def do_HEAD(self):
        if self._should_proxy():
            self._proxy(send_body=False)
            return
        super().do_HEAD()

    def do_POST(self):
        self._proxy_or_405()

    def do_PUT(self):
        self._proxy_or_405()

    def do_PATCH(self):
        self._proxy_or_405()

    def do_DELETE(self):
        self._proxy_or_405()

    def send_head(self):
        path = self.translate_path(self.path)
        if Path(path).exists():
            return super().send_head()

        original_path = self.path
        self.path = "/index.html"
        try:
            return super().send_head()
        finally:
            self.path = original_path

    def _should_proxy(self) -> bool:
        path = urlsplit(self.path).path
        return path == "/api" or path.startswith("/api/")

    def _proxy_or_405(self) -> None:
        if self._should_proxy():
            self._proxy()
            return
        self.send_error(405, "Method not allowed")

    def _proxy(self, *, send_body: bool = True) -> None:
        try:
            target_url = _upstream_url(self.path)
            request = Request(
                target_url,
                data=self._request_body(),
                headers=self._upstream_request_headers(),
                method=self.command,
            )
            with urlopen(request, timeout=30, context=_ssl_context_for(target_url)) as response:
                payload = response.read()
                self.send_response(response.status)
                self._write_response_headers(response.headers.items(), content_length=len(payload))
                self.end_headers()
                if send_body and payload:
                    self.wfile.write(payload)
        except HTTPError as exc:
            payload = exc.read()
            self.send_response(exc.code)
            self._write_response_headers(exc.headers.items(), content_length=len(payload))
            self.end_headers()
            if send_body and payload:
                self.wfile.write(payload)
        except URLError as exc:
            self._write_json_error(
                502,
                f"Demo UI proxy could not reach the control-plane API: {exc.reason}",
                send_body=send_body,
            )
        except Exception as exc:  # noqa: BLE001
            self._write_json_error(500, f"Demo UI proxy failed: {exc}", send_body=send_body)

    def _request_body(self) -> bytes | None:
        content_length = int(self.headers.get("Content-Length", "0") or 0)
        if content_length <= 0:
            return None
        return self.rfile.read(content_length)

    def _upstream_request_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        for name, value in self.headers.items():
            lower_name = name.lower()
            if lower_name in HOP_BY_HOP_HEADERS or lower_name in {"host", "content-length"}:
                continue
            headers[name] = value
        headers["X-Forwarded-Host"] = self.headers.get("Host", "")
        headers["X-Forwarded-Proto"] = self.headers.get("X-Forwarded-Proto", "http")
        return headers

    def _write_response_headers(self, headers, *, content_length: int) -> None:
        sent_content_length = False
        for name, value in headers:
            lower_name = str(name).lower()
            if lower_name in HOP_BY_HOP_HEADERS:
                continue
            if lower_name == "content-length":
                sent_content_length = True
            self.send_header(name, value)
        if not sent_content_length:
            self.send_header("Content-Length", str(content_length))

    def _write_json_error(self, status_code: int, detail: str, *, send_body: bool) -> None:
        payload = json.dumps({"detail": detail}).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if send_body:
            self.wfile.write(payload)


def main() -> None:
    port = int(os.getenv("PORT", str(DEFAULT_PORT)))
    server = ThreadingHTTPServer(("0.0.0.0", port), DemoUiHandler)
    print(f"demo-ui listening on :{port}, proxying /api to {_control_plane_proxy_url()}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
