import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "debug_trace.py"
SPEC = importlib.util.spec_from_file_location("shared_debug_trace", MODULE_PATH)
assert SPEC and SPEC.loader
debug_trace = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(debug_trace)


class DebugTraceTests(unittest.TestCase):
    def test_make_trace_packet_coerces_payloads_to_json_safe_values(self) -> None:
        packet = debug_trace.make_trace_packet(
            "api",
            "event",
            title="Example packet",
            service="control-plane",
            payload={"value": object()},
            metadata={"items": [1, object()]},
        )

        self.assertEqual(packet["category"], "api")
        self.assertEqual(packet["phase"], "event")
        self.assertIsInstance(packet["timestamp"], str)
        self.assertIsInstance(packet["payload"]["value"], str)
        self.assertIsInstance(packet["metadata"]["items"][1], str)

    def test_interaction_trace_packets_returns_request_and_response_packets(self) -> None:
        packets = debug_trace.interaction_trace_packets(
            category="model",
            service="anomaly-service",
            target="predictive-service",
            method="POST",
            endpoint="http://predictive/v2/models/demo/infer",
            request_payload={"inputs": []},
            response_payload={"outputs": []},
            request_timestamp="2026-04-06T00:00:00+00:00",
            response_timestamp="2026-04-06T00:00:01+00:00",
            metadata={"model_name": "demo"},
        )

        self.assertEqual(len(packets), 2)
        self.assertEqual(packets[0]["phase"], "request")
        self.assertEqual(packets[1]["phase"], "response")
        self.assertEqual(packets[0]["endpoint"], "http://predictive/v2/models/demo/infer")
        self.assertEqual(packets[1]["metadata"]["model_name"], "demo")


if __name__ == "__main__":
    unittest.main()
