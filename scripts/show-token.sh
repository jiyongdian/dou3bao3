#!/usr/bin/env bash
set -euo pipefail
python3 - <<'PY'
import json
from pathlib import Path

path = Path("/var/lib/dola-fetch-service/config.json")
data = json.loads(path.read_text(encoding="utf-8"))
print(data.get("api_token", ""))
PY
