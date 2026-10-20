# Validation status and known issues

Snapshot: 2026-10-20, version 1.0.3. Includes Paho compatibility, diagnostic
logging, coordinated polling, and guarded startup power recovery. Earlier ZIP
snapshots are superseded by this sanitized source distribution.

## Verified evidence

- 17 Node tests and 29 Python tests; Python suite exercised with Paho 1.6.1 and 2.1.0.
- A private reference capture previously decoded with 109/109 valid checksums.
  It is intentionally excluded from this distribution.
- One user installation built/started the Home Assistant app, connected to MQTT,
  published discovery, and received live temperature telemetry.
- The operator reported Heat/Off working. Logs show a valid P2 command ACK and
  repeated P4 readbacks; this does not establish reliable operation of all levels.

## Unresolved behavior

- Power selection can change and then revert. P1–P5 maps to stove power, not an
  independently controlled room fan. The latest observations occurred during
  ignition phases; a startup restriction is a hypothesis, not a confirmed cause.
- Some polls, temperature commands, and power commands time out. The bridge closes
  the session after a timeout to prevent late replies satisfying another request.
  The module reconnects; uncertain commands are never replayed across sessions.
  Version 1.0.3 allows one same-session power resend at observed startup-to-running
  transition after an acknowledged request; this still needs hardware validation.
- Ten-byte unsolicited frames have been observed while no command is pending.
  Their contents and purpose remain unknown. The parser rejects them as encrypted
  frames. They are not proven to cause the timeouts.
- Online availability indicates a connected socket, not guaranteed fresh readings.
  Version 1.0.3 publishes observed climate state and separate requested-power
  diagnostics; a command ACK alone does not establish physical application.
- One stove per broker: MQTT client ID, topics, and device ID are fixed. The
  archive's generic identifier is not automatic multi-device support.
- Retained command replay protection and richer command-failure diagnostics need
  additional work. Do not retain commands on `duepi_stove/cmd/#`.
- The manifest lists aarch64/amd64/armv7 but Dockerfile defaults to aarch64.
  Cross-architecture builds and general compatibility are not validated.
- Cloud/app passthrough is not implemented. IP allowlisting is not cryptographic
  peer authentication. MQTT TLS is not implemented in this bridge.

Use attended tests first, follow the manufacturer's operating/shutdown procedures,
and retain the physical controls. Avoid unattended heating automations until the
specific installation is validated. To roll back, stop the bridge, remove the
DNS/NAT overrides, and reconnect the module using its supported procedure.
