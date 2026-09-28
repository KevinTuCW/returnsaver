"""Print a short-lived signed Admin URL for one tenant."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from auth import admin_signature


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tenant_id")
    parser.add_argument("--base-url", default="http://localhost:8777/admin")
    parser.add_argument("--ttl", type=int, default=8 * 60 * 60)
    args = parser.parse_args()
    expires = int(time.time()) + args.ttl
    query = urlencode({"tenant_id": args.tenant_id, "expires": expires,
                       "sig": admin_signature(args.tenant_id, expires)})
    print(f"{args.base_url}?{query}")


if __name__ == "__main__":
    main()
