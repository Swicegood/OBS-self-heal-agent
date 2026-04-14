"""Minimal OBS WebSocket v5 client (stdlib crypto + websocket-client)."""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from typing import Any

import websocket

from obs_self_heal.config import ObsConfig

# websocket-client connection object (typing varies by version)
WsConn = Any


def _auth_string(password: str, salt: str, challenge: str) -> str:
    secret = hashlib.sha256((password + salt).encode("utf-8")).digest()
    b64_secret = base64.b64encode(secret).decode("utf-8")
    combined = (b64_secret + challenge).encode("utf-8")
    return base64.b64encode(hashlib.sha256(combined).digest()).decode("utf-8")


class ObsWebSocketV5:
    """Connect, Identify, and issue Request (op 6) / read RequestResponse (op 7)."""

    def __init__(self, cfg: ObsConfig) -> None:
        self._cfg = cfg
        self._ws: WsConn | None = None

    def __enter__(self) -> ObsWebSocketV5:
        url = f"ws://{self._cfg.host}:{self._cfg.port}"
        self._ws = websocket.create_connection(url, timeout=self._cfg.timeout_sec)
        hello_raw = self._ws.recv()
        hello = json.loads(hello_raw)
        if hello.get("op") != 0:
            raise RuntimeError(f"expected Hello op 0, got {hello!r}")
        hello_data = hello.get("d") or {}
        rpc_version = int(hello_data.get("rpcVersion", 1))
        auth_payload = hello_data.get("authentication")
        identify: dict[str, Any] = {"rpcVersion": rpc_version}
        if auth_payload and self._cfg.password:
            identify["authentication"] = _auth_string(
                self._cfg.password,
                str(auth_payload["salt"]),
                str(auth_payload["challenge"]),
            )
        self._send({"op": 1, "d": identify})
        while True:
            identified_raw = self._ws.recv()
            identified = json.loads(identified_raw)
            if identified.get("op") == 2:
                break
            if identified.get("op") not in (5,):  # ignore events; extend if needed
                raise RuntimeError(f"unexpected message during Identify: {identified!r}")
        return self

    def __exit__(self, *exc: object) -> None:
        if self._ws:
            self._ws.close()
            self._ws = None

    def _send(self, payload: dict[str, Any]) -> None:
        assert self._ws is not None
        self._ws.send(json.dumps(payload))

    def request(self, request_type: str, request_data: dict[str, Any] | None = None) -> dict[str, Any]:
        assert self._ws is not None
        rid = str(uuid.uuid4())
        self._send(
            {
                "op": 6,
                "d": {
                    "requestType": request_type,
                    "requestId": rid,
                    **({"requestData": request_data} if request_data is not None else {}),
                },
            }
        )
        while True:
            raw = self._ws.recv()
            msg = json.loads(raw)
            if msg.get("op") != 7:
                continue
            d = msg.get("d") or {}
            if d.get("requestId") != rid:
                continue
            status = d.get("requestStatus") or {}
            if not status.get("result", False):
                code = status.get("code")
                comment = status.get("comment", "")
                raise RuntimeError(f"OBS request failed: {request_type} code={code} comment={comment}")
            return d.get("responseData") or {}
