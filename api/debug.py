from http.server import BaseHTTPRequestHandler
import json
import urllib.request
import urllib.parse
import traceback

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            # Simple test - fetch from blockscout directly
            address = "0xfa13a15a2fb420e2313918496b5b05427ed8e31a"
            url = f"https://base-sepolia.blockscout.com/api/v2/addresses/{address}/transactions?size=1"
            
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, method="GET")
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "data": data}).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": str(e), "trace": traceback.format_exc()}).encode())