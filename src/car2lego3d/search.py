"""Deterministic bounded beam search over compatible native module candidates."""
from dataclasses import dataclass, field
import math


@dataclass
class Candidate:
    id: str
    module: str
    cost: float
    parameters: dict = field(default_factory=dict)
    interfaces: dict = field(default_factory=dict)
    requires: dict = field(default_factory=dict)
    legal: bool = True
    rejection: str | None = None


def beam_search(groups, width=8, limit=16, compatible=None):
    if not 1 <= width <= 8 or not 1 <= limit <= 16:
        raise ValueError("Beam width <= 8 and candidate limit <= 16 are required")
    beam = [(0., [], {})]
    rejected = []
    for module, candidates in groups.items():
        valid = []
        for c in candidates:
            if c.module != module or not math.isfinite(c.cost):
                raise ValueError("Invalid module candidate")
            if not c.legal:
                rejected.append(dict(id=c.id, reason=c.rejection or "illegal"))
            else:
                valid.append(c)
        valid = sorted(valid, key=lambda c: (c.cost, c.id))[:limit]
        extended = []
        for cost, selected, interfaces in beam:
            for c in valid:
                if any(interfaces.get(k) != v for k, v in c.requires.items()):
                    continue
                if any(k in interfaces and interfaces[k] != v for k, v in c.interfaces.items()):
                    continue
                if compatible and not compatible(selected, c):
                    continue
                extended.append((cost+c.cost, selected+[c], {**interfaces, **c.interfaces}))
        beam = sorted(extended, key=lambda row: (row[0], tuple(c.id for c in row[1])))[:width]
        if not beam:
            raise ValueError(f"No compatible legal candidates for module {module}")
    return beam, rejected


def snap_connector(fixed_position, moving_local_position, rotation):
    """Place a candidate's real connector at a known world connector."""
    import numpy as np
    matrix = np.asarray(rotation).reshape(3, 3)
    if not np.allclose(matrix.T@matrix, np.eye(3)) or not np.isclose(np.linalg.det(matrix), 1):
        raise ValueError("Connector placement requires a rigid rotation")
    return (np.asarray(fixed_position)-matrix@np.asarray(moving_local_position)).tolist()
