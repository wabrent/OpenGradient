import urllib.request
import json

address = "0xfa13a15a2fb420e2313918496b5b05427ed8e31a"
# Try checksummed
# address = "0xFa13a15a2fB420e2313918496b5B05427Ed8e31a"

urls = [
    f"https://base-sepolia.blockscout.com/api/v2/addresses/{address}/transactions?items_count=200",
]

for url in urls:
    print(f"Testing {url}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            print(f"Success! Found {len(data.get('items', []))} items")
    except Exception as e:
        print(f"Failed: {e}")
        if hasattr(e, 'read'):
            print(f"Response: {e.read().decode()}")
