"""Exercise real Claude transcript creation and resume against a local fake API.

No inference, AWS calls, or user credentials are used. Pass --cora-root and
--claude explicitly. The test asserts the resumed API request contains both
the original user prompt and the original assistant response.
"""

import argparse
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import patch
from uuid import uuid4


def module_from_file(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class FakeAPI(BaseHTTPRequestHandler):
    requests: ClassVar[list] = []

    def log_message(self, *_args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if "/messages" not in self.path:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")
            return
        self.requests.append(payload)
        message = {
            "id": "msg_poc",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-20250514",
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 10},
        }
        events = [
            ("message_start", {"type": "message_start", "message": message}),
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "text_delta",
                        "text": "Synthetic finding: node-poc-42 is unavailable.",
                    },
                },
            ),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 10},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        ]
        body = "".join(
            f"event: {event}\ndata: {json.dumps(data)}\n\n" for event, data in events
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cora-root", required=True, type=Path)
    parser.add_argument("--claude", required=True)
    transfer = parser.add_mutually_exclusive_group()
    transfer.add_argument(
        "--write-bundle",
        type=Path,
        help="Save synthetic bundle for cross-version tests",
    )
    transfer.add_argument(
        "--read-bundle",
        type=Path,
        help="Resume a synthetic bundle produced by another CLI version",
    )
    args = parser.parse_args()
    cora = module_from_file(
        "poc_exporter", args.cora_root / "rosa_cora_agent/handoff.py"
    )
    boundary = module_from_file(
        "poc_importer", Path(__file__).resolve().parents[1] / "utils/cora-handoff"
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeAPI)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="cora-resume-smoke-") as temporary:
            root = Path(temporary).resolve()
            source, destination = root / "cora", root / "boundary"
            source.mkdir()
            destination.mkdir()
            sid = str(uuid4())
            env = {
                "PATH": os.environ["PATH"],
                "HOME": str(source),
                "CLAUDE_CONFIG_DIR": str(source / ".claude"),
                "ANTHROPIC_API_KEY": "synthetic-poc-key-not-a-real-secret",
                "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "DISABLE_AUTOUPDATER": "1",
                "DISABLE_TELEMETRY": "1",
            }
            common = [
                args.claude,
                "--print",
                "--model",
                "claude-sonnet-4-20250514",
                "--tools",
                "",
                "--setting-sources",
                "",
                "--strict-mcp-config",
            ]
            first = subprocess.run(
                [
                    *common,
                    "--session-id",
                    sid,
                    "Investigate synthetic incident cora-poc-marker-17.",
                ],
                cwd=source,
                check=False,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if first.returncode:
                raise RuntimeError(
                    f"Initial Claude run failed: {first.stdout}\n{first.stderr}"
                )
            recorder = cora.HandoffRecorder()
            recorder.observe(1, SimpleNamespace(session_id=sid))
            identity = {
                "cluster_id": "cluster-1",
                "investigation_id": "inv-1",
                "summary": "Synthetic report",
            }
            with patch.dict(
                os.environ, {"CLAUDE_CONFIG_DIR": env["CLAUDE_CONFIG_DIR"]}
            ):
                bundle = recorder.export(identity, str(source))
            if args.write_bundle:
                with args.write_bundle.open("x") as stream:
                    json.dump(bundle, stream)
            if args.read_bundle:
                bundle = json.loads(args.read_bundle.read_text())
                sid = bundle["resume_session_id"]
            env.update(
                {
                    "HOME": str(destination),
                    "CLAUDE_CONFIG_DIR": str(destination / ".claude"),
                }
            )
            with (
                patch.dict(
                    os.environ,
                    {
                        "CLUSTER_ID": "cluster-1",
                        "INVESTIGATION_ID": "inv-1",
                        "CLAUDE_CONFIG_DIR": env["CLAUDE_CONFIG_DIR"],
                    },
                ),
                patch.object(boundary.os, "execvp"),
                patch.object(boundary.os, "chdir"),
            ):
                boundary.import_bundle(bundle, destination)
                boundary.resume(bundle, destination)
            before = len(FakeAPI.requests)
            second = subprocess.run(
                [*common, "--resume", sid, "Continue the previous investigation."],
                cwd=destination,
                check=False,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if second.returncode:
                raise RuntimeError(
                    f"Resumed Claude run failed: {second.stdout}\n{second.stderr}"
                )
            resumed = json.dumps(FakeAPI.requests[before:])
            assert "cora-poc-marker-17" in resumed, (
                "Original user prompt missing after resume"
            )
            assert "node-poc-42" in resumed, (
                "Original assistant response missing after resume"
            )
            print(
                "PASS: real Claude exported/imported history and sent both prior turns on resume (local fake API; no inference)."
            )
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
