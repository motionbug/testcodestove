# Install the Duepi Stove Controller in Home Assistant

This guide installs the local MQTT climate bridge. No HACS integration,
`custom_components` directory, or manual `climate:` YAML entry is needed.
Current Home Assistant calls add-ons **Apps**; older versions use **Add-ons**.

## Deployment status

This is an **experimental community preview**, not a validated release. Offline
regression tests and reference capture decoding pass. On one Home Assistant
installation, version 1.0.1 started, connected to MQTT, and published discovery;
1.0.2 received live telemetry, acknowledged a power command, and read power P4.
However, intermittent reply timeouts/reconnects and reverting power selections
remain unresolved. Ten-byte unsolicited frames are rejected by the encrypted
frame parser; their meaning has not been established. Finish the startup checks
below before redirecting the stove, and do not rely on it for unattended heating.

One packaging check remains: `Dockerfile` defaults to an **aarch64** base even
though the manifest advertises other architectures. An amd64/armv7 host needs
a matching image build. This is an image requirement, not a user option.
Version **1.0.1** fixes the startup crash with Alpine's Paho MQTT 1.x package;
startup and discovery have been regression-tested with Paho 1.6.1 and 2.1.0.

## 1. Gather prerequisites

- Home Assistant OS with an administrator account and Apps available. Home
  Assistant Container/Core cannot install this app through this procedure.
- A Duepi/MyDPremote Wi-Fi module already connected to your LAN and its private
  device code (the code used to register the stove in MyDPremote, not its IP).
  If you do not have the code, recover it from your own device/app records before
  proceeding; do not paste captures or credentials into support requests.
- The stove module's current LAN IP and the Home Assistant host's LAN IP. They
  are different settings. Prefer DHCP reservations for both.
- Router access to override `duepiwebserver.com` for the stove, or configure a
  narrowly scoped destination NAT rule if DNS does not work.
- LAN reachability from stove to Home Assistant on TCP **3000**, plus an MQTT
  broker reachable by both the bridge and Home Assistant. Do not port-forward
  this controller from the Internet.

Only one stove is supported per broker with the current fixed MQTT identifiers.
Stop any standalone Node controller and close MyDPremote during initial setup.

## 2. Prepare MQTT

1. Install and start the official **Mosquitto broker** app, or use an existing
   private broker.
2. Under **Settings → Devices & services**, configure the discovered MQTT
   integration, or choose **Add integration → MQTT**. Connect it to that broker.
3. Keep MQTT discovery enabled with the prefix `homeassistant`.

For the official broker, leave the bridge's `mqtt_user` and `mqtt_password`
empty: `run.sh` obtains its connection details from Supervisor's MQTT service.
For an external broker, provide its host, port, and explicit credentials in
step 4. Home Assistant must use the same broker. This bridge currently uses
plain MQTT TCP; MQTT TLS and WebSocket options are not implemented.

