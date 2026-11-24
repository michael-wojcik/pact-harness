"""The warm transport: import once, fork per hook.

A cold process pays an interpreter start plus an import of the shared tree on
every call. For the five hooks on a PreToolUse Edit that is 4.1 to 4.8 seconds,
which is not a latency a person will tolerate before their file edit runs.

The warm worker pays the import once (0.8 to 0.9 seconds for 55 shared
modules), then forks a child per hook. A fork inherits the imported modules, so
the child is isolated exactly as a fresh process is -- same stdin, same exit
code, same stderr -- and the work it does is the hook body plus its own
imports, which are already resident. Measured: 261 ms for the same five hooks.

Two fork hazards were measured rather than assumed, and both are handled here:

- The child must flush stdout and stderr before os._exit. os._exit skips
  interpreter shutdown, so a buffered decision is otherwise discarded and the
  hook looks silent.
- The parent must flush before forking. Anything buffered in the parent's
  streams at fork time is inherited by the child and flushed into the child's
  captured output, corrupting the decision.

The worker is serial. At 35 to 90 ms per fork, serial is fast enough that
concurrency would add failure modes for no felt benefit, and a caller that
wants parallel groups still gets them: its threads queue on the socket.
"""

from __future__ import annotations

import base64
import json
import os
import select
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import runpy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..errors import PactHarnessError
from .launch import HookResult
from .transport import warm_supported

#: Frame cap, mirroring the decoder's document bound.
MAX_FRAME_BYTES = 8 * 1024 * 1024

#: How long a client waits for a worker to accept before giving up.
WORKER_START_TIMEOUT_S = 15.0

#: How long a client waits for one hook response beyond the hook's own timeout.
RESPONSE_GRACE_S = 5.0


class WarmTransportError(PactHarnessError):
    """The worker could not be reached, started, or trusted."""


