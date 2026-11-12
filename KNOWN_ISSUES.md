# Validation status and known issues

Snapshot: 2026-11-12, version 1.0.6. Includes Paho compatibility, diagnostic
logging, coordinated polling, guarded startup and persistent-mismatch power
recovery, stale TCP session replacement, post-connect and post-write settle
delays, `450ms` inter-command pacing, reconnect command delivery within a
20-second availability grace window, and a wrong device code warning. Earlier
ZIP snapshots are superseded by this sanitized source distribution.

## Verified evidence

- 19 Node tests and 41 Python tests; Python suite exercised with Paho 1.6.1 and 2.1.0.
- A private reference capture previously decoded with 109/109 valid checksums.
  It is intentionally excluded from this distribution.
- One user installation built/started the Home Assistant app, connected to MQTT,
  published discovery, and received live temperature telemetry.
- The operator reported Heat/Off working. Logs show valid command ACKs and
  power/temperature readbacks alongside ESP8266 5-second TCP reconnect cycles
  when poll commands arrive immediately on connect or too soon after a write;
  versions 1.0.5–1.0.6 target these session/command pacing and reliability
  issues and await further live hardware validation.

## Unresolved behavior

- Power selection can change and then revert, especially during startup phases
  `0x01`/`0x02` where the stove controller locks power to its ignition recipe.
  P1–P5 maps to stove power, not an independently controlled room fan. Versions
  1.0.5–1.0.6 preserve `pending_power` across brief TCP reconnects and add a
  single guarded resend on persistent readback mismatch (`mismatch_polls >= 2`
  in `0x02`/`0x04`) in addition to the `0x01`/`0x02` → `0x04` transition; live
  hardware validation is still required.
- Some polls, temperature commands, and power commands time out. The bridge closes
  the session after a timeout to prevent late replies satisfying another request,
  replaces stale sockets immediately when `stove_ip` reconnects, waits `1.0s`
  after TCP connect, paces commands by `450ms` (`900ms` after control writes),
  and holds availability and user commands across brief reconnects (up to 20s)
  with at most one delivery retry when no reply was received.
- Ten-byte unsolicited frames (`\x1b00000000&`) arrive when a TCP connection sits
  idle for ~10 seconds with no command pending. Version 1.0.5 wakes the poll loop
  on reconnect and logs unsolicited 10-byte idle frames at debug level instead of
  warning when no command is pending.
- Online availability indicates a connected socket, not guaranteed fresh readings.
  Version 1.0.3 publishes observed climate state and separate requested-power
  diagnostics; a command ACK alone does not establish physical application.
- One stove per broker: MQTT client ID, topics, and device ID are fixed. The
  public export's generic identifier is not automatic multi-device support.
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
