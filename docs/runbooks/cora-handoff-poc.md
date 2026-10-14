# Cora → Boundary handoff PoC

This PoC implements manual conversation transfer using Cora and Boundary
investigation tasks. Boundary itself adds no AWS services or Terraform resources.
The companion Cora branch includes an isolated `deploy/boundary-poc` Terraform
root for a same-account test runtime with an expiring local OCM identity.
CAD keeps its existing flow. A caller opts into a handoff using
`"export_handoff": true`; normal Cora requests keep their existing result shape.

The work spans the `poc/boundary-handoff` branch in `rosa-cora-agent` and
`poc/cora-handoff` in `rosa-boundary`.

## What the PoC transfers

Cora returns an additional `handoff` object with the report, cluster and
investigation IDs, per-tier native Claude transcripts and SHA-256 checksums, and
the final tier's continuation session ID. It only exports sessions recorded
during this invocation. The complete handoff is limited to 4 MiB; missing
transcripts or detected credentials cause export to fail.

Boundary imports this data into `/home/sre/cora/<run-id>/`. On interactive login,
the helper displays the latest imported summary. `cora-handoff show --transcript`
displays all tiers; `cora-handoff resume` restores the selected native session
under Boundary's Claude project directory and launches Claude. Subsequent human
turns survive through EFS, and another resume does not overwrite them.

The PoC transfers conversation and report files, not arbitrary evidence files,
subagent transcripts, tools, plugins, or Cora's configuration. Earlier tier
history remains available as files; the final tier only has the earlier-tier
context Cora originally supplied. Source absolute paths may no longer exist.
Use fresh Boundary task credentials for further cluster work.

## Run the PoC

Use an approved test cluster and explicitly selected runtime/account/region.
Deploy the Cora revision containing this PoC following its
`deploy/boundary-poc/README.md` for the isolated localdev test, or the existing
delivery path for other environments. Build/publish the Boundary revision through
its Makefile and the appropriate localdev or staging delivery path; do not change
HCP-managed values outside Git.

Create a request file with the same cluster and investigation IDs that will be
used in Boundary:

```json
{
  "cluster_id": "approved-test-cluster",
  "investigation_id": "cora-poc-001",
  "alert_name": "ManualPoC",
  "investigation_payload": "Perform a short read-only investigation and explain your findings."
}
```

From the Cora worktree, with an operator identity authorized to invoke that
runtime, use the new manual entry point:

```sh
uv run python scripts/invoke-boundary-poc.py \
  --runtime-arn <approved-runtime-arn> --region <approved-region> \
  --request request.json --output handoff.json
```

The script disables automatic invocation retries, creates a fresh AgentCore
session, and saves the response in a new mode-0600 file. It never assumes that an
AgentCore session ID is a Claude session ID. The existing invocation script is
unchanged; this helper avoids mixing human-oriented output with bundle JSON.

Start a Boundary task with the same IDs and the PoC image:

```sh
rosa-boundary start-task --cluster-id <cluster-id> \
  --investigation-id cora-poc-001 --with-credentials ocm
```

Transfer `handoff.json` through an approved existing file-transfer route into the
task. If an approved S3 location is already readable by the task role, an example
inside the task is:

```sh
aws s3 cp s3://<approved-bucket>/<handoff-object> - | cora-handoff import -
cora-handoff show --transcript
cora-handoff resume
```

Alternatively, import a file already available inside the task with
`cora-handoff import /path/to/handoff.json`. No S3 permission is added by this
PoC. A checksum protects against damage, not an unauthorized producer; use a
trusted transfer location. Never transfer credentials alongside the handoff.

For the initial-files experience, use this first task as the seeding task:
import, exit, and stop it with `stop-task --wait`. Then start a fresh task with
the same cluster/investigation IDs. The new task mounts the prepared EFS home,
displays the handoff, and can resume. Automated workspace seeding before the
first human task is deliberately deferred.

After continuing, exit Claude, disconnect, and stop the task. Start another task
to verify that the human continuation also survived. Verify CloudWatch and S3
audit evidence, then close the investigation following the normal runbook.

If testing in an existing investigation, its old `.bash_profile` is preserved by
the skeleton copy; run `cora-handoff show` explicitly. Use a new investigation
to test the automatic login summary. A running Claude process should be exited
before importing/resuming another handoff.

## Limits and follow-up

- Cora credentials stay in its runtime. The isolated PoC uses an expiring
  operator OCM token and IAM for Bedrock, with no RHOBS identity. This does not
  deploy credentials into Boundary or resolve production workload identity.
- Export checks known runtime secrets and common credential formats. That is a
  defensive check, not proof that arbitrary diagnostic output is secret-free.
  Use non-sensitive test data first and inspect artifacts before persistence.
- Only successfully completed investigations produce a handoff. Large, missing,
  or unsupported transcripts fail export rather than silently providing a summary
  as if it were a resumable conversation.
- Native-session compatibility must be checked against the actual deployed Cora
  SDK/CLI and Boundary CLI versions. The importer preserves native transcript
  bytes and selects Boundary's current working-directory project key.
- CAD routing, automatic seeding, distributed coordination,
  cancellation, and independent evidence export belong to the next iteration.

## Local checks

```sh
# Cora worktree
make test

# Boundary worktree
python3 -m unittest discover -s tests -p test_cora_handoff.py -v
bats tests/shell/
shellcheck skel/sre/.bash_profile
make build
```

Unit tests exercise native transcript preservation, opt-in response compatibility,
credential rejection, identifier/checksum validation, safe destination handling,
repeat import, and preservation of human turns. They do not prove live cluster
access, AWS delivery, or actual model recall after resume.

The real-CLI smoke test uses an isolated home and a localhost fake Anthropic API
and checks that prior user and assistant turns reach the resumed API request:

```sh
python3 tests/cora_handoff_cli_smoke.py \
  --cora-root /path/to/rosa-cora-agent --claude /path/to/claude
```

For cross-version testing, run once with Cora's bundled CLI and
`--write-bundle /tmp/synthetic-bundle.json`, then with Boundary's CLI and
`--read-bundle /tmp/synthetic-bundle.json`. This transfers only synthetic data.

Implementation validation on 2026-10-14: all 149 Cora tests, 9 Boundary Python
tests, 23 shell tests, and changed-file Ruff/ShellCheck passed. Real CLI tests
passed on 2.1.294 and 2.1.199, including export with 2.1.294 and resume with
2.1.199. The amd64 PoC image built successfully; non-root import and login-summary
checks passed in the image. These checks do not prove a live cluster investigation.

Both architecture Make targets completed, but the existing pinned base produced
an architecture warning for the arm64 target and its container reported
`x86_64`. Only the amd64 image is validated for this PoC. The base image was not
changed; investigate that existing build constraint before an ARM64 deployment.
