"""Evidence-aware scoring and bounded iteration; scores never imply acceptance."""
from dataclasses import asdict
from pathlib import Path

import numpy as np

from .common import digest, write_json
from .config import WEIGHTS, options


def hard_checks(rows, audit, assembly_hash=None, junctions=None):
    rigid = True
    for row in rows:
        rotation = np.asarray(row["rotation"]).reshape(3, 3)
        rigid &= bool(np.isfinite(row["position"]).all() and np.isfinite(rotation).all()
            and np.allclose(rotation.T@rotation, np.eye(3), atol=1e-5)
            and np.isclose(np.linalg.det(rotation), 1, atol=1e-5))
    fresh = bool(assembly_hash and audit.get("assembly_sha256") == assembly_hash
                 and audit.get("part_count") == len(rows))
    catalog = audit.get("catalog", {})
    resources = all(all(catalog.get(p["part"]+".dat", {}).get("validity", {}).get(k)
        for k in ("geometry", "connections", "collider")) for p in rows) and bool(rows)
    checks = dict(rigid_transforms=rigid, fresh_evidence=fresh, resources=resources,
        collision_coverage=fresh and audit.get("collision_coverage") == "complete",
        collisions=fresh and audit.get("collision_count") == 0,
        connectivity=fresh and audit.get("connectivity", {}).get("status") == "passed")
    if junctions is not None:
        checks["module_interfaces"] = (junctions.get("assembly_sha256") == assembly_hash
                                       and junctions.get("status") == "passed")
    return dict(passed=all(checks.values()), checks=checks)


def score(components, critical_defects=()):
    for key, value in components.items():
        if key not in WEIGHTS or value is not None and (not np.isfinite(value) or not 0 <= value <= 1):
            raise ValueError("Quality scores must be finite values in 0..1, or unknown")
    measured = {k: v for k, v in components.items() if v is not None}
    coverage = sum(WEIGHTS[k] for k in measured)
    return dict(components={k: components.get(k) for k in WEIGHTS}, evidence_coverage=coverage,
        weighted_score=sum(WEIGHTS[k]*v for k, v in measured.items()),
        critical_defects=list(critical_defects), appearance_accepted=False)


def iterate(initial, propose, evaluate, directory, config=None):
    """Evaluate local candidates, retain the best legal one and stop on stagnation."""
    config = options(config)
    directory = Path(directory)
    best, best_result, history, stale = None, None, [], 0
    current = initial
    for iteration in range(config["max_quality_iterations"]):
        candidates = [current] if iteration == 0 else list(propose(best or current, best_result, iteration))
        if not candidates:
            reason = "candidate_space_exhausted"
            break
        results = [(candidate, evaluate(candidate, iteration)) for candidate in candidates[:16]]
        legal = [(candidate, result) for candidate, result in results if result["hard_checks"]["passed"]]
        old_score = best_result["weighted_score"] if best_result else None
        old_defects = len(best_result["critical_defects"]) if best_result else None
        if legal:
            candidate, result = max(legal, key=lambda pair: (pair[1]["weighted_score"], -len(pair[1]["critical_defects"])))
            if best_result is None or (result["weighted_score"], -len(result["critical_defects"])) > (
                    best_result["weighted_score"], -len(best_result["critical_defects"])):
                best, best_result = candidate, result
        improvement = (best_result["weighted_score"]-old_score)/max(abs(old_score), .01) if old_score is not None else None
        reduced = old_defects is not None and len(best_result["critical_defects"]) < old_defects
        reviewed = any(r.get("status") != "evaluation_incomplete" for _, r in results)
        stale = stale+1 if reviewed and improvement is not None and improvement < .01 and not reduced else 0
        history.append(dict(iteration=iteration+1, candidates=[dict(id=str(c), **r) for c, r in results],
                            best=str(best), relative_improvement=improvement, stagnant_rounds=stale))
        write_json(directory/f"iteration-{iteration+1:02}.json", history[-1])
        if stale >= 3:
            reason = "stagnation_three_rounds"
            break
        current = best or current
    else:
        reason = "iteration_limit"
    report = dict(schema_version=1, best=str(best) if best is not None else None, best_quality=best_result,
        iterations=history, stop_reason=reason, status="not_accepted" if best else "no_legal_candidate",
        delivery_accepted=False, studio_gui_verified=False, physical_build_required=False)
    write_json(directory/"quality-report.json", report)
    return best, report
