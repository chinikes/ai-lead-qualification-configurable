import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
"""
GET /api/debug — TEMPORARY diagnostic endpoint.

Reports installed package versions and isolates which stage of Supabase
client construction fails. Delete this file once the issue is resolved.
"""

from http.server import BaseHTTPRequestHandler
import json
import traceback
import platform


def _versions():
    """Report installed versions of the packages that matter."""
    out = {}
    try:
        from importlib.metadata import version, PackageNotFoundError
        for pkg in [
            "supabase", "postgrest", "realtime", "storage3",
            "supabase_auth", "gotrue", "supafunc",
            "httpx", "pydantic", "python-dotenv", "websockets",
        ]:
            try:
                out[pkg] = version(pkg)
            except PackageNotFoundError:
                out[pkg] = "not installed"
            except Exception as e:
                out[pkg] = f"error: {e}"
    except Exception as e:
        out["_error"] = str(e)
    return out


def _env_presence():
    """Confirm env vars exist WITHOUT leaking their values."""
    keys = [
        "SUPABASE_URL", "SUPABASE_SERVICE_KEY",
        "ANTHROPIC_API_KEY", "HUNTER_API_KEY", "PDL_API_KEY",
    ]
    return {k: ("set" if os.environ.get(k) else "MISSING") for k in keys}


def _filesystem_probe():
    """EBUSY is an OS-level error — check what this sandbox actually allows."""
    probe = {}
    probe["cwd"] = os.getcwd()
    probe["tmp_writable"] = os.access("/tmp", os.W_OK)
    probe["dev_shm_exists"] = os.path.exists("/dev/shm")
    try:
        p = "/tmp/_probe_write_test"
        with open(p, "w") as f:
            f.write("ok")
        os.remove(p)
        probe["tmp_write_test"] = "ok"
    except Exception as e:
        probe["tmp_write_test"] = f"{type(e).__name__}: {e}"
    # POSIX semaphore creation is a classic EBUSY source on serverless
    try:
        import multiprocessing
        lock = multiprocessing.Lock()
        probe["mp_lock"] = "ok"
    except Exception as e:
        probe["mp_lock"] = f"{type(e).__name__}: {e}"
    return probe


def _import_probe():
    """Find out whether the failure is at import time or construction time."""
    stages = {}
    try:
        import supabase
        stages["import_supabase"] = "ok"
    except Exception as e:
        stages["import_supabase"] = f"{type(e).__name__}: {e}"
        stages["import_supabase_trace"] = traceback.format_exc().splitlines()[-15:]
        return stages

    try:
        from supabase import create_client
        stages["import_create_client"] = "ok"
    except Exception as e:
        stages["import_create_client"] = f"{type(e).__name__}: {e}"
        return stages

    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        stages["create_client"] = "skipped — env vars missing"
        return stages

    try:
        client = create_client(url, key)
        stages["create_client"] = "ok"
    except Exception as e:
        stages["create_client"] = f"{type(e).__name__}: {e}"
        stages["create_client_trace"] = traceback.format_exc().splitlines()[-25:]
        return stages

    # Construction succeeded — try an actual query
    try:
        result = client.table("leads").select("id").limit(1).execute()
        stages["query"] = f"ok — {len(result.data or [])} row(s)"
    except Exception as e:
        stages["query"] = f"{type(e).__name__}: {e}"
        stages["query_trace"] = traceback.format_exc().splitlines()[-25:]

    return stages


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        report = {
            "python": sys.version,
            "platform": platform.platform(),
            "env": _env_presence(),
            "versions": _versions(),
            "filesystem": _filesystem_probe(),
            "stages": _import_probe(),
        }
        body = json.dumps(report, indent=2, default=str).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
