# Record Claude-shaped hook frames while PACT's own test suite runs.
#
# Put this directory on PYTHONPATH and set PACT_CORPUS_OUT to a file. Python then
# imports sitecustomize in every test process, and any hook script the tests run
# as a child process is recorded: its stdin (the Claude frame), the hook path and
# the frame's cwd. The corpus consumer turns those into parity cells, so the shim
# is measured on frames upstream's own tests use instead of hand-written ones.
#
# Nothing is recorded when PACT_CORPUS_OUT is unset, and failures in this shim
# are swallowed: a recorder must never break the suite it observes.

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading

_OUT = os.environ.get("PACT_CORPUS_OUT")
_LIMIT = int(os.environ.get("PACT_CORPUS_LIMIT", "4000") or "4000")
_PER_HOOK = int(os.environ.get("PACT_CORPUS_PER_HOOK", "8") or "8")

if _OUT:
    _lock = threading.Lock()
    _seen: set[str] = set()
    _per_hook: dict[str, int] = {}
    _count = 0
    _full = False

    def _relative_hook(script: str) -> str | None:
        marker = "/hooks/"
        normalized = script.replace(os.sep, "/")
        index = normalized.find(marker)
        if index < 0:
            return None
        return normalized[index + 1 :]

    def _match(args: object) -> str | None:
        if not isinstance(args, (list, tuple)) or len(args) < 2:
            return None
        executable = str(args[0])
        script = str(args[1])
        if "python" not in os.path.basename(executable).lower():
            return None
        if not script.endswith(".py"):
            return None
        return _relative_hook(script)

    def _write(record: dict) -> None:
        payload = json.dumps(record, sort_keys=True) + "\n"
        with open(_OUT, "a", encoding="utf-8") as handle:
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                handle.write(payload)
            finally:
                try:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass

    def _record(hook: str, stdin: object) -> None:
        global _count, _full
        if _full:
            return
        if isinstance(stdin, bytes):
            try:
                text = stdin.decode("utf-8")
            except UnicodeDecodeError:
                return
        elif isinstance(stdin, str):
            text = stdin
        else:
            return
        if not text.strip():
            return
        try:
            frame = json.loads(text)
        except ValueError:
            return
        if not isinstance(frame, dict) or not isinstance(frame.get("hook_event_name"), str):
            return
        digest = hashlib.sha256(json.dumps(frame, sort_keys=True).encode("utf-8")).hexdigest()
        key = hook + ":" + digest
        with _lock:
            if key in _seen:
                return
            if _per_hook.get(hook, 0) >= _PER_HOOK:
                return
            if _count >= _LIMIT:
                _full = True
                return
            _seen.add(key)
            _per_hook[hook] = _per_hook.get(hook, 0) + 1
            _count += 1
        _write(
            {
                "hook": hook,
                "event": frame.get("hook_event_name"),
                "cwd": frame.get("cwd"),
                "home": os.path.expanduser("~"),
                "frame": frame,
            }
        )

    class _RecordingPopen(subprocess.Popen):
        def __init__(self, args, *rest, **kwargs):
            self._pact_hook = _match(args)
            super().__init__(args, *rest, **kwargs)

        def communicate(self, input=None, timeout=None):  # noqa: A002 - matches stdlib
            stdout, stderr = super().communicate(input, timeout)
            if self._pact_hook:
                try:
                    _record(self._pact_hook, input)
                except Exception:
                    pass
            return stdout, stderr

    subprocess.Popen = _RecordingPopen
