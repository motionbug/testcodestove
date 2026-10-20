# Duepi Stove Local MITM & MQTT Controller (Home Assistant Add-on)

Local, cloud-free TCP relay and MQTT Discovery climate bridge for **Duepi Group** pellet stoves equipped with the **MyDPremote** Wi-Fi module (`com.duepi.mydpremote`).

## Implemented features (experimental)

- **100% Local Control (Zero Hardware Changes):** Reuses the stove's existing ESP8266 Wi-Fi module (`ESP8266 Wi-Fi module`) by impersonating `duepiwebserver.com:3000` on your LAN.
- **Native Home Assistant `climate` Entity:** Automatically registers `climate.duepi_stove` via MQTT Discovery (`homeassistant/climate/stove/config`) — no custom component (`custom_components`) required.
- **Full AES-128-CBC Cloud Protocol Support:** Implements the exact key derivation (`MD5(code) XOR 23FADC18A422678312C86AC29281AC01`) and frame encryption used by the ESP8266 module.
- **Supports:**
  - Turning the stove **ON (`heat`)** and **OFF (`off`)**
  - Adjusting **Target Temperature (`16 °C` – `35 °C`)**
  - Reading **Current Ambient Temperature (`0.1 °C` resolution)**
  - Selecting **Power / Fan Levels (`P1` – `P5`)**
  - Diagnostic JSON attributes for observed power, requested power, and command outcome
  - Coordinated polling and one guarded startup power resend (1.0.3; awaiting hardware validation)

## Required configuration

Set `device_code` and `stove_ip` before starting. Use the stove module’s current LAN IP;
if it changes, update `stove_ip` and restart the add-on. A DHCP reservation is recommended.
Follow [DOCS.md](DOCS.md) for the full Home Assistant installation, MQTT setup,
network cutover, verification, and rollback. Read its deployment-status section:
one live installation works partially, but communication timeouts and reverting
power selections remain unresolved. This is an experimental preview.
