#!/usr/bin/env bash
set -euo pipefail
python3 - <<'PY'
import json
import secrets
from pathlib import Path

path = Path("/var/lib/dola-fetch-service/config.json")
data = json.loads(path.read_text(encoding="utf-8"))
data["api_token"] = secrets.token_urlsafe(32)
path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print(data["api_token"])
PY
systemctl restart dola-fetch-service
