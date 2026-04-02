"""
mock_app.py
───────────
Lightweight mock HTTP server that simulates all real app endpoints.
Runs inside the mock-app Docker container.
Returns realistic 200/201 responses with small latency so k6 scripts pass.
"""

import json
import time
from http.server import HTTPServer, BaseHTTPRequestHandler


class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"[mock-app] {self.address_string()} - {fmt % args}")

    def _respond(self, status=200, body=None):
        payload = json.dumps(body or {"status": "ok"}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        time.sleep(0.02)  # simulate 20ms latency
        p = self.path.split("?")[0]  # strip query string

        if p == "/":
            self._respond(200, {"message": "mock app running"})
        elif p.startswith("/api/products"):
            self._respond(200, {"products": [{"id": 1, "name": "Test Product"}]})
        elif p.startswith("/api/search"):
            self._respond(200, {"results": [{"id": 1, "name": "Result"}]})
        elif p.startswith("/api/wishlist"):
            self._respond(200, {"items": []})
        elif p.startswith("/api/orders"):
            self._respond(200, {"orders": [{"order_id": "order-001", "status": "confirmed"}]})
        else:
            self._respond(200, {"path": self.path})

    def do_POST(self):
        time.sleep(0.03)  # simulate 30ms latency
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)

        if self.path == "/api/login":
            self._respond(200, {"token": "mock-jwt-token", "user": "testuser"})
        elif self.path == "/api/cart":
            self._respond(201, {"cart_id": "cart-001", "items": []})
        elif self.path == "/api/checkout":
            self._respond(201, {"order_id": "order-001", "status": "confirmed"})
        elif self.path == "/api/wishlist":
            self._respond(201, {"wishlist_id": "wish-001"})
        elif self.path == "/api/orders":
            self._respond(201, {"order_id": "order-002", "status": "created"})
        else:
            self._respond(201, {"path": self.path})


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 8080), Handler)
    print("[mock-app] Listening on port 8080")
    server.serve_forever()
