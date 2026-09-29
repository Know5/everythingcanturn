"""Web UI 服务端集成测试：起真实 HTTP 服务，不 mock 请求处理。

重点是安全边界——token、Origin、Host 三道校验各自拦什么，以及它们
组合起来能不能挡住 DNS rebinding。
"""

from __future__ import annotations

import http.client
import json
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from PIL import Image

from convcore.webui.server import UiState, make_handler


@pytest.fixture
def ui():
    """起一个随机端口的服务，返回 (state, port)。"""
    state = UiState()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield state, httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


def _req(
    port: int,
    method: str,
    path: str,
    *,
    host: str | None = None,
    headers: dict[str, str] | None = None,
    body: dict | None = None,
) -> tuple[int, str]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"Host": host or f"127.0.0.1:{port}", **(headers or {})}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        h["Content-Type"] = "application/json"
        h["Content-Length"] = str(len(data))
    try:
        conn.request(method, path, body=data, headers=h)
        resp = conn.getresponse()
        return resp.status, resp.read().decode("utf-8", "replace")
    finally:
        conn.close()


@pytest.fixture
def png(tmp_path: Path) -> Path:
    p = tmp_path / "red.png"
    Image.new("RGB", (32, 32), (200, 30, 40)).save(p)
    return p


# ── Host 校验（DNS rebinding） ──


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "LOCALHOST"])
def test_index_served_on_loopback_host(ui, host):
    state, port = ui
    code, html = _req(port, "GET", "/", host=f"{host}:{port}")
    assert code == 200
    assert state.token in html


def test_rebinding_host_cannot_fetch_token(ui):
    state, port = ui
    code, html = _req(port, "GET", "/", host=f"attacker.example:{port}")
    assert code == 403
    assert state.token not in html


def test_rebinding_host_cannot_call_api_even_with_token(ui, tmp_path):
    """rebinding 后 Origin 与 Host 一致，只有 Host 校验能拦住。"""
    state, port = ui
    evil = f"attacker.example:{port}"
    code, _ = _req(
        port,
        "POST",
        "/api/out-dir",
        host=evil,
        headers={"Origin": f"http://{evil}", "X-Token": state.token},
        body={"path": str(tmp_path)},
    )
    assert code == 403
    assert state.out_dir is None


def test_wrong_port_in_host_rejected(ui):
    state, port = ui
    code, _ = _req(
        port, "GET", "/api/env", host=f"127.0.0.1:{port + 1}",
        headers={"X-Token": state.token},
    )
    assert code == 403


def test_missing_host_rejected(ui):
    _, port = ui
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.putrequest("GET", "/", skip_host=True)
        conn.endheaders()
        assert conn.getresponse().status == 403
    finally:
        conn.close()


# ── token / Origin ──


def test_api_requires_token(ui):
    _, port = ui
    assert _req(port, "GET", "/api/env")[0] == 403
    assert _req(port, "POST", "/api/inspect", body={"path": "x"})[0] == 403


def test_cross_site_origin_rejected(ui, png):
    state, port = ui
    code, _ = _req(
        port,
        "POST",
        "/api/inspect",
        headers={"Origin": "http://evil.example", "X-Token": state.token},
        body={"path": str(png)},
    )
    assert code == 403


def test_static_path_traversal_rejected(ui):
    _, port = ui
    code, text = _req(port, "GET", "/static/../server.py")
    assert code == 403
    assert "make_handler" not in text


# ── 正常路径 ──


def test_env_with_token(ui):
    state, port = ui
    code, text = _req(port, "GET", "/api/env", headers={"X-Token": state.token})
    assert code == 200
    assert json.loads(text)["writable"] > 0


def test_inspect_lists_targets(ui, png):
    state, port = ui
    code, text = _req(
        port, "POST", "/api/inspect",
        headers={"X-Token": state.token}, body={"path": str(png)},
    )
    assert code == 200
    data = json.loads(text)
    assert data["mime"] == "image/png"
    assert any(t["mime"] == "image/webp" for t in data["targets"])


def test_convert_end_to_end(ui, png):
    state, port = ui
    auth = {"X-Token": state.token}
    code, text = _req(
        port, "POST", "/api/convert", headers=auth,
        body={"items": [{"path": str(png), "target": "webp"}]},
    )
    assert code == 200
    ids = json.loads(text)["ids"]

    deadline = time.monotonic() + 30
    while True:
        _, text = _req(port, "POST", "/api/progress", headers=auth, body={"ids": ids})
        job = json.loads(text)["jobs"][0]
        if job["state"] in ("done", "failed", "cancelled") or time.monotonic() > deadline:
            break
        time.sleep(0.05)

    assert job["state"] == "done", job
    out = Path(job["outPath"])
    assert out.is_file() and out.suffix == ".webp"
