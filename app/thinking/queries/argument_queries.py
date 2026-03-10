def counters_by_argument(arguments, counters):
    argument_ids = {argument.id for argument in arguments}
    counters = [
        counter for counter in counters if counter.target_argument_id in argument_ids
    ]

    by_parent = {}
    for counter in counters:
        by_parent.setdefault(counter.parent_counter_id, []).append(counter)

    for counter in counters:
        counter.rebuttal_children = by_parent.get(counter.id, [])

    root_counters = by_parent.get(None, [])
    return {
        argument_id: [
            counter
            for counter in root_counters
            if counter.target_argument_id == argument_id
        ]
        for argument_id in argument_ids
    }


def flatten_counters(counter_roots):
    stack = list(counter_roots)
    flattened = []
    while stack:
        current = stack.pop()
        flattened.append(current)
        children = list(getattr(current, "rebuttal_children", []))
        stack.extend(reversed(children))
    return flattened
