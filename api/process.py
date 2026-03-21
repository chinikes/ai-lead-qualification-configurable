import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
"""
POST /api/process — Run the enrichment → AI → scoring pipeline on a lead.
Body: { "lead_id": "uuid" }
"""

from http.server import BaseHTTPRequestHandler
import json
import asyncio
from _db import get_client, get_lead
from _pipeline import run_pipeline


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length > 0 else {}
            lead_id = body.get("lead_id")

            if not lead_id:
                self._json_response(400, {"error": "lead_id is required"})
                return

            db = get_client()
            lead = get_lead(db, lead_id)
            if not lead:
                self._json_response(404, {"error": "Lead not found"})
                return

            # Run the async pipeline
            result = asyncio.run(run_pipeline(lead))

            self._json_response(200, {
                "status": "processed",
                "lead_id": lead_id,
                "composite_score": result.get("composite_score"),
                "temperature": result.get("temperature"),
                "decision": result.get("decision"),
                "decision_reasoning": result.get("decision_reasoning"),
                "firmographic_score": result.get("firmographic_score"),
                "demographic_score": result.get("demographic_score"),
                "behavioral_score": result.get("behavioral_score"),
                "ai_fit_score": result.get("ai_fit_score"),
                "ai_company_summary": result.get("ai_company_summary"),
                "ai_icp_fit_score": result.get("ai_icp_fit_score"),
                "ai_buying_signals": result.get("ai_buying_signals"),
                "ai_pain_points": result.get("ai_pain_points"),
                "ai_talking_points": result.get("ai_talking_points"),
                "ai_urgency": result.get("ai_urgency"),
                "ai_confidence": result.get("ai_confidence"),
                "overall_confidence": result.get("overall_confidence"),
                "company_industry": result.get("company_industry"),
                "company_employee_count": result.get("company_employee_count"),
                "company_revenue": result.get("company_revenue"),
            })

        except Exception as e:
            self._json_response(500, {"error": str(e)})

    def _json_response(self, status: int, data: dict):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
