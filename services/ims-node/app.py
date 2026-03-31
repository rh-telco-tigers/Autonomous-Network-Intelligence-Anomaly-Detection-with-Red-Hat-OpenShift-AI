import os
import socketserver
import threading
import time
from collections import deque
from typing import Deque, Dict, List

from fastapi import FastAPI

from shared.metrics import install_metrics


class TelemetryState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with getattr(self, "lock", threading.Lock()):
            self.started_at = time.time()
            self.method_counts = {"REGISTER": 0, "INVITE": 0, "BYE": 0}
            self.response_4xx = 0
            self.response_5xx = 0
            self.retransmissions = 0
            self.payload_sizes: List[int] = []
            self.latencies_ms: List[float] = []
            self.recent_messages: Deque[Dict[str, object]] = deque(maxlen=15)

    def record(self, message: str, protocol: str) -> None:
        first_line = message.splitlines()[0].strip() if message.splitlines() else ""
        method = first_line.split(" ", 1)[0].upper() if first_line else "UNKNOWN"
        response_code = 488 if "MALFORMED" in message.upper() else 200
        if method not in self.method_counts:
            method = "INVITE" if "INVITE" in message.upper() else "REGISTER"
        payload_size = len(message.encode("utf-8"))
        retransmission = "RETRANS" in message.upper()
        latency_ms = 22.0
        if method == "REGISTER":
            latency_ms = 95.0 if retransmission else 34.0
        elif method == "INVITE":
            latency_ms = 180.0 if response_code >= 400 else 58.0

        with self.lock:
            self.method_counts[method] = self.method_counts.get(method, 0) + 1
            if 400 <= response_code < 500:
                self.response_4xx += 1
            if response_code >= 500:
                self.response_5xx += 1
            if retransmission:
                self.retransmissions += 1
            self.payload_sizes.append(payload_size)
            self.latencies_ms.append(latency_ms)
            self.recent_messages.appendleft(
                {
                    "protocol": protocol,
                    "method": method,
                    "response_code": response_code,
                    "latency_ms": latency_ms,
                    "payload_size": payload_size,
                    "retransmission": retransmission,
                    "timestamp": time.time(),
                }
            )

    def snapshot(self) -> Dict[str, object]:
        with self.lock:
            duration_seconds = max(time.time() - self.started_at, 1.0)
            total_messages = max(sum(self.method_counts.values()), 1)
            payload_variance = 0.0
            if self.payload_sizes:
                payload_variance = max(self.payload_sizes) - min(self.payload_sizes)
            latency_p95 = max(self.latencies_ms) if self.latencies_ms else 0.0
            return {
                "node_id": NODE_ID,
                "node_role": NODE_ROLE,
                "sip_port": SIP_PORT,
                "duration_seconds": round(duration_seconds, 2),
                "message_count": sum(self.method_counts.values()),
                "register_rate": round(self.method_counts.get("REGISTER", 0) / duration_seconds, 2),
                "invite_rate": round(self.method_counts.get("INVITE", 0) / duration_seconds, 2),
                "bye_rate": round(self.method_counts.get("BYE", 0) / duration_seconds, 2),
                "error_4xx_ratio": round(self.response_4xx / total_messages, 3),
                "error_5xx_ratio": round(self.response_5xx / total_messages, 3),
                "latency_p95": round(latency_p95, 2),
                "latency_mean": round(sum(self.latencies_ms) / len(self.latencies_ms), 2) if self.latencies_ms else 0.0,
                "retransmission_count": self.retransmissions,
                "inter_arrival_mean": round(duration_seconds / total_messages, 3),
                "payload_variance": round(payload_variance, 2),
                "recent_messages": list(self.recent_messages),
            }


NODE_ID = os.getenv("IMS_NODE_ID", "pcscf-1")
NODE_ROLE = os.getenv("IMS_NODE_ROLE", "P-CSCF")
SIP_PORT = int(os.getenv("SIP_PORT", "5060"))
STATE = TelemetryState()


def parse_sip_request(message: str) -> tuple[str, Dict[str, str]]:
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    request_line = lines[0] if lines else ""
    headers: Dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return request_line, headers


def sip_response(message: str) -> bytes:
    request_line, headers = parse_sip_request(message)
    method = request_line.split(" ", 1)[0].upper() if request_line else "REGISTER"
    status_code = "488 Not Acceptable Here" if "MALFORMED" in message.upper() else "200 OK"
    via = headers.get("via", f"SIP/2.0/UDP {NODE_ID}:{SIP_PORT}")
    from_header = headers.get("from", f"<sip:{NODE_ID}@{NODE_ID}>")
    to_header = headers.get("to", f"<sip:{NODE_ID}@{NODE_ID}>")
    if "tag=" not in to_header.lower():
        to_header = f"{to_header};tag=ims-demo"
    call_id = headers.get("call-id", f"{NODE_ID}-call")
    cseq = headers.get("cseq", f"1 {method}")
    contact = headers.get("contact", f"<sip:{NODE_ID}@{NODE_ID}:{SIP_PORT}>")

    response = [
        f"SIP/2.0 {status_code}",
        f"Via: {via}",
        f"From: {from_header}",
        f"To: {to_header}",
        f"Call-ID: {call_id}",
        f"CSeq: {cseq}",
        f"Contact: {contact}",
        "Server: ims-demo-node",
        "Content-Length: 0",
        "",
        "",
    ]
    return "\r\n".join(response).encode("utf-8")


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True


class ThreadedUDPServer(socketserver.ThreadingMixIn, socketserver.UDPServer):
    allow_reuse_address = True


class SIPUDPHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        payload, sock = self.request
        message = payload.decode("utf-8", errors="ignore")
        STATE.record(message, "udp")
        sock.sendto(sip_response(message), self.client_address)


class SIPTCPHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        payload = self.request.recv(8192)
        message = payload.decode("utf-8", errors="ignore")
        STATE.record(message, "tcp")
        self.request.sendall(sip_response(message))


def start_socket_server(server: socketserver.BaseServer) -> None:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()


app = FastAPI(title="ims-node", version="0.1.0")
install_metrics(app, "ims-node")


@app.on_event("startup")
def startup() -> None:
    start_socket_server(ThreadedUDPServer(("0.0.0.0", SIP_PORT), SIPUDPHandler))
    start_socket_server(ThreadedTCPServer(("0.0.0.0", SIP_PORT), SIPTCPHandler))


@app.get("/healthz")
def healthz():
    return {"status": "ok", "node_id": NODE_ID, "node_role": NODE_ROLE, "sip_port": SIP_PORT}


@app.get("/telemetry")
def telemetry():
    return STATE.snapshot()


@app.post("/reset")
def reset():
    STATE.reset()
    return {"status": "reset", "node_id": NODE_ID}
