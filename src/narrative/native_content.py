"""UI form sessions route repeat saves to the same public Operations submission."""
from __future__ import annotations

import copy
import threading
from datetime import UTC, datetime
from typing import Any

from .content_operations import CONTENT_OPERATIONS, bind_arguments, eligible


def receipt(payload):
    if not isinstance(payload, dict):
        return None
    if payload.get("submission_id"):
        return payload
    for key in ("result", "asset"):
        candidate = payload.get(key)
        if isinstance(candidate, dict) and candidate.get("submission_id"):
            return candidate
    return None


def target_value(arguments, key):
    value = arguments
    for part in key.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def field_patches(old, new, path):
    if old == new:
        return []
    if isinstance(old, dict) and isinstance(new, dict):
        result = []
        for key in sorted(old.keys() | new.keys()):
            if key not in new:
                result.append({"op": "remove", "path": path+[key]})
            elif key not in old:
                result.append({"op": "set", "path": path+[key], "value": new[key]})
            else:
                result.extend(field_patches(old[key], new[key], path+[key]))
        return result
    if isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        return [patch for index, value in enumerate(new) for patch in field_patches(old[index], value, path+[index])]
    return [{"op": "set", "path": path, "value": new}]


class FormSubmissions:
    def __init__(self):
        self.lock = threading.RLock()
        self.stack = []
        self.forms = {}
        self.priorities = {}
        self.sequence = 0
        self.conflicts = {}

    def token(self):
        with self.lock:
            self.sequence += 1
            return "form-"+datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")+"-"+str(self.sequence)

    def activate(self, identifier, active, priority=0):
        with self.lock:
            self.stack = [value for value in self.stack if value != identifier]
            if active:
                self.stack.append(identifier)
                self.priorities[identifier] = priority

    def current(self):
        with self.lock:
            return max(enumerate(self.stack), key=lambda item: (self.priorities.get(item[1], 0), item[0]))[1] if self.stack else ""

    def restore(self, identifier, staged, arguments):
        with self.lock:
            self.forms.setdefault(identifier, {})[staged["operation"]] = {
                "receipt": copy.deepcopy(staged), "arguments": copy.deepcopy(arguments)}

    def take_conflict(self, identifier):
        with self.lock:
            return self.conflicts.pop(identifier, None)

    def proxy(self, ops, identifier) -> Any:
        return _FormOps(ops, self, identifier) if identifier else ops


class _FormOps:
    def __init__(self, ops, forms, identifier):
        self._ops, self._forms, self._identifier = ops, forms, identifier

    def __getattr__(self, name):
        method = getattr(self._ops, name)
        if name not in CONTENT_OPERATIONS:
            return method
        def invoke(*args, **kwargs):
            arguments = bind_arguments(method.__func__, self._ops, args, kwargs)
            if not eligible(name, arguments, self._ops):
                return method(*args, **kwargs)
            spec = CONTENT_OPERATIONS[name]
            with self._forms.lock:
                prior = copy.deepcopy(self._forms.forms.get(self._identifier, {}).get(name))
            if prior:
                old = prior["arguments"]
                if any(target_value(old, key) != target_value(arguments, key) for key in spec.targets):
                    from .content_errors import ContentGuardError
                    message = "暂存目标已改变，请关闭后重新打开对应编辑器；原暂存仍保留"
                    with self._forms.lock:
                        self._forms.forms.get(self._identifier, {}).pop(name, None)
                        self._forms.conflicts[self._identifier] = {"submission_id": prior["receipt"]["submission_id"], "message": message}
                    raise ContentGuardError(message)
                patches = []
                for key in spec.editable:
                    if key in arguments and key in old:
                        patches.extend(field_patches(old[key], arguments[key], [key]))
                    elif key in arguments:
                        patches.append({"op": "set", "path": [key], "value": arguments[key]})
                    elif key in old:
                        patches.append({"op": "remove", "path": [key]})
                if not patches:
                    return prior["receipt"]
                from .content_errors import ContentConflictError, ContentGuardError
                try:
                    result = self._ops.patch_content_submission(prior["receipt"]["submission_id"],
                        prior["receipt"]["submission_revision"], patches, "修补当前表单内容")
                except (ContentConflictError, ContentGuardError) as error:
                    with self._forms.lock:
                        self._forms.forms.get(self._identifier, {}).pop(name, None)
                        self._forms.conflicts[self._identifier] = {"submission_id": prior["receipt"]["submission_id"], "message": str(error)}
                    raise
                # Keep immutable original routing and concurrency values.
                candidate = copy.deepcopy(old)
                for key in spec.editable:
                    if key in arguments:
                        candidate[key] = copy.deepcopy(arguments[key])
                    else:
                        candidate.pop(key, None)
            else:
                result = method(*args, **kwargs)
                candidate = arguments
            staged = receipt(result)
            with self._forms.lock:
                entries = self._forms.forms.setdefault(self._identifier, {})
                if staged and staged.get("status") == "staged":
                    entries[name] = {"receipt": copy.deepcopy(staged), "arguments": candidate}
                elif staged:
                    entries.pop(name, None)
            return result
        return invoke
