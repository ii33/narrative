"""Host tools share Operations and require a program-bound full access task."""
from __future__ import annotations


def register(Tool, register_tool, validate):
    text = {'type': 'string'}
    integer = {'type': 'integer', 'minimum': 0}
    page = {'offset': integer, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 65536}}
    path = {'path': text | {'required': True, 'description': '本机绝对路径，允许工程外；业务数据库编辑仍应使用对应Operations业务工具'}}
    def add(name, summary, params, writes=False):
        def invoke(ops, args):
            for key, value in args.items():
                validate(value, params[key], key)
            try:
                return getattr(ops, name)(**args)
            except (ValueError, OSError, LookupError) as error:
                from .tool_api import ToolError
                raise ToolError(str(error)) from error
        register_tool(Tool(name, summary, params, invoke, writes=writes, scope='host'))
    add('read_host_file', '完全访问任务读取本机文件分页。文本offset/limit按字符，encoding=base64时按原始字节；结果及审计自动脱敏。普通模式不可用', path | page | {'encoding': text})
    add('write_host_file', '完全访问任务写本机绝对路径文件，默认原子替换；append追加。encoding=base64可写二进制。实际业务数据应优先对应Operations，不以shell结果冒充业务入库', path | {'content': text | {'required': True}, 'encoding': text, 'append': {'type': 'boolean'}}, True)
    add('list_host_directory', '完全访问任务分页列出本机目录，不递归；offset按目录条目，结果脱敏', path | page)
    add('run_host_command', '完全访问任务异步运行真实系统shell命令，cwd必须绝对路径；返回command-N，随后read_host_command分页读取。无命令总时限。background只有显式true且任务正常完成才移交为本工程后台服务，默认任务终态收束进程树', {
        'command': text | {'required': True}, 'cwd': text | {'required': True}, 'encoding': text, 'background': {'type': 'boolean'}}, True)
    add('read_host_command', '完全访问任务分页读取当前任务命令或本工程已移交后台服务的脱敏输出与状态；不重复运行命令，字符offset；完整非截断输出保存在脱敏审计', {'command_id': text | {'required': True}} | page)
    add('stop_host_command', '完全访问任务停止指定command-N的进程树并等待收束，只作用当前任务或本工程已移交后台服务，不终止助手运行时', {'command_id': text | {'required': True}}, True)
