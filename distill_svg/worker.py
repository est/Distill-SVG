"""NDJSON worker used by the native GUI shell.

Run as ``python -m distill_svg.worker`` or as the frozen ``distill-core``
helper.  The protocol is one UTF-8 JSON object per line::

    -> {"cmd":"hello"}
    <- {"event":"hello","v":1,"capabilities":{...}}

    -> {"cmd":"convert","id":"r1","options":{...},"ocr":[...]}
    <- {"event":"progress","id":"r1","stage":"ocr","pct":0.4,"label":"识别文字"}
    <- {"event":"log","id":"r1","message":"..."}
    <- {"event":"result","id":"r1","result":{...}}
    <- {"event":"error","id":"r1","error":{"code":"...","message":"..."}}

    -> {"cmd":"cancel","id":"r1"}
    -> {"cmd":"shutdown"}

Conversions run on a worker thread so the main thread can keep reading
``cancel`` while a render is in flight.  Cancellation is cooperative between
pipeline stages; a stuck OpenCV call is handled by killing the whole helper
from the shell.
"""

from __future__ import annotations

import json
import sys
import threading
import traceback

from .api import ConversionCancelled, ConvertOptions, convert

VERSION = 1
CORE_VERSION = "0.2.0"

_out_lock = threading.Lock()


def _write(obj: dict) -> None:
    line = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    with _out_lock:
        try:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
        except (BrokenPipeError, ValueError):
            # The GUI went away; there is nobody left to tell.
            raise SystemExit(0) from None


class Worker:
    def __init__(self) -> None:
        self._cancel_id: str | None = None
        self._busy = False
        self._thread: threading.Thread | None = None

    def run(self) -> int:
        _write(
            {
                "v": VERSION,
                "event": "hello",
                "core": CORE_VERSION,
                "capabilities": {
                    "commands": ["hello", "convert", "cancel", "shutdown"],
                    "ocr": ["supplied"],  # the macOS shell performs Vision OCR
                    "styles": ["flat", "wireframe"],
                    "photos": ["embed", "placeholder", "outline"],
                    "icon_modes": ["auto", "vector", "embed"],
                },
            }
        )
        for raw_line in sys.stdin:
            raw = raw_line.strip()
            if not raw:
                continue
            try:
                message = json.loads(raw)
            except json.JSONDecodeError as exc:
                _write({"v": VERSION, "event": "error", "error": {"code": "BAD_JSON", "message": str(exc)}})
                continue
            if not isinstance(message, dict):
                continue
            cmd = message.get("cmd")
            if cmd == "shutdown":
                _write({"v": VERSION, "event": "bye"})
                break
            if cmd == "hello":
                _write({"v": VERSION, "event": "hello", "core": CORE_VERSION})
                continue
            if cmd == "cancel":
                self._cancel_id = str(message.get("id", ""))
                _write({"v": VERSION, "event": "cancel-ack", "id": self._cancel_id})
                continue
            if cmd == "convert":
                self._start(message)
                continue
            _write(
                {
                    "v": VERSION,
                    "event": "error",
                    "id": message.get("id"),
                    "error": {"code": "UNKNOWN_COMMAND", "message": f"unknown command: {cmd!r}"},
                }
            )
        self._cancel_id = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        return 0

    def _start(self, message: dict) -> None:
        if self._busy:
            _write(
                {
                    "v": VERSION,
                    "event": "error",
                    "id": message.get("id"),
                    "error": {"code": "BUSY", "message": "a conversion is already running"},
                }
            )
            return
        request_id = str(message.get("id", "job"))
        options = dict(message.get("options") or {})
        if "input" in message and "input" not in options:
            options["input"] = message["input"]
        if "ocr" in message:
            options["ocr_lines"] = message["ocr"]
        try:
            opts = ConvertOptions.from_dict(options)
        except TypeError as exc:
            _write(
                {
                    "v": VERSION,
                    "event": "error",
                    "id": request_id,
                    "error": {"code": "BAD_OPTIONS", "message": str(exc)},
                }
            )
            return
        if not opts.input or not opts.output:
            _write(
                {
                    "v": VERSION,
                    "event": "error",
                    "id": request_id,
                    "error": {"code": "BAD_OPTIONS", "message": "input and output paths are required"},
                }
            )
            return
        self._busy = True
        self._cancel_id = None
        self._thread = threading.Thread(
            target=self._run_job, args=(request_id, opts), name="distill-job", daemon=True
        )
        self._thread.start()

    def _run_job(self, request_id: str, opts: ConvertOptions) -> None:
        def progress(stage: str, pct: float, label: str) -> None:
            _write(
                {
                    "v": VERSION,
                    "event": "progress",
                    "id": request_id,
                    "stage": stage,
                    "pct": round(max(0.0, min(1.0, pct)), 4),
                    "label": label,
                }
            )

        def cancelled() -> bool:
            return self._cancel_id == request_id

        try:
            result = convert(opts, progress=progress, cancel=cancelled)
        except ConversionCancelled:
            _write(
                {
                    "v": VERSION,
                    "event": "error",
                    "id": request_id,
                    "error": {"code": "CANCELLED", "message": "conversion cancelled"},
                }
            )
        except FileNotFoundError as exc:
            _write(
                {
                    "v": VERSION,
                    "event": "error",
                    "id": request_id,
                    "error": {"code": "INPUT_NOT_FOUND", "message": str(exc)},
                }
            )
        except Exception as exc:  # noqa: BLE001 - report, never kill the worker
            traceback.print_exc(file=sys.stderr)
            _write(
                {
                    "v": VERSION,
                    "event": "error",
                    "id": request_id,
                    "error": {"code": "INTERNAL", "message": f"{type(exc).__name__}: {exc}"},
                }
            )
        else:
            for message in result.get("log", []):
                _write({"v": VERSION, "event": "log", "id": request_id, "message": message})
            _write({"v": VERSION, "event": "result", "id": request_id, "result": result})
        finally:
            self._busy = False
            if self._cancel_id == request_id:
                self._cancel_id = None
            sys.stderr.flush()


def main() -> int:
    try:
        return Worker().run()
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
