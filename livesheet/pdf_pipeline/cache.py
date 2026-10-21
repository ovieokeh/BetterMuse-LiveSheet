"""Discard unreadable cache metadata, never the immutable source or evidence.

Cached files are reusable only after their existing content hashes and input
fingerprints match. A malformed metadata file is a cache miss, not a failed job.
"""

import json
from pathlib import Path


def read_cache_object(path):
    try:
        value = json.loads(Path(path).read_text())
    except (FileNotFoundError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}
