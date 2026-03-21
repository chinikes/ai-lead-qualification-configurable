import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
"""
POST /api/leads — Create a new lead
GET  /api/leads — List all leads with scores
"""

from http.server import BaseHTTPRequestHandler
import json
import re
from urllib.parse import urlparse, parse_qs
from _db import get_client, create_lead, list_leads, log_activity

FREE_EMAILS = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com", "icloud.com"}


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        """Intake a new lead."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length > 0 else {}

            email = (body.get("email") or "").strip().lower() or None
            if email and not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
                email = None

            domain = (body.get("company_domain") or "").strip().lower() or None
            if domain:
                domain = re.sub(r"^(https?://)?(www\.)?", "", domain).rstrip("/")
            elif email:
                domain = email.split("@")[1]

            lead_data = {
                "email": email,
                "first_name": body.get("first_name"),
                "last_name": body.get("last_name"),
                "company_name": body.get("company_name"),
                "company_domain": domain,
                "job_title": body.get("job_title"),
                "phone": body.get("phone"),
                "message": body.get("message"),
                "source": body.get("source", "web_form"),
                "utm_source": body.get("utm_source"),
                "utm_medium": body.get("utm_medium"),
                "utm_campaign": body.get("utm_campaign"),
                "page_url": body.get("page_url"),
                "status": "new",
            }

            db = get_client()
            lead = create_lead(db, lead_data)
            log_activity(db, "intake", f"New lead: {email or domain or 'unknown'}", lead.get("id"))

            self._json_response(201, {"status": "created", "lead": lead})

        except Exception as e:
            self._json_response(500, {"error": str(e)})

    def do_GET(self):
        """List leads with optional filters."""
        try:
            params = parse_qs(urlparse(self.path).query)
            limit = int(params.get("limit", [50])[0])
            offset = int(params.get("offset", [0])[0])
            status = params.get("status", [None])[0]
            decision = params.get("decision", [None])[0]

            db = get_client()
            leads = list_leads(db, limit=limit, offset=offset, status=status, decision=decision)

            self._json_response(200, {"leads": leads, "count": len(leads)})

        except Exception as e:
            self._json_response(500, {"error": str(e)})

    def _json_response(self, status: int, data: dict):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

