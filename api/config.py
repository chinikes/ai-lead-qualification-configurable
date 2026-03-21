import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
"""
GET  /api/config — Fetch current ICP + scoring config
PUT  /api/config — Update ICP + scoring config
"""

from http.server import BaseHTTPRequestHandler
import json
from _db import get_client


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            db = get_client()

            # Fetch ICP config
            icp = db.table("icp_config").select("*").eq("is_active", True).limit(1).execute()
            icp_data = icp.data[0] if icp.data else {}

            # Fetch scoring config
            scoring = db.table("scoring_config").select("*").eq("is_active", True).limit(1).execute()
            scoring_data = scoring.data[0] if scoring.data else {}

            self._json_response(200, {
                "icp": icp_data,
                "scoring": scoring_data,
            })

        except Exception as e:
            self._json_response(500, {"error": str(e)})

    def do_PUT(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length > 0 else {}

            db = get_client()

            # Update ICP config if provided
            icp_updates = body.get("icp")
            if icp_updates:
                # Get the active config ID
                current = db.table("icp_config").select("id").eq("is_active", True).limit(1).execute()
                if current.data:
                    config_id = current.data[0]["id"]
                    icp_updates["updated_at"] = "now()"
                    icp_updates.pop("id", None)
                    icp_updates.pop("is_active", None)
                    db.table("icp_config").update(icp_updates).eq("id", config_id).execute()

            # Update scoring config if provided
            scoring_updates = body.get("scoring")
            if scoring_updates:
                current = db.table("scoring_config").select("id").eq("is_active", True).limit(1).execute()
                if current.data:
                    config_id = current.data[0]["id"]
                    scoring_updates["updated_at"] = "now()"
                    scoring_updates.pop("id", None)
                    scoring_updates.pop("is_active", None)
                    db.table("scoring_config").update(scoring_updates).eq("id", config_id).execute()

            self._json_response(200, {"status": "updated"})

        except Exception as e:
            self._json_response(500, {"error": str(e)})

    def _json_response(self, status: int, data: dict):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
