"""Derived narrative ranges for storylines, independent of story-time occurrence."""
from __future__ import annotations

from bisect import bisect_right


def _geometry(snapshot: dict) -> tuple[dict, dict, dict, dict]:
    nodes = {row['id']: row for row in snapshot.get('nodes', []) if not row.get('retracted')}
    objects = {row['id']: row for row in snapshot.get('objects', [])}
    contexts = {key: row for key, row in objects.items() if row.get('kind') == 'context'}
    orders_by_context = {}
    for row in snapshot.get('source_orders', []):
        orders_by_context.setdefault(row.get('context_id', 'canon'), {})[row['source_id']] = row['ordinal']
    context_orders = {}
    def orders(context):
        if context not in context_orders:
            result, seen, current = {}, set(), context
            while current and current not in seen:
                seen.add(current)
                for source, ordinal in orders_by_context.get(current, {}).items():
                    result.setdefault(source, ordinal)
                current = contexts.get(current, {}).get('parent_id')
            context_orders[context] = result
        return context_orders[context]
    coordinates = {}
    own = {}
    for identifier, node in nodes.items():
        context = node.get('context_id', 'canon')
        candidates = []
        disclosure = node.get('disclosure')
        if isinstance(disclosure, dict):
            candidates.append(disclosure)
        if not candidates:
            candidates.extend(node.get('evidence_spans') or [])
        located = []
        for point in candidates:
            source, start = point.get('source_id'), point.get('start')
            ordinal = orders(context).get(source)
            if ordinal is not None and type(start) is int and start >= 0:
                position = (ordinal, start)
                coordinates[(context, position)] = {'source_id': source, 'start': start}
                located.append(position)
        own[identifier] = min(located) if located else None
    # Postorder traversal is iterative; shared and deeply nested containers do
    # not require an assumed number of structural levels.
    bounds, visiting = {}, set()
    for root in nodes:
        stack = [(root, False)]
        while stack:
            identifier, leaving = stack.pop()
            if identifier in bounds:
                continue
            node = nodes[identifier]
            children = [child for child in node.get('events') or [] if child in nodes
                        and nodes[child].get('context_id', 'canon') == node.get('context_id', 'canon')]
            if not leaving:
                if identifier in visiting:
                    continue  # The shared containment validator reports cycles.
                visiting.add(identifier)
                stack.append((identifier, True))
                stack.extend((child, False) for child in children if child not in visiting)
                continue
            positions = [own[identifier]] if own[identifier] is not None else []
            for child in children:
                if child in bounds and bounds[child] is not None:
                    positions.extend(bounds[child])
            bounds[identifier] = (min(positions), max(positions)) if positions else None
            visiting.discard(identifier)
    return nodes, coordinates, own, bounds


def reading_starts(snapshot: dict) -> dict[str, dict]:
    """Earliest known member position; no inferred IDs or evidence quote ends."""
    nodes, coordinates, _own, bounds = _geometry(snapshot)
    return {identifier: coordinates[(nodes[identifier].get('context_id', 'canon'), bound[0])]
            for identifier, bound in bounds.items() if bound is not None}


