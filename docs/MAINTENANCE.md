# Update and publishing workflow

Start with [architecture](ARCHITECTURE.md), [known issues](../KNOWN_ISSUES.md),
and [changelog](../CHANGELOG.md). These files are the durable project context
for a new contributor or coding-agent session.

## 1. Establish the current state

Check working changes, branch, remote, and app version before editing:

```sh
git status --short
git remote -v
git log -3 --oneline
```

This public repository contains sanitized source, not the original research
workspace. If maintaining a separate private workspace, develop there and export
reviewed files individually. Never merge private history or recursively copy
its contents here. Review/port public contributions before the next export.

## 2. Change and verify behavior

Keep the operation lock, session-bound replies, close-on-timeout, and power-intent
cancellation rules described in the architecture guide. Add regressions for
changed behavior. Preserve unrelated edits and avoid live appliance commands
without the operator's coordination.

For runtime/configuration changes, increment `duepi_stove_addon/config.yaml`,
update `CHANGELOG.md`, `KNOWN_ISSUES.md`, and relevant setup instructions. A
documentation-only commit does not need an app version bump.

```sh
node --test tests/*.test.js
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -q
bash -n duepi_stove_addon/run.sh
```

Use a separate environment to verify Paho 1.x compatibility:

```sh
python3 -m venv .venv-paho1
.venv-paho1/bin/pip install cryptography 'paho-mqtt==1.6.1'
.venv-paho1/bin/python -m unittest discover -s tests -p 'test_*.py' -q
```

Baseline at 1.0.3: 17 Node and 29 Python tests. Record current results rather
than assuming those counts remain fixed. Simulated tests do not prove container
builds, physical fan behavior, or general architecture/model support. Documentation
changes need link/diff/privacy review; repeat runtime tests when code changes or
an unresolved concern warrants them.

## 3. Export and inspect only intended files

Runtime sources belong in `duepi_stove_addon/` and `dpremote/stove.js`; tests
belong in `tests/`. Maintain curated public docs and screenshots deliberately.
When exporting from private development, sanitize examples, comments, identifiers,
and links as well as executable code. Use documentation-range IPs and generic
device identifiers; never restore an installation-specific MQTT ID.

Do not include private Git history, workspace handoffs, original research notes,
reference source dumps, vendor binaries, captures, logs, local environments,
options.json, credentials, or archive backups. Do not commit raw release ZIPs
just to share source: GitHub's **Code → Download ZIP** provides a source archive.

Preserve Florian Casse / DuepiRemoteHA links and full MIT notices in both root
and add-on `THIRD_PARTY_NOTICES.md`. Screenshots require visual privacy review,
generic filenames, and removal of textual/EXIF metadata. Electricity/cost widgets
in the example dashboard are separate integrations, not bridge capabilities.

## 4. Privacy gate before every publication

Review all changed and untracked files, including names, comments, image metadata,
and nested archives. Check for device codes, actual derived keys, MQTT/account
credentials, tokens, private keys, local paths, private IPs, MACs, and hostnames.
Run the private export scanner when publishing from the maintainer's development
workspace; it checks candidate files, archive contents, and reachable Git blobs.
Its forbidden-filename checks cover the working tree only: separately inspect
archive member names and historical paths, including files deleted by later
commits. Automated patterns do not replace manual review or prove the absence
of every secret.

```sh
git status --short
git diff --stat
git diff --check
# Stage only individually reviewed paths.
git diff --cached
git diff --cached --check
```

Never print suspected secrets in review output or public issues. A shared
derivation mask and synthetic protocol vectors are public; real device codes
and derived keys are private. `.gitignore` is an additional guard, not an audit.

## 5. Commit, push, and verify

Fetch `origin/main` and integrate reviewed remote changes before publishing.
Use public author identity, stage only reviewed paths, commit, and rerun privacy
checks against committed blobs. Use an ordinary push or the project's PR flow.
Resolve non-fast-forward rejections by integrating remote work; do not force-push.

The initial public history was replaced with explicit operator approval during
privacy cleanup. That was a one-time exception, not a release procedure. If a
secret reaches Git history, stop and follow a separately reviewed cleanup;
deleting it in a later commit does not remove earlier copies or caches.

After pushing, compare local HEAD with remote main and inspect the published
README, images, notices, and changed files. Record commit, tests, and live-testing
limits in the release notes. Do not push private refs or archive backups.

## 6. Deploy and hand off

Publishing code does not install it in Home Assistant. Follow
[DOCS.md](../duepi_stove_addon/DOCS.md): replace the app folder, refresh the store,
update/rebuild, check displayed version and startup log, then verify telemetry
and changed behavior with the operator. Keep supported shutdown/cooling and
physical controls available. Preserve a known-good app copy for rollback.

Before ending a session, update changelog and known issues with what changed,
what was tested, what remains uncertain, and the next useful step. Move superseded
private research/backups to a dated local archive with an inventory; never publish
that archive or discard active source, tests, or needed evidence.

New-chat prompt: **Read AGENTS.md, docs/ARCHITECTURE.md, docs/MAINTENANCE.md, and
KNOWN_ISSUES.md; inspect current Git status, then continue the requested update.**
