# Contributing

Start with [AGENTS.md](AGENTS.md), the [architecture](docs/ARCHITECTURE.md),
and the [maintenance and publication procedure](docs/MAINTENANCE.md).
Update [CHANGELOG.md](CHANGELOG.md) and [KNOWN_ISSUES.md](KNOWN_ISSUES.md)
when behavior or validation status changes.

Run the Node and Python test commands in the README before proposing a change.
Keep protocol checksums, framing, and supported limits consistent across both
implementations. Python uses four spaces and snake_case; JavaScript uses two
spaces, CommonJS, and camelCase.

Add focused regression tests for command ordering, timeout/session boundaries,
and changes to requested versus observed state. Describe what changed, the
commands you ran, and any hardware verification. State clearly when behavior
has only been tested with simulated devices.

Issues should include app version, Home Assistant architecture, stove/module
model, reproduction steps, and sanitized log excerpts. Remove device codes,
derived keys, MQTT credentials, local addresses, hostnames, and device IDs.
Do not upload raw captures or options.json.

Keep third-party attribution and MIT notices with any copied/adapted code.
Use concise imperative commit messages, such as `Fix power verification after startup`.