def _b64encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64decode(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def warm_import(hooks_dir: Path) -> int:
    """Import the shared tree once, returning how many modules loaded."""
    import importlib
    import pkgutil

    hooks_text = str(hooks_dir)
    if hooks_text not in sys.path:
        sys.path.insert(0, hooks_text)
    import shared  # noqa: PLC0415 -- deliberately after sys.path is prepared

    loaded = 0
    for module in pkgutil.iter_modules(shared.__path__):
        try:
            importlib.import_module("shared." + module.name)
            loaded += 1
        except BaseException:  # noqa: BLE001 -- one broken module must not stop the rest
            continue
    return loaded


def run_forked(
    script: Path,
    stdin: bytes,
    *,
    env: Mapping[str, str],
    cwd: str | None = None,
    timeout_s: float | None = None,
) -> dict[str, Any]:
    """Run one script in a forked child of the current process.

    Exposed so the fork mechanics can be tested without a socket, and so the
    timeout path can be exercised against a script of the test's choosing.
    """
    if not warm_supported():
        raise WarmTransportError("the warm transport requires os.fork")

    in_r, in_w = os.pipe()
    out_r, out_w = os.pipe()
    err_r, err_w = os.pipe()
    started = time.monotonic()

    sys.stdout.flush()
    sys.stderr.flush()
    _debug(f"hook begin {script.name} timeout={timeout_s}")
    pid = os.fork()
    if pid == 0:  # pragma: no cover -- child process
        code = 1
        try:
            os.close(in_w)
            os.close(out_r)
            os.close(err_r)
            os.dup2(in_r, 0)
            os.dup2(out_w, 1)
            os.dup2(err_w, 2)
            os.close(in_r)
            os.close(out_w)
            os.close(err_w)
            os.environ.clear()
            os.environ.update(env)
            if cwd:
                os.chdir(cwd)
            # Rebind the Python-level streams to the descriptors this child just
            # received. Inheriting the parent's stream objects is wrong whenever
            # the parent replaced them -- a test harness, an embedder, a host
            # that wraps stdout for logging -- because then a hook's print goes
            # into that object instead of the pipe, and the decision is lost
            # while the exit code still says the hook ran.
            sys.stdin = os.fdopen(0, "r", closefd=False)
            sys.stdout = os.fdopen(1, "w", closefd=False)
            sys.stderr = os.fdopen(2, "w", closefd=False)
            try:
                runpy.run_path(str(script), run_name="__main__")
                code = 0
            except SystemExit as exc:
                value = exc.code
                code = 0 if value is None else (int(value) if isinstance(value, int) else 1)
            except BaseException:
                traceback.print_exc()
                code = 1
            try:
                sys.stdout.flush()
                sys.stderr.flush()
            except BaseException:
                pass
        finally:
            os._exit(code)

    os.close(in_r)
    os.close(out_w)
    os.close(err_w)

    try:
        os.write(in_w, stdin)
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            os.close(in_w)
        except OSError:
            pass

    out_chunks: list[bytes] = []
    err_chunks: list[bytes] = []
    open_fds = {out_r, err_r}
    deadline = None if timeout_s is None else started + timeout_s
    timed_out = False
    while open_fds:
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            timed_out = True
            break
        ready, _, _ = select.select(list(open_fds), [], [], remaining)
        if not ready:
            timed_out = True
            break
        for fd in ready:
            chunk = os.read(fd, 65536)
            if chunk:
                (out_chunks if fd == out_r else err_chunks).append(chunk)
            else:
                open_fds.discard(fd)

    if timed_out:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass

    for fd in (out_r, err_r):
        try:
            os.close(fd)
        except OSError:
            pass

    _, status = os.waitpid(pid, 0)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    _debug(f"hook end {script.name} timed_out={timed_out} ms={elapsed_ms}")
    exit_code = None if timed_out else os.waitstatus_to_exitcode(status)
    return {
        "exit": exit_code,
        "stdout": b"".join(out_chunks),
        "stderr": b"".join(err_chunks),
        "duration_ms": elapsed_ms,
        "timed_out": timed_out,
    }


@dataclass(frozen=True, slots=True)
class ForkTransport:
    """A transport that forks from the current process.

    This is what the worker uses to evaluate an event: the hooks run as forked
    children of the warm worker, so they keep process isolation and inherit the
    imported modules. It is not useful outside a process that has already paid
    the warm import, which is exactly the worker.
    """

    name: str = "fork"
    # Forking from a worker thread can deadlock the child on a lock held at the
    # moment of the fork, so this transport never runs a group concurrently.
    supports_parallel: bool = False

    def run(
        self,
        registration_script: Path,
        stdin: bytes,
        *,
        env: Mapping[str, str],
        python: str,
        timeout_s: float | None = None,
        cwd: str | None = None,
    ) -> HookResult:
        result = run_forked(
            registration_script, stdin, env=env, cwd=cwd, timeout_s=timeout_s
        )
        return HookResult(
            hook=registration_script.stem,
            script=registration_script,
            exit=result["exit"],
            stdout=result["stdout"],
            stderr=result["stderr"],
            duration_ms=int(result["duration_ms"]),
            timed_out=bool(result["timed_out"]),
        )


@dataclass(frozen=True, slots=True)
class WarmTransport:
    """A client of one warm worker."""

    socket_path: Path
    name: str = "warm"
    supports_parallel: bool = False

    def _roundtrip(self, payload: Mapping[str, Any], timeout_s: float | None) -> dict[str, Any]:
        """Send one request and read one JSON response."""
        data = (json.dumps(payload) + "\n").encode("utf-8")
        if len(data) > MAX_FRAME_BYTES:
            raise WarmTransportError("request exceeds the warm transport frame cap")
        deadline = time.monotonic() + (timeout_s or 0) + RESPONSE_GRACE_S
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(max(deadline - time.monotonic(), 1.0))
            client.connect(str(self.socket_path))
            client.sendall(data)
            chunks: list[bytes] = []
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\n" in chunk:
                    break
        response = json.loads(b"".join(chunks).split(b"\n", 1)[0])
        if not isinstance(response, dict):
            raise WarmTransportError("warm worker sent a non-object response")
        return response

    def evaluate(
        self,
        event: Mapping[str, Any],
        *,
        adapter: str = "dsh",
        state_root: str | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """Evaluate one normalised event inside the worker."""
        try:
            return self._roundtrip(
                {
                    "type": "evaluate",
                    "adapter": adapter,
                    "event": dict(event),
                    "state_root": state_root,
                },
                timeout_s,
            )
        except (OSError, socket.timeout, json.JSONDecodeError, WarmTransportError) as exc:
            return {"type": "evaluate", "status": "error", "error": f"warm worker unreachable: {exc}"}

    def run(
        self,
        registration_script: Path,
        stdin: bytes,
        *,
        env: Mapping[str, str],
        python: str,
        timeout_s: float | None = None,
        cwd: str | None = None,
    ) -> HookResult:
        request = {
            "script": str(registration_script),
            "stdin": _b64encode(stdin),
            "env": dict(env),
            "cwd": cwd,
            "timeout_s": timeout_s,
        }
        try:
            response = self._roundtrip(request, timeout_s)
        except WarmTransportError as exc:
            return HookResult(
                hook=registration_script.stem,
                script=registration_script,
                exit=None,
                stdout=b"",
                stderr=b"",
                duration_ms=0,
                launch_error=str(exc),
            )
        except (OSError, socket.timeout, json.JSONDecodeError) as exc:
            return HookResult(
                hook=registration_script.stem,
                script=registration_script,
                exit=None,
                stdout=b"",
                stderr=b"",
                duration_ms=0,
                launch_error=f"warm worker unreachable: {exc}",
            )
        return HookResult(
            hook=registration_script.stem,
            script=registration_script,
            exit=response.get("exit"),
            stdout=_b64decode(response.get("stdout", "")),
            stderr=_b64decode(response.get("stderr", "")),
            duration_ms=int(response.get("duration_ms", 0)),
            timed_out=bool(response.get("timed_out", False)),
            launch_error=response.get("launch_error"),
        )


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def serve(socket_path: Path, plugin_root: Path) -> None:
    """Run the warm worker until interrupted."""
    if not warm_supported():
        raise WarmTransportError("the warm transport requires os.fork")

    hooks_dir = plugin_root / "hooks"
    if not hooks_dir.is_dir():
        raise WarmTransportError(f"plugin root has no hooks directory: {plugin_root}")
    warm_import(hooks_dir)

    socket_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        socket_path.unlink()
    except FileNotFoundError:
        pass

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    os.chmod(socket_path, 0o600)
    server.listen(16)
    try:
        while True:
            try:
                connection, _ = server.accept()
            except InterruptedError:
                continue
            with connection:
                try:
                    data = b""
                    while b"\n" not in data:
                        chunk = connection.recv(65536)
                        if not chunk:
                            break
                        data += chunk
                        if len(data) > MAX_FRAME_BYTES:
                            break
                    request = json.loads(data.split(b"\n", 1)[0])
                    response = _handle(request, plugin_root)
                except BaseException as exc:  # noqa: BLE001 -- a bad request must not kill the worker
                    response = {
                        "exit": None,
                        "stdout": "",
                        "stderr": "",
                        "duration_ms": 0,
                        "timed_out": False,
                        "launch_error": f"warm worker rejected the request: {type(exc).__name__}: {exc}",
                    }
                try:
                    connection.sendall((json.dumps(response) + "\n").encode("utf-8"))
                except OSError:
                    pass
    finally:
        server.close()
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass


def _handle(request: Mapping[str, Any], plugin_root: Path) -> dict[str, Any]:
    """Dispatch one worker request."""
    if request.get("type") == "ping":
        return {"type": "ping", "status": "ok"}
    if request.get("type") == "evaluate":
        return _evaluate(request, plugin_root)
    script = Path(str(request.get("script", "")))
    if not _inside(script, plugin_root):
        return {
            "exit": None,
            "stdout": "",
            "stderr": "",
            "duration_ms": 0,
            "timed_out": False,
            "launch_error": f"script is outside the plugin root: {script}",
        }
    result = run_forked(
        script,
        _b64decode(str(request.get("stdin", ""))),
        env={str(key): str(value) for key, value in (request.get("env") or {}).items()},
        cwd=request.get("cwd"),
        timeout_s=request.get("timeout_s"),
    )
    return {
        "exit": result["exit"],
        "stdout": _b64encode(result["stdout"]),
        "stderr": _b64encode(result["stderr"]),
        "duration_ms": result["duration_ms"],
        "timed_out": result["timed_out"],
        "launch_error": None,
    }


#: How long one evaluation may take before the worker kills it.
DEFAULT_EVALUATE_TIMEOUT_S = 120.0


def _debug(message: str) -> None:
    """Append a diagnostic line when PACT_WARM_DEBUG names a file."""
    path = (os.environ.get("PACT_WARM_DEBUG") or "").strip()
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{os.getpid()} {time.monotonic():.3f} {message}\n")
    except OSError:
        pass


def _fork_call(func, timeout_s: float | None) -> dict[str, Any]:
    """Run a callable in a forked child and read back its JSON result.

    The evaluation gets its own child so a hung hook cannot wedge the worker;
    without it, one blocked fork inside a serial worker stops every later
    request for the life of the plugin.

    The result crosses the boundary through a temporary file rather than a
    pipe. A pipe must reach end-of-file before the parent can read, and
    end-of-file waits for every process that inherited the write end -- which
    includes any descendant a hook leaves behind. That produced an intermittent
    hang whose only symptom was a caller timeout. A file plus a polled waitpid
    has no such dependency, and the deadline is enforced by the same loop that
    collects the result.
    """
    handle, path = tempfile.mkstemp(prefix="pact-eval-", suffix=".json")
    os.close(handle)
    sys.stdout.flush()
    sys.stderr.flush()
    _debug(f"fork_call begin timeout={timeout_s}")
    pid = os.fork()
    if pid == 0:  # pragma: no cover -- child process
        try:
            try:
                payload = json.dumps(func())
            except BaseException as exc:  # noqa: BLE001 -- reported to the parent
                payload = json.dumps(
                    {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
                )
            with open(path, "w", encoding="utf-8") as out:
                out.write(payload)
        finally:
            try:
                sys.stdout.flush()
                sys.stderr.flush()
            except BaseException:
                pass
            os._exit(0)

    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    timed_out = False
    while True:
        finished, _ = os.waitpid(pid, os.WNOHANG)
        if finished == pid:
            break
        if deadline is not None and time.monotonic() >= deadline:
            timed_out = True
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
            os.waitpid(pid, 0)
            break
        time.sleep(0.002)
    _debug(f"fork_call parent done pid={pid} timed_out={timed_out}")

    try:
        if timed_out:
            return {
                "type": "evaluate",
                "status": "error",
                "error": f"evaluation exceeded {timeout_s:g}s and was killed",
            }
        with open(path, "r", encoding="utf-8") as result_file:
            parsed = json.loads(result_file.read())
    except (OSError, json.JSONDecodeError, ValueError):
        return {"type": "evaluate", "status": "error", "error": "unreadable evaluation result"}
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    if not isinstance(parsed, dict):
        return {"type": "evaluate", "status": "error", "error": "evaluation returned a non-object"}
    return parsed


def _evaluate(request: Mapping[str, Any], plugin_root: Path) -> dict[str, Any]:
    """Evaluate one normalised event inside the warm worker.

    The worker already holds the imported shared tree, so the hooks run as forks
    of this process rather than as fresh interpreters. That is the whole point
    of routing evaluation through the worker: a tool call pays one socket round
    trip and one fork per hook, not one interpreter start per hook.
    """
    timeout = request.get("timeout_s")
    timeout_s = float(timeout) if timeout else DEFAULT_EVALUATE_TIMEOUT_S
    return _fork_call(lambda: _evaluate_now(request, plugin_root), timeout_s)


def _evaluate_now(request: Mapping[str, Any], plugin_root: Path) -> dict[str, Any]:
    """The evaluation body, run inside the watchdog child."""
    try:
        from ..adapters.registry import get_adapter
        from ..contract import EventV1
        from ..engine import EngineConfig, evaluate

        adapter_id = str(request.get("adapter") or "dsh")
        adapter = get_adapter(adapter_id)
        event = EventV1.from_dict(request.get("event"))
        state_root = request.get("state_root")
        config = EngineConfig(
            adapter_id=adapter_id,
            plugin_root=plugin_root,
            state_root=Path(str(state_root)) if state_root else _default_state_root(),
            python=sys.executable,
            transport=_FORK_TRANSPORT,
            base_env=dict(os.environ),
        )
        envelope = evaluate(event, config, adapter=adapter)
        return {"type": "evaluate", "status": "ok", "envelope": envelope.to_dict()}
    except BaseException as exc:  # noqa: BLE001 -- report, never kill the worker
        return {
            "type": "evaluate",
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
        }


_FORK_TRANSPORT = ForkTransport()


def _default_state_root() -> Path:
    override = (os.environ.get("PACT_HOME") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".pact"


@dataclass
class WarmWorkerProcess:
    """A worker subprocess plus the transport that talks to it."""

    process: subprocess.Popen[bytes]
    socket_path: Path
    transport: WarmTransport

    def stop(self) -> None:
        """Ask the worker to exit and reap it."""
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


def spawn_worker(
    *,
    python: str,
    plugin_root: Path,
    socket_path: Path,
    base_env: Mapping[str, str] | None = None,
    start_timeout_s: float = WORKER_START_TIMEOUT_S,
) -> WarmWorkerProcess:
    """Start a warm worker and wait until it accepts a connection."""
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ if base_env is None else base_env)
    env.setdefault("PYTHONUNBUFFERED", "1")
    # A worker started from a checkout must find the package it is asked to
    # serve, so the package's own parent directory is prepended. An installed
    # distribution ignores this because its modules are already importable.
    package_src = str(Path(__file__).resolve().parents[2])
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = package_src + (os.pathsep + existing if existing else "")
    process = subprocess.Popen(
        [
            python,
            "-m",
            "pact_harness",
            "serve",
            "--socket",
            str(socket_path),
            "--plugin-root",
            str(plugin_root),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + start_timeout_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = b""
            if process.stderr is not None:
                stderr = process.stderr.read() or b""
            raise WarmTransportError(
                "warm worker exited during startup: "
                + stderr.decode("utf-8", errors="replace").strip()
            )
        if socket_path.exists():
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.settimeout(0.2)
                    probe.connect(str(socket_path))
                return WarmWorkerProcess(
                    process=process,
                    socket_path=socket_path,
                    transport=WarmTransport(socket_path=socket_path),
                )
            except OSError:
                pass
        time.sleep(0.05)
    process.kill()
    process.wait(timeout=5)
    raise WarmTransportError("warm worker did not accept a connection in time")


def default_socket_path(state_root: Path) -> Path:
    """A per-user socket path under the state root."""
    return state_root / "pact-harness" / "worker.sock"