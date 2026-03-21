import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
"""
GET /api/analytics — Dashboard metrics and stats.
"""

from http.server import BaseHTTPRequestHandler
import json
from _db import get_client, get_analytics


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            db = get_client()
            stats = get_analytics(db)
            self._json_response(200, stats)
        except Exception as e:
            self._json_response(500, {"error": str(e)})

    def _json_response(self, status: int, data: dict):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
