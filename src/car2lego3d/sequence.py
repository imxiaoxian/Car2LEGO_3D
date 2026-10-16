"""Connected build prefixes from native connector evidence."""
from dataclasses import replace


def resequence(car, audit, limit):
    """Order attachments after a foundation, preserving standard part geometry."""
    if limit < 1 or audit["part_count"] != len(car.parts):
        raise ValueError("Invalid build sequence input")
    bounds = audit["per_part_bounds_ldu"]
    graph = [set() for _ in car.parts]
    for edge in audit["connections"]:
        a, b = int(edge["a"][1:]), int(edge["b"][1:])
        graph[a].add(b)
        graph[b].add(a)
    foundations = [i for i, part in enumerate(car.parts) if part.module == "body and chassis"]
    if not foundations:
        raise ValueError("No chassis foundation")
    def priority(i):
        low, high = bounds[i]
        area = (high[0]-low[0])*(high[2]-low[2])
        return -high[1], -area, i
    seed = min(foundations, key=priority)
    order, reached, parents = [seed], {seed}, {seed: None}
    frontier = set(graph[seed])
    while frontier:
        # Lowest top surface first leaves glazing and roof until their supports exist.
        chosen = min(frontier, key=lambda i: (-bounds[i][0][1],
                     car.parts[i].module != car.parts[order[-1]].module, car.parts[i].step, i))
        parents[chosen] = min(graph[chosen] & reached, key=order.index)
        frontier.remove(chosen)
        order.append(chosen)
        reached.add(chosen)
        frontier.update(graph[chosen]-reached)
    if len(order) != len(car.parts):
        raise ValueError("Disconnected parts cannot form a connected build sequence")
    indices = {old: new for new, old in enumerate(order)}
    parts, step, count, module = [], 0, 0, None
    for old in order:
        part = car.parts[old]
        if module != part.module or count == limit:
            step, count, module = step+1, 0, part.module
        parts.append(replace(part, step=step))
        count += 1
    car.parts = parts
    return dict(method="native connector graph with chassis foundation and lower surfaces first",
                part_count=len(parts), step_count=step, connected_prefixes=True,
                attachments=[dict(part_index=indices[i], parent_index=indices[parents[i]]
                    if parents[i] is not None else None) for i in order],
                insertion_paths_verified=False, physical_verified=False,
                limitations=["Connectivity proves an attachment exists, not an accessible insertion path or clutch strength."])
