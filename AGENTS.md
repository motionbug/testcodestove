# Repository Guidelines

## Start Here

Read `docs/ARCHITECTURE.md`, `docs/MAINTENANCE.md`, `CHANGELOG.md`, and
`KNOWN_ISSUES.md` before changing behavior. Inspect Git status and preserve
unrelated work. Current app version is 1.0.3; hardware reliability remains
under investigation. These files provide continuity without earlier chat history.

## Project Structure

`duepi_stove_addon/` contains the Python TCP/MQTT bridge, app manifest, container
packaging, and installation guide. `dpremote/stove.js` is an alternative standalone
Node TCP/HTTP controller. `tests/` contains simulated transport regressions;
`docs/images/` contains approved screenshots. `SPEC.md` describes interoperability
observations. Do not add private research or vendor artifacts.

## Development and Validation

Run `node --test tests/*.test.js`. Create a Python virtualenv, install
`requirements-dev.txt`, and run `python -m unittest discover -s tests -p 'test_*.py' -q`.
Check entrypoint syntax with `bash -n duepi_stove_addon/run.sh`. Maintain Paho
1.6.1 and 2.x compatibility using separate environments. Add focused regressions
for changed behavior. Match existing Python four-space/snake_case and JavaScript
two-space/CommonJS/camelCase conventions.

## Transport and Appliance Rules

Preserve connection-bound reply ownership and close-on-timeout. Never replay
uncertain writes after reconnect. Keep requested power distinct from readback;
Off, newer intent, alarm, disconnect, or expiry must cancel deferred recovery.
Coordinate physical tests with the operator. Do not equate simulations or ACKs
with physical appliance confirmation.

## Publishing and Attribution

Follow `docs/MAINTENANCE.md` before committing or publishing. Review every intended
file, staged diff, image, and archive for private information. Never copy private
Git history, captures, logs, credentials, workspace paths, or installation IDs.
Preserve Florian Casse / DuepiRemoteHA attribution and full MIT notices.

Use concise imperative commit subjects; PRs describe behavior, validation, and
remaining risks. Update app version for runtime changes and keep changelog/known
issues current. Normal updates use ordinary pushes or PRs; history rewrites need
separate explicit approval. Leave these documents current for the next session.
