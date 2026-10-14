"""PoC import and continuation tests, isolated from the user's Claude home."""

import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

HELPER = Path(__file__).resolve().parents[1] / "utils" / "cora-handoff"
loader = importlib.machinery.SourceFileLoader("cora_handoff", str(HELPER))
spec = importlib.util.spec_from_loader(loader.name, loader)
handoff = importlib.util.module_from_spec(spec)
loader.exec_module(handoff)


class HandoffTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        env = patch.dict(
            os.environ, {"CLUSTER_ID": "cluster-1", "INVESTIGATION_ID": "inv-1"}
        )
        env.start()
        self.addCleanup(env.stop)
        sid = str(uuid4())
        transcript = (
            json.dumps(
                {
                    "type": "user",
                    "sessionId": sid,
                    "message": {"content": "Earlier observation"},
                }
            )
            + "\n"
        )
        self.bundle = {
            "kind": "cora.boundary.handoff",
            "schema_version": 1,
            "run_id": str(uuid4()),
            "cluster_id": "cluster-1",
            "investigation_id": "inv-1",
            "resume_session_id": sid,
            "source_cwd": "/app",
            "result": {
                "cluster_id": "cluster-1",
                "investigation_id": "inv-1",
                "summary": "Report",
            },
            "sessions": [
                {
                    "tier": 1,
                    "session_id": sid,
                    "transcript": transcript,
                    "sha256": hashlib.sha256(transcript.encode()).hexdigest(),
                }
            ],
        }

    def test_import_survives_reload_and_is_idempotent(self):
        target = handoff.import_bundle(self.bundle, self.home)
        self.assertEqual(handoff.load_bundle(self.home), self.bundle)
        self.assertEqual(handoff.import_bundle(self.bundle, self.home), target)
        self.assertTrue((target / "result.json").exists())

    def test_wrong_investigation_does_not_write(self):
        self.bundle["investigation_id"] = "other"
        with self.assertRaisesRegex(ValueError, "INVESTIGATION_ID"):
            handoff.import_bundle(self.bundle, self.home)
        self.assertFalse((self.home / "cora").exists())

    def test_checksum_mismatch_does_not_write(self):
        self.bundle["sessions"][0]["transcript"] += "changed"
        with self.assertRaisesRegex(ValueError, "checksum"):
            handoff.import_bundle(self.bundle, self.home)
        self.assertFalse((self.home / "cora").exists())

    def test_path_traversal_is_rejected(self):
        self.bundle["run_id"] = "../../escape"
        with self.assertRaises(ValueError):
            handoff.import_bundle(self.bundle, self.home)

    def test_destination_symlink_is_rejected(self):
        (self.home / "cora").symlink_to(self.home, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            handoff.import_bundle(self.bundle, self.home)

    def test_changed_run_never_overwrites_import(self):
        handoff.import_bundle(self.bundle, self.home)
        self.bundle["result"]["summary"] = "changed"
        with self.assertRaisesRegex(ValueError, "overwrite"):
            handoff.import_bundle(self.bundle, self.home)

    def test_response_json_and_agentcore_sse(self):
        response = json.dumps({"handoff": self.bundle})
        for raw in [
            response,
            "data: " + response + "\n\n",
            "data: " + json.dumps(response) + "\n\n",
        ]:
            self.assertEqual(
                handoff.read_response(io.BytesIO(raw.encode())), self.bundle
            )

    def test_resume_preserves_human_turns(self):
        with (
            patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.home / ".claude")}),
            patch.object(handoff.os, "execvp") as execute,
            patch.object(handoff.os, "chdir"),
        ):
            handoff.resume(self.bundle, self.home)
            sid = self.bundle["resume_session_id"]
            execute.assert_called_once()
            self.assertEqual(
                execute.call_args.args[1][0:3], ["claude", "--resume", sid]
            )
            transcript = next(
                (self.home / ".claude" / "projects").glob(f"*/{sid}.jsonl")
            )
            transcript.write_text(transcript.read_text() + "human continuation\n")
            handoff.resume(self.bundle, self.home)
            self.assertTrue(transcript.read_text().endswith("human continuation\n"))

    def test_terminal_escape_is_removed(self):
        self.assertNotIn("\x1b", handoff.safe_text("\x1b[2Jreport"))


if __name__ == "__main__":
    unittest.main()
