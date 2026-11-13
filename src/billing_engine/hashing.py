"""Canonical serialisation, content hashing and deterministic ids.

Anything that must be reproducible (invoice fingerprints, ledger hash chain,
generated ids) goes through here, so there is exactly one definition of
"the same data".
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum
from fractions import Fraction
from typing import Any

_NAMESPACE = uuid.UUID("6f1c2f0e-8a52-4e0b-9a43-5b1c0d8e7a11")


def _default(o: Any) -> Any:
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return dataclasses.asdict(o)
    if isinstance(o, Decimal):
        return format(o.normalize(), "f")
    if isinstance(o, Fraction):
        return f"{o.numerator}/{o.denominator}"
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, Enum):
        return o.value
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    raise TypeError(f"cannot canonicalise {type(o).__name__}")


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, default=_default, sort_keys=True, separators=(",", ":"))


def fingerprint(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode()).hexdigest()


def deterministic_id(kind: str, *parts: object) -> str:
    """Stable id derived from its inputs (UUIDv5); same inputs -> same id."""
    return f"{kind}_{uuid.uuid5(_NAMESPACE, '|'.join(str(p) for p in parts)).hex}"