See the [official MQTT setup guide](https://www.home-assistant.io/integrations/mqtt/).

## 3. Copy and install the local app

The simplest copy method is the official **Samba share** app:

1. Install Samba share, configure its login, enable its `local_apps` share, and
   start it.
2. On a Mac, open Finder → Go → Connect to Server and enter
   `smb://<HOME_ASSISTANT_LAN_IP>`. On Windows use
   `\\<HOME_ASSISTANT_LAN_IP>\local_apps` in File Explorer.
3. Open `local_apps` and copy the repository's **entire `duepi_stove_addon`
   folder** into it. The resulting layout must be:

   ```text
   local_apps/
     duepi_stove_addon/
       config.yaml
       Dockerfile
       run.sh
       stove_bridge.py
       README.md
       DOCS.md
   ```

Older Samba versions call this share `addons`; it points at the same local
app location. A terminal/editor can be used instead if it actually exposes
the local-app directory (often `/local_apps` or legacy `/addons`). Do not copy
into Home Assistant's `/config`, `custom_components`, or a nested second
`duepi_stove_addon` folder. Do not copy `dpremote/relay.pcap`.

4. Open **Settings → Apps → Install app** (older UI: **Settings → Add-ons →
   Add-on Store**), refresh/check for updates from its menu, and look under
   **Local apps / Local add-ons**.
5. Open **Duepi Stove Local MITM & MQTT Controller** and select **Install**.
   The host builds it locally. Wait for installation to finish; inspect
   Supervisor logs if it fails. No repository URL is required; the URL in
   `config.yaml` links to the source repository; use the local-folder installation
   described here rather than assuming an app-store repository is configured.

See the [official Samba guide](https://github.com/home-assistant/addons/blob/master/samba/DOCS.md)
for share access and naming.

## 4. Configure and start the bridge

Open the app's **Configuration** tab. Set your real device code and stove IP.
This complete example uses the official Mosquitto broker; replace the two
placeholder values before saving:

```yaml
device_code: "YOUR_PRIVATE_DEVICE_CODE"
stove_ip: "192.0.2.10"
listen_port: 3000
poll_interval: 15
upstream_relay_enabled: false
upstream_relay_host: "80.241.219.49"
upstream_relay_port: 3000
mqtt_host: "core-mosquitto"
mqtt_port: 1883
mqtt_user: ""
mqtt_password: ""
discovery_prefix: "homeassistant"
```

For an external broker, replace `mqtt_host`, `mqtt_port`, `mqtt_user`, and
`mqtt_password`. When Supervisor offers an MQTT service and `mqtt_user` is empty,
its service details take precedence over the host/port options.

Keep `listen_port` and the app's exposed network port at **3000** for this guide.
The manifest maps container port 3000; changing only `listen_port` breaks that
mapping. Leave all `upstream_relay_*` settings at their defaults: passthrough is
reserved and is **not implemented**, so enabling it does not preserve app/cloud
control.

Save, start the app, and inspect **Logs**. Expect messages containing:

```text
Connected to MQTT broker
Listening for Duepi stove TCP connections on 0.0.0.0:3000
```

The MQTT connection must succeed, not merely print the initial “Connecting” line.
At this stage the climate entity may exist but be unavailable because the stove
has not been redirected. If the process exits or repeatedly restarts, resolve
that first. Enable **Start on boot** after these startup checks succeed.

## 5. Redirect the stove to Home Assistant

1. In your router/DNS server, override **`duepiwebserver.com` → the Home Assistant
   host's LAN IP**, not the stove IP. Ensure the stove actually uses that DNS
   server. Router menu labels vary by firmware; use its local DNS/host override
   settings.
2. Allow stove → Home Assistant TCP 3000 across any VLAN/firewall boundary.
3. Reconnect the Wi-Fi module using its supported procedure so it opens a new
   TCP connection. Follow the stove manufacturer's shutdown procedure before
   any power cycle; do not cut power during combustion or cooling.
4. Check the bridge log for `Stove TCP connection established from` and confirm
   the reported source is the configured `stove_ip`.

If DNS is ignored because the module caches/hardcodes the vendor address, a
router destination NAT rule can redirect traffic **from the stove IP only**,
destined for `80.241.219.49:3000` or `62.141.46.29:3000`, to the Home Assistant
host on port 3000. These are reference-capture addresses, not guaranteed current
vendor endpoints. Preserve the source IP; source NAT may make the IP allowlist
reject the connection. Never redirect all clients or all TCP traffic.

This replaces the vendor connection. The MyDPremote app and cloud passthrough
are not supported by the local controller.

## 6. Verify the installation

1. In **Settings → Devices & services → MQTT**, find **Duepi Pellet Stove**.
   Open its climate entity; its actual entity ID may differ from
   `climate.duepi_stove` if Home Assistant renamed it or a previous entity exists.
2. Confirm current temperature, target temperature, and P1–P5 power update.
   Allow one polling cycle (15 seconds by default) plus connection time.
3. In MQTT's topic listener, inspect `duepi_stove/#`. Expect availability
   `online` and state messages. Discovery is retained at
   `homeassistant/climate/stove/config` (or your configured discovery prefix).
4. Add the climate entity to a dashboard. When ready to operate the stove,
   change one setting and confirm the **polled** state agrees with the physical
   stove; a command's optimistic state alone does not prove hardware response.

Do not enable heating automations until live readings and a deliberate control
command have been verified. A timeout closes the TCP session; subsequent
commands require the stove to reconnect. Control commands are not retried.

## 7. IP changes, updates, and rollback

- **Stove IP changed:** update `stove_ip`, any stove-specific NAT/firewall rules,
  and restart the app. The controller does not automatically discover new IPs.
- **Home Assistant IP changed:** update the router's DNS/NAT destination and
  reconnect the module. Prefer DHCP reservations to avoid either change.
- **Update local code:** back up app options, stop the app, and replace the
  files in the same local-app folder. Select **Check for updates** in the store.
  When the copied `config.yaml` has a newer version, select **Update** on the app
  page; Supervisor rebuilds its local image. For same-version source changes,
  use **Rebuild** if offered. Restarting alone retains the old image.
  Verify logs/readings again after updating.
- **Return to vendor control:** stop the bridge, remove the custom DNS/NAT
  overrides, and reconnect the module using its supported procedure. Confirm
  MyDPremote works again. Merely stopping the bridge leaves redirected traffic
  pointing at a stopped listener. Keep credentials private during removal.

## Troubleshooting

| Symptom | Check / next step |
|---|---|
| No Apps/Add-ons menu | Confirm this is Home Assistant OS; this install path does not apply to Container/Core. |
| Local app not listed | Check folder layout and enabled Samba share, refresh the store, and read Supervisor validation logs. |
| Build fails or `exec format error` | Check build logs, registry access, disk space, and base-image architecture. The current Dockerfile defaults to aarch64. |
| `CallbackAPIVersion` error | You are running 1.0.0 code. Copy the latest folder, check for store updates, and update/rebuild the app; restarting alone retains the old image. |
| Missing `device_code` / invalid `stove_ip` | Save the real values in the app Configuration tab and restart. Do not edit Home Assistant's main configuration YAML. |
| MQTT connection/authentication failure | Start the broker; check credentials, DNS, and port. With external MQTT, set an explicit username to avoid Supervisor's automatic service override. |
| Entity missing but MQTT connected | Ensure Home Assistant uses the same broker and discovery prefix. Inspect retained discovery and MQTT integration logs, then restart the bridge to republish. |
| Entity unavailable / no stove connection | Verify stove IP, DNS actually used by stove, host port 3000, VLAN rules, and that an old connection has been re-established. |
| Source IP differs from `stove_ip` | Check router source NAT and DHCP leases; preserve the original source IP. |
| Connected but polling times out | Check device code, a single active controller, repeated reconnects, and actual device replies. Any undocumented handshake requirement still needs target-device investigation. |
| Control state changes then reverts | Compare subsequent polled state and logs; the optimistic MQTT update does not confirm the stove accepted the command. |

## Configuration reference

| Option | Type | Default | Description |
|---|---|---|---|
| `stove_ip` | `str` | *(required)* | Current LAN IP of the stove module. Only this peer may connect; reserve its address in DHCP. |
| `device_code` | `password` | *(required)* | Your 10-character Duepi device code (without the trailing `#`), as sent in the `master:<code>#` login handshake. |
| `listen_port` | `port` | `3000` | TCP port where the add-on listens for the stove connection. |
| `poll_interval` | `int` | `15` | Seconds between status/temperature polling cycles (`5–300`). |
| `upstream_relay_enabled` | `bool` | `false` | Reserved for optional cloud relay passthrough. |
| `upstream_relay_host` | `str` | `80.241.219.49` | Upstream Contabo relay IP (`duepiwebserver.com`). |
| `upstream_relay_port` | `port` | `3000` | Upstream relay TCP port. |
| `mqtt_host` | `str` | `core-mosquitto` | MQTT broker hostname (auto-detected from Supervisor if Mosquitto add-on is installed). |
| `mqtt_port` | `port` | `1883` | MQTT broker port. |
| `mqtt_user` | `str` | `""` | Optional MQTT username (leave blank to auto-detect via Supervisor `bashio::services mqtt`). |
| `mqtt_password` | `password` | `""` | Optional MQTT password. |
| `discovery_prefix` | `str` | `homeassistant` | Home Assistant MQTT Discovery topic prefix. |

## MQTT Topics Published & Subscribed

| Topic | Direction | Retain | Description |
|---|---|---|---|
| `homeassistant/climate/stove/config` | Add-on → HA | Yes | MQTT Discovery JSON configuration payload |
| `duepi_stove/availability` | Add-on → HA | Yes | `online` when stove TCP socket is connected, `offline` otherwise |
| `duepi_stove/state/mode` | Add-on → HA | Yes | `heat` or `off` |
| `duepi_stove/state/action` | Add-on → HA | Yes | `heating`, `idle` (Eco Stop), or `off` |
| `duepi_stove/state/current_temperature` | Add-on → HA | Yes | Ambient temperature in `°C` (`0.1 °C` resolution) |
| `duepi_stove/state/target_temperature` | Add-on → HA | Yes | Target temperature setpoint (`16–35 °C`) |
| `duepi_stove/state/fan_mode` | Add-on → HA | Yes | Power level `P1`–`P5` |
| `duepi_stove/state/attributes` | Add-on → HA | Yes | JSON with phase/error, reported power, requested power, and command outcome |
| `duepi_stove/cmd/mode` | HA → Add-on | No | Accepts `heat` (`RF0010..RF0050`) or `off` (`RF0000`) |
| `duepi_stove/cmd/target_temperature` | HA → Add-on | No | Accepts `16`–`35` (`RF2<hex>0`) |
| `duepi_stove/cmd/fan_mode` | HA → Add-on | No | Accepts `P1`–`P5` (`RF0010`–`RF0050`) |


## References and validation limits

- [Home Assistant Apps / supported installation](https://www.home-assistant.io/apps/)
- [MQTT setup and discovery](https://www.home-assistant.io/integrations/mqtt/)
- [Samba local-app file access](https://github.com/home-assistant/addons/blob/master/samba/DOCS.md)
- [Current app image build requirements](https://contributors.home-assistant.io/blog/2026/04/02/builder-migration/)

Documentation was checked against the repository configuration and official
Home Assistant guidance. Completing the steps on the target Home Assistant host
is still required to validate packaging, discovery, network routing, and physical
stove behavior.


### Power selection diagnostics (1.0.2)

If selecting P1–P5 changes the display and it then reverts, check the app log
for `MQTT command received`, `Sending power command`, `Stove reply`, and
`Power readback`. These distinguish command delivery from the value returned
by the stove. Invalid-frame warnings include frame length and the pending
command, without dumping device codes, keys, or raw frame contents.
Version 1.0.2 adds diagnostics; it does not change the power protocol or establish
that every stove model accepts the existing power commands. Send a log covering
one deliberate selection and the following polling cycle when investigating.

### Power command coordination (1.0.3)

Power/fan state now comes from stove readback. A successful command reply alone
does not change the reported fan level. Complete polls and command verification
run sequentially; simultaneous refresh requests share the completed poll, and
newer queued power requests replace older ones before they reach the stove.

The climate entity's attributes include:

- `requested_power`: latest requested level, separately from `power_level` readback.
- `power_command_status`: `idle`, `queued`, `acknowledged`, `confirmed`,
  `mismatched`, `cancelled`, `expired`, or `unknown`. Confirmed means the setting
  matched a readback; it does not measure physical fan speed.
- `power_request_pending`: whether that request remains eligible for startup
  verification. A confirmed setting during startup can still be pending until
  normal running begins.

A request observed during startup (phase `01`/`02`) may be resent **once** on
transition into running (phase `04`) if the setting still differs. An explicit
Heat command may first wait for ignition. Requests expire after 15 minutes and
cannot cross TCP connections. Off, a newer power intent, an alarm, or leaving
the heating cycle cancels recovery. A fan selection while already off does not
schedule a future startup resend. TCP timeouts still close the connection;
uncertain writes are not automatically retried after reconnect.

Diagnostics describe the latest completed snapshot; retained attributes may be
old while availability is offline. The existing protocol registers and four-second
exchange timeout are unchanged. This version has offline regression coverage,
but the startup recovery behavior still needs validation on the physical stove.

To update a local installation, replace the files in `local_apps/duepi_stove_addon`,
refresh the local app store, and update/rebuild the app to **1.0.3**. Verify the
display and readback for one P1–P5 change during stable running, then during a
normal startup, and retain the app log if the setting still differs. Use the
checked-out source for 1.0.3; earlier ZIP snapshots are superseded.