def analyze(snapshot: dict) -> tuple[list[dict], list[dict]]:
    """Major stories partition a context; big events partition their parent story."""
    nodes, coordinates, own, bounds = _geometry(snapshot)
    issues, spans = [], []
    source_ordinals = {(context, point['source_id']): position[0]
                       for (context, position), point in coordinates.items()}
    source_ordinals.update({(row.get('context_id', 'canon'), row['source_id']): row['ordinal']
                            for row in snapshot.get('source_orders', [])})
    def issue(code, identifier, message, severity='error'):
        issues.append({'code': code, 'severity': severity, 'object_id': identifier, 'message': message})
    parents = {}
    for node in nodes.values():
        for child in node.get('events') or []:
            if child in nodes:
                parents.setdefault(child, []).append(node['id'])
    for node in nodes.values():
        kind = node.get('node_type')
        required = {'storyline': 'big_event', 'big_event': 'scene'}.get(kind)
        if required and any(child in nodes and nodes[child].get('node_type') != required
                            for child in node.get('events') or []):
            issue('STORYLINE_CHILD_TYPE', node['id'], '主要故事直属大事件；大事件直属场景，不能跳过层级')
        if kind == 'big_event':
            assigned = [identifier for identifier in parents.get(node['id'], [])
                        if nodes[identifier].get('node_type') == 'storyline']
            if len(assigned) > 1 or any(nodes[identifier].get('context_id', 'canon') != node.get('context_id', 'canon')
                                       for identifier in assigned):
                issue('BIG_EVENT_STORYLINE', node['id'], '大事件至多属于一个同语境主要故事；未总结的篇章不必创建主要故事')
    # At each level ownership descends through all nested containers.
    for kind, child_kind in [('storyline', 'big_event'), ('big_event', 'scene')]:
        containers = [node for node in nodes.values() if node.get('node_type') == kind]
        owners, groups = {}, {}
        for node in containers:
            context = node.get('context_id', 'canon')
            children = [child for child in node.get('events') or [] if child in nodes
                        and nodes[child].get('node_type') == child_kind
                        and nodes[child].get('context_id', 'canon') == context]
            pending, visited = list(children), set()
            while pending:
                identifier = pending.pop()
                if identifier in visited:
                    continue
                visited.add(identifier)
                member = nodes[identifier]
                if member.get('context_id', 'canon') != context:
                    continue
                owners.setdefault(identifier, set()).add(node['id'])
                pending.extend(child for child in member.get('events') or [] if child in nodes)
            starts = [bounds[child][0] for child in children if bounds.get(child) is not None]
            if not starts:
                issue('STORYLINE_POSITION', node['id'], '容器没有可定位的直属成员，无法确定阅读起点')
                continue
            start = min(starts)
            parent = next((identifier for identifier in parents.get(node['id'], [])
                           if nodes[identifier].get('node_type') == 'storyline'), None) if kind == 'big_event' else None
            row = {'id': node['id'], 'node_type': kind, 'context_id': context, 'parent_id': parent,
                   'start': coordinates[(context, start)], 'end': None,
                   'children': children, '_start': start, '_end': None}
            if kind == 'storyline':
                completion = node.get('completion')
                completion = completion if isinstance(completion, dict) else None
                ordinal = source_ordinals.get((context, (completion or {}).get('source_id')))
                if not isinstance(completion, dict) or type(completion.get('start')) is not int or completion['start'] < 0 or ordinal is None:
                    issue('STORYLINE_COMPLETION', node['id'], '已总结的主要故事必须给出正式原文坐标completion半开右界')
                else:
                    end = (ordinal, completion['start'])
                    if end <= max(bounds[child][1] for child in children if bounds.get(child) is not None):
                        issue('STORYLINE_COMPLETION', node['id'], '主要故事完成边界必须晚于所有所属成员的开始位置')
                    row['_end'], row['end'] = end, dict(completion)
            groups.setdefault((context, None), []).append(row)
        for identifier, assigned in owners.items():
            if len(assigned) > 1:
                issue('STORYLINE_MEMBER_OWNER', identifier, '同一成员不能交错归属多个同层阅读区间')
        for (context, _parent), rows in groups.items():
            rows.sort(key=lambda row: (row['_start'], row['id']))
            for previous, following in zip(rows, rows[1:], strict=False):
                if kind == 'storyline' and previous['_end'] is not None and previous['_end'] > following['_start']:
                    issue('STORYLINE_OVERLAP', previous['id'], '已完成主要故事的区间不能与下一主要故事重叠；允许篇章间留白')
                if kind == 'big_event':
                    previous['_end'] = following['_start']
                    previous['end'] = dict(following['start'])
                if previous['_start'] == following['_start']:
                    issue('STORYLINE_START', following['id'], '同层相邻区间不能有相同起点')
            if kind == 'big_event':
                last_parent = next((span for span in spans if span['id'] == rows[-1]['parent_id']), None)
                if last_parent and last_parent['_end'] is not None:
                    rows[-1]['_end'], rows[-1]['end'] = last_parent['_end'], dict(last_parent['end'])
            starts = [row['_start'] for row in rows]
            indexed = {row['id']: row for row in rows}
            for child in nodes.values():
                if child.get('node_type') != child_kind or child.get('context_id', 'canon') != context:
                    continue
                assigned = owners.get(child['id'], set())
                bound = bounds.get(child['id'])
                if bound is None:
                    if assigned:
                        issue('STORYLINE_SCENE_POSITION', child['id'], '所属区间成员不可定位')
                    continue
                index = bisect_right(starts, bound[0]) - 1
                if index < 0:
                    continue
                expected = rows[index]
                if expected['_end'] is not None and bound[0] >= expected['_end']:
                    if assigned:
                        issue('STORYLINE_OVERLAP', child['id'], '成员超出已完成主要故事边界')
                    continue
                if assigned != {expected['id']}:
                    severity = 'error' if assigned or expected['_end'] is not None else 'warning'
                    issue('STORYLINE_MEMBERSHIP' if severity == 'error' else 'STORYLINE_OPEN_SCENE',
                          child['id'], '成员在阅读区间内缺失归属或归入其他区间', severity)
                for owner in assigned:
                    span = indexed.get(owner)
                    if span and (bound[0] < span['_start'] or span['_end'] is not None and bound[1] >= span['_end']):
                        issue('STORYLINE_OVERLAP', child['id'], '成员跨越下一个区间起点，形成交错或重叠')
            spans.extend(rows)
    spans.sort(key=lambda row: (row['context_id'], row['_start'], row['node_type'], row['id']))
    return [{key: value for key, value in row.items() if not key.startswith('_')} for row in spans], issues
