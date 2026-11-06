"""Editable shot writing and shared scene sound intentions."""
from copy import deepcopy


def script_fields(change, previous):
    return {key: deepcopy(change.get(key, previous.get(key, default))) for key, default in
            [('sound_notes', ''), ('description', ''), ('shot_language', ''), ('visual_elements', []), ('sound_intent_ids', [])]}


def apply_intent(board, change, allocate):
    intents = board.setdefault('sound_intents', [])
    if change['op'] == 'create_sound_intent':
        row = {'id': allocate('intent-'), 'kind': '', 'speaker': '', 'text': '', 'source_block_id': None}
        intents.append(row)
    else:
        row = next((row for row in intents if row['id'] == change.get('intent_id')), None)
        if row is None:
            raise ValueError('声音意图不属于本场')
        if change['op'] == 'delete_sound_intent':
            if any(row['id'] in shot.get('sound_intent_ids', []) or any(sound.get('intent_id') == row['id'] for sound in shot.get('sounds', [])) for shot in board['shots']):
                raise ValueError('声音意图仍被镜头或声轨引用，请先解除绑定')
            intents.remove(row)
            return
    row.update({key: deepcopy(change[key]) for key in ('kind', 'speaker', 'text', 'source_block_id') if key in change})


def validate_script(board, asset_lookup=None, source_blocks=None):
    intents = {}
    for row in board.get('sound_intents', []):
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or row['id'] in intents:
            raise ValueError('声音意图编号须唯一')
        if row.get('kind') not in {'dialogue', 'effect', 'silence'} or any(not isinstance(row.get(key), str) for key in ('speaker', 'text')):
            raise ValueError('声音意图需要有效类型、说话者和文本')
        source = row.get('source_block_id')
        if source is not None and (not isinstance(source, str) or not source or (source_blocks is not None and source_blocks.get(source, {}).get('type') not in {'dialogue', 'sound'})):
            raise ValueError('声音意图正文依据须引用本场对白或声音块')
        intents[row['id']] = row
    for shot in board['shots']:
        for key in ('description', 'shot_language', 'sound_notes'):
            if not isinstance(shot.get(key, ''), str):
                raise ValueError('镜头正文与镜头语言须为文本')
        elements = shot.get('visual_elements', [])
        if not isinstance(elements, list):
            raise ValueError('视觉元素须为列表')
        for row in elements:
            if not isinstance(row, dict) or set(row) != {'kind', 'name', 'usage', 'asset_id'} or row.get('kind') not in {'person', 'prop', 'set'} or not isinstance(row.get('name'), str) or not row['name'].strip() or not isinstance(row.get('usage'), str):
                raise ValueError('视觉元素需要类型、名称、使用描述与资源绑定')
            identifier = row['asset_id']
            if identifier is not None:
                if not isinstance(identifier, str) or not identifier:
                    raise ValueError('视觉资源须为实际资产编号或null')
                if asset_lookup:
                    asset_lookup(identifier)
        refs = shot.get('sound_intent_ids', [])
        if not isinstance(refs, list) or any(not isinstance(ref, str) or ref not in intents for ref in refs) or len(refs) != len(set(refs)):
            raise ValueError('镜头声音须引用本场有效且无重复的声音意图')
        for sound in shot.get('sounds', []):
            identifier = sound.get('intent_id')
            if identifier is not None and (not isinstance(identifier, str) or identifier not in refs or intents[identifier]['kind'] != sound.get('kind')):
                raise ValueError('声轨意图须绑定本镜引用的同类型声音意图')
