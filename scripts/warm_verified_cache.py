"""Warm the grounded topic cache without producing or uploading content."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from app.services.cache_warmer import warm_verified_cache  # noqa: E402


def main() -> None:
    os.chdir(ROOT)
    load_dotenv()
    data_dir = Path(os.getenv("DATA_DIR", "./data"))
    summary = warm_verified_cache(data_dir)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
