"""One bounded real task: outside-project text, harmless command and business write."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import accept_screenplay as qa

from narrative.chat_store import ChatStore

BASE_SEED = qa.seed


def seed(ops):
    f = BASE_SEED(ops)
    ops.save_harness_settings({"provider": "ark", "model": "ark-code-latest", "reasoning_effort": "low",
        "context_window": 1_000_000, "output_reserve": 131_072, "compaction_reserve": 65_536,
        "compaction_max_tokens": 32_768, "max_tokens": 131_072})
    chat = ChatStore(ops.project_info().path).create('完全访问隔离验收')
    ops.set_assistant_access(chat['id'], 'full')
    external = Path(tempfile.mkdtemp(prefix='narrative-full-access-'))
    f.update(chat_id=chat['id'], external_directory=str(external), external_file=str(external / 'proof.txt'),
        command=subprocess.list2cmdline([sys.executable, '-c', "print('full-access-command-ok')"]))
    f['before_screenplay'] = ops.read_screenplay(f['screenplay_id'])
    return f


def prompt(f):
    return ('本次完全访问小任务，不读原文或修改剧本。请通过宿主工具在工程外路径' + f['external_file']
        + '写入精确文本full-access-file-ok，并随后读取确认。运行一次无害命令' + f['command']
        + '，cwd=' + f['external_directory'] + '，通过read_host_command读到完成状态与full-access-command-ok输出。'
        + '通过save_asset创建一个材质，title=完全访问验证材质，detail=隔离验收。'
        + '创建文件、启动命令、保存材质互不依赖，可同一批执行；之后读取文件和命令完成输出。无需复述账目，不要启动后台进程或改其他文件。')


def validate(ops, f, screenplay, calls, report):
    audit = json.loads((Path(report['audit_directory']) / 'task.json').read_text(encoding='utf-8'))
    steps = audit['steps']
    good = [s for s in steps if s.get('ok')]
    commands = [s['result'] for s in good if s['tool'] == 'read_host_command']
    reads = [s['result'] for s in good if s['tool'] == 'read_host_file']
    assets = ops.list_assets('material')['assets']
    from narrative.assistant import tool_catalog
    from narrative.project_package import layout
    (layout(ops.project_info().path).entry.parent / 'actual-task-tool-catalog.json').write_text(json.dumps(tool_catalog(access_mode='full'), ensure_ascii=False, indent=2), encoding='utf-8')
    report['full_access_evidence'] = {'task_access_mode': audit.get('access_mode'), 'steps': steps,
        'external_file_content': Path(f['external_file']).read_text() if Path(f['external_file']).is_file() else None,
        'assets': assets, 'host_task_audit': str(Path(report['audit_directory']) / 'host'),
        'note': 'Actual model calls, actual outside-project file and command, business asset saved through Operations. No manual repair.'}
    return {'full_snapshot': audit.get('access_mode') == 'full',
        'outside_file_written_and_read': Path(f['external_file']).is_file() and Path(f['external_file']).read_text() == 'full-access-file-ok' and any(row.get('content') == 'full-access-file-ok' for row in reads),
        'harmless_command_completed': any(row.get('status') == 'completed' and 'full-access-command-ok' in row.get('output', row.get('content', '')) for row in commands),
        'command_launched_once': len([s for s in good if s['tool'] == 'run_host_command']) == 1,
        'one_business_asset': len(assets) == 1 and assets[0]['title'] == '完全访问验证材质',
        'body_unchanged': screenplay == f['before_screenplay'],
        'actual_host_tool_set': all(any(s['tool'] == name for s in good) for name in ['write_host_file', 'read_host_file', 'run_host_command', 'read_host_command', 'save_asset'])}


def main():
    qa.seed = seed
    qa.PROMPT_VERSION = 'assistant-full-host/1'
    qa.FIXTURE_ORIGIN = 'New isolated source/project/chat in full mode; outside-project temporary directory; one harmless command and one material asset.'
    return qa.main(validate=validate, required_tools=['write_host_file', 'read_host_file', 'run_host_command', 'read_host_command', 'save_asset'], use_writing_target=False, prompt_factory=prompt)


if __name__ == '__main__':
    raise SystemExit(main())
