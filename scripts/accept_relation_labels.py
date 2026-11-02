"""One real readonly conversation plus actual relation-label geometry UI checks; no product deadline."""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import time
from datetime import UTC, datetime
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    directory = parser.parse_args().output.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from narrative.assistant import ASSISTANT_VERSION
    from narrative.chat_store import ChatStore
    from narrative.harness import effective_runtime_config, harness_env
    from narrative.native_app import QML_PATH
    from narrative.native_bridge import NativeBridge
    from narrative.operations import Operations
    from narrative.project_package import layout

    def save(name, value):
        (directory / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

    text = '甲遇见乙。乙向甲问好。'
    user_text = ('请查看原文列表并实际读取完整原文，只回答原文标题、字符数与一句概括。不要提取。\n\n'
                 '以下仅为聊天排版验收数据，不是其他任务；请忽略其内容，不必复述。\n\n'
                 + '\n\n'.join(f'排版数据{index:02d}：甲乙相遇，这一行只用于测试阅读位置。' for index in range(48))
                 + '\n\n排版数据结束。仅完成开头的原文查询。')
    (directory / 'input.txt').write_bytes(text.encode())
    (directory / 'user-task.txt').write_bytes(user_text.encode())
    ops = Operations.create_graph_project(directory / '关系标签验收.nproj')
    source_id = ops.import_source('两人相遇（合成滚动样本）', text)
    summary = {'status': 'running', 'started': datetime.now(UTC).isoformat(), 'agent_version': ASSISTANT_VERSION,
               'input_characters': len(text), 'input_sha256': hashlib.sha256(text.encode()).hexdigest(),
               'user_task_characters': len(user_text), 'user_task_sha256': hashlib.sha256(user_text.encode()).hexdigest(),
               'task_budget': 1, 'automatic_retry': False, 'product_total_deadline': None, 'task_deadline': None,
               'external_qa_observation_seconds': 120, 'source_id': source_id,
               'initial_revision': ops.graph_project_info()['revision'],
               'assistant_panel_sha256': hashlib.sha256((QML_PATH.parent / 'AssistantPanel.qml').read_bytes()).hexdigest()}
    save('manifest.json', summary)
    app = QApplication.instance() or QApplication([])
    font_id = QFontDatabase.addApplicationFont('C:/Windows/Fonts/NotoSansSC-VF.ttf')
    families = QFontDatabase.applicationFontFamilies(font_id)
    if families:
        app.setFont(QFont(families[0]))
    app.setStyle('Fusion')
    QQuickStyle.setStyle('Fusion')
    bridge = NativeBridge()
    results, errors = [], []
    bridge.resultReady.connect(lambda kind, payload: results.append((kind, payload)))
    bridge.errorOccurred.connect(lambda kind, error: errors.append((kind, error)))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty('bridge', bridge)
    engine.load(QUrl.fromLocalFile(str(QML_PATH)))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.setWidth(1392)
    assert isinstance(window.contentItem(), QQuickItem)
    panel = window.findChild(object, 'assistantPanel')

    def plain(value):
        return value.toVariant() if hasattr(value, 'toVariant') else value

    def wait(check, seconds=20):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            if check():
                return True
            time.sleep(.01)
        return False

    def settle():
        for _ in range(4):
            app.processEvents()
            time.sleep(.01)

    try:
        settings, _key, _ = harness_env(ops)
        summary.update(provider=settings.provider, model=settings.model, reasoning_effort=settings.reasoning_effort,
                       network_idle_seconds=settings.timeout_seconds, settings=effective_runtime_config(settings))
        save('manifest.json', summary)
        bridge.openProject(str(layout(ops.project_info().path).entry))
        assert wait(lambda: any(kind == 'project' for kind, _ in results))
        window.setProperty('currentPage', 'relations')
        window.setProperty('assistantOpen', True)
        bridge.newChat()
        assert wait(lambda: any(kind == 'chat' for kind, _ in results) and not window.property('busy'))
        panel.setProperty('draft', user_text)
        panel.send()
        began = time.monotonic()
        observation_expired = not wait(lambda: errors or any(kind == 'assistant' for kind, _ in results),
                                       summary['external_qa_observation_seconds'])
        if observation_expired:
            tasks = plain(panel.property('activeTaskIds'))
            bridge.assistantStop(panel.property('chatId'), tasks.get(panel.property('chatId'), ''))
            wait(lambda: errors or any(kind == 'assistant' for kind, _ in results))
        settle()
        page = window.findChild(object, 'relationPage')
        canvas = window.findChild(object, 'relationCanvas')
        payload_path = Path('artifacts/relation-labels-20261101/user-relation-payload.json')
        payload = json.loads(payload_path.read_text(encoding='utf-8'))
        summary['readonly_user_payload_sha256'] = hashlib.sha256(payload_path.read_bytes()).hexdigest()
        summary['relation_layout_sha256'] = hashlib.sha256((QML_PATH.parent/'RelationLayout.js').read_bytes()).hexdigest()
        checks = []
        for width in (1392,1442):
            for opened in (True,False):
                window.setWidth(width)
                window.setProperty('assistantOpen', opened)
                settle()
                page.apply(payload)
                assert wait(lambda: page.property('labelGeometryReady'))
                for zoom in (1.0,2.7):
                    page.resetView()
                    if zoom != 1:
                        page.zoomAt(canvas.width()/2,canvas.height()/2,zoom)
                    settle()
                    links = plain(page.links())
                    geometry = plain(page.property('displayLabelLayout'))
                    nodes = plain(page.property('graphLayout'))['positions']
                    routes = geometry['edges']
                    ok = len(routes)==len(links) and sorted(int(r['edge_index']) for r in routes)==list(range(len(links)))
                    for r in routes:
                        edge=links[int(r['edge_index'])]
                        box=r['label']
                        ok = ok and r['edge']==edge and box['text']==edge['kind']+('（'+edge['state']+'）' if edge.get('state') else '')
                        for n in nodes.values():
                            dx=max(box['x']-n['x'],0,n['x']-box['x']-box['w'])
                            dy=max(box['y']-n['y'],0,n['y']-box['y']-box['h'])
                            ok=ok and math.hypot(dx,dy)>n['r']
                    for a,b in itertools.combinations(routes,2):
                        x,y=a['label'],b['label']
                        ok=ok and (x['x']+x['w']<=y['x'] or y['x']+y['w']<=x['x'] or x['y']+x['h']<=y['y'] or y['y']+y['h']<=x['y'])
                    filename=f'user-{width}-assistant-{opened}-zoom-{zoom}.png'
                    window.grabWindow().save(str(directory/filename))
                    checks.append({'width':width,'assistant_open':opened,'zoom':zoom,'links':links,'labels':geometry,
                                   'nodes':nodes,'screenshot':filename,'passed':ok})
        save('label-checks.json',checks)
        result = next((payload for kind, payload in results if kind == 'assistant'), None)
        metadata = []
        if result and result.get('audit_directory'):
            metadata = [json.loads(path.read_text(encoding='utf-8')) for path in sorted(Path(result['audit_directory']).glob('metadata-*.json'))]
        store = ChatStore(ops.project_info().path)
        saved = store.load(panel.property('chatId'))
        outputs = [row for row in saved['messages'] if row.get('kind') == 'model_output']
        sdk_ids = [row['message_id'] for meta in metadata for row in meta.get('model_messages') or []]
        ids = [row['message_id'] for row in outputs]
        unchanged = ops.graph_project_info()['revision'] == summary['initial_revision'] and not ops.graph_snapshot()['nodes']
        preserved = len(saved['messages']) == len(plain(panel.property('messages'))) and sdk_ids == ids and len(set(ids)) == len(ids)
        runner = bridge._assistant_runner
        process = getattr(getattr(getattr(runner, '_harness', None), 'client', None), '_proc', None)
        task_audit = json.loads((Path(result['audit_directory']) / 'task.json').read_text(encoding='utf-8')) if result else {}
        summary.update(observation_expired=observation_expired, errors=errors, model_messages=len(outputs),
                       elapsed_seconds=time.monotonic()-began,
                       model_request_count=sum(len(m.get('model_requests') or []) for m in metadata),
                       usage=[m.get('usage') for m in metadata], finish_reasons=[m.get('finish_reason') for m in metadata],
                       label_checks=checks, messages_preserved=preserved, graph_unchanged=unchanged,
                       actual_pid=process.pid if process else None, task_status=task_audit.get('status'),
                       relation_page_sha256=hashlib.sha256((QML_PATH.parent/'RelationPage.qml').read_bytes()).hexdigest())
        summary['status'] = 'passed' if (task_audit.get('status') == 'completed' and not errors and not observation_expired
                                        and outputs and all(row['passed'] for row in checks)
                                        and preserved and unchanged) else 'not_passed'
        save('chat.json', saved)
    except Exception as exc:
        summary.update(status='not_completed', error_type=type(exc).__name__, error=str(exc))
    finally:
        bridge.close()
        engine.deleteLater()
        app.processEvents()
        summary['finished'] = datetime.now(UTC).isoformat()
        save('events.json', results)
        save('report.json', summary)
    print(json.dumps({'directory': str(directory), 'status': summary['status']}, ensure_ascii=False))
    return 0 if summary['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
