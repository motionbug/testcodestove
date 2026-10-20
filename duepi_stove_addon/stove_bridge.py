#!/usr/bin/env python3
"""
Home Assistant Add-on: Duepi (MyDPremote) Stove Local MITM Relay & MQTT Climate Bridge.

Responsibilities:
1. Derives the 16-byte AES-128-CBC key from the stove's device_code:
     AES_KEY = MD5(device_code) XOR bytes.fromhex("23FADC18A422678312C86AC29281AC01")
2. Listens on TCP port 3000 to accept the stove's outbound connection when
   `duepiwebserver.com` is pointed to Home Assistant in local DNS (with optional
   access restricted to the configured stove IP).
3. Connects to the MQTT broker and publishes the Home Assistant MQTT Discovery
   payload to `homeassistant/climate/stove/config`.
4. Polls the stove for state/temperatures and processes MQTT climate commands
   (mode `heat`/`off`, target temperature `16..35`, fan/power level `P1..P5`).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import logging
import os
import re
import sys
from pathlib import Path
from time import monotonic
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import paho.mqtt.client as mqtt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("duepi_stove")

KEY_MASK = bytes.fromhex("23FADC18A422678312C86AC29281AC01")

# Duepi 6-char command codes
CMD_GET_STATUS = "RD9000"
CMD_GET_AMBIENT_TEMP = "RD1000"
CMD_GET_FLUE_TEMP = "RD0000"
CMD_GET_SETPOINT = "RC6000"
CMD_GET_POWER = "RC5000"
CMD_GET_ERROR = "RDA000"
CMD_POWER_OFF = "RF0000"
CMD_RESET_ERROR = "RD6000"

POWER_COMMANDS = {
    "P1": "RF0010",
    "P2": "RF0020",
    "P3": "RF0030",
    "P4": "RF0040",
    "P5": "RF0050",
}

# A startup request belongs only to this connection and this heating cycle.
POWER_REQUEST_LIFETIME = 15 * 60


# ---------------------------------------------------------------------------
# 1. Duepi Cloud Protocol & AES-128-CBC Codec
# ---------------------------------------------------------------------------

def derive_key(device_code: str) -> bytes:
    """Derive 16-byte AES key: MD5(code_without_#) XOR KEY_MASK."""
    clean = device_code.strip().rstrip("#").encode("utf-8")
    md5_digest = hashlib.md5(clean).digest()
    return bytes(a ^ b for a, b in zip(md5_digest, KEY_MASK))


def duepi_checksum(cmd6: str) -> str:
    """Compute 2-char uppercase hex checksum (sum of ASCII bytes & 0xFF)."""
    return f"{sum(cmd6.encode('ascii')) & 0xFF:02X}"


def encrypt_frame(cmd6: str, key: bytes, iv: bytes | None = None) -> bytes:
    """
    Build a 46-byte encrypted frame:
      0x1B + Base64( IV_16B || AES_128_CBC(key, IV, zero_padded(cmd6 + checksum)) ) + '&'
    """
    payload8 = (cmd6 + duepi_checksum(cmd6)).encode("ascii")
    padded16 = payload8.ljust(16, b"\x00")
    iv16 = iv if iv is not None else os.urandom(16)
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv16)).encryptor()
    ciphertext16 = encryptor.update(padded16) + encryptor.finalize()
    b64 = base64.b64encode(iv16 + ciphertext16)
    return b"\x1b" + b64 + b"&"


def decrypt_frame(frame: bytes, key: bytes) -> tuple[str, bool]:
    """
    Decrypt a 46-byte `\x1b<base64>&` frame.
    Returns (body_6char, checksum_valid).
    """
    clean_b64 = frame.strip()
    if clean_b64.startswith(b"\x1b"):
        clean_b64 = clean_b64[1:]
    if clean_b64.endswith(b"&"):
        clean_b64 = clean_b64[:-1]

    if not re.fullmatch(rb"[A-Za-z0-9+/]{43}=", clean_b64):
        raise ValueError("Invalid frame encoding")
    raw = base64.b64decode(clean_b64, validate=True)
    if len(raw) != 32:
        raise ValueError(f"Unexpected decoded frame length: {len(raw)}")

    iv16, ct = raw[:16], raw[16:]
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv16)).decryptor()
    pt = decryptor.update(ct) + decryptor.finalize()
    text = pt.split(b"\x00", 1)[0].decode("latin1")
    if not re.fullmatch(r"[A-Z0-9]{6}[0-9A-Fa-f]{2}", text):
        return text, False
    body6, chk2 = text[:-2], text[-2:].upper()
    return body6, (duepi_checksum(body6) == chk2)


# ---------------------------------------------------------------------------
# 2. Stove Bridge (TCP MITM/Relay + MQTT Discovery & Loops)
# ---------------------------------------------------------------------------

class DuepiStoveBridge:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        try:
            self.stove_ip = ipaddress.ip_address(config.get("stove_ip", ""))
        except ValueError as exc:
            raise ValueError("Set stove_ip to the stove IP address") from exc
        if isinstance(self.stove_ip, ipaddress.IPv6Address) and self.stove_ip.ipv4_mapped:
            self.stove_ip = self.stove_ip.ipv4_mapped
        self.device_code: str = config["device_code"].strip().rstrip("#")
        self.key: bytes = derive_key(self.device_code)
        self.listen_port: int = int(config.get("listen_port", 3000))
        self.poll_interval: int = int(config.get("poll_interval", 15))

        # MQTT topics
        prefix = config.get("discovery_prefix", "homeassistant").rstrip("/")
        self.discovery_topic = f"{prefix}/climate/stove/config"
        self.base_topic = "duepi_stove"
        self.availability_topic = f"{self.base_topic}/availability"
        self.state_mode_topic = f"{self.base_topic}/state/mode"
        self.state_action_topic = f"{self.base_topic}/state/action"
        self.state_curr_temp_topic = f"{self.base_topic}/state/current_temperature"
        self.state_targ_temp_topic = f"{self.base_topic}/state/target_temperature"
        self.state_fan_mode_topic = f"{self.base_topic}/state/fan_mode"
        self.state_attrs_topic = f"{self.base_topic}/state/attributes"

        self.cmd_mode_topic = f"{self.base_topic}/cmd/mode"
        self.cmd_temp_topic = f"{self.base_topic}/cmd/target_temperature"
        self.cmd_fan_topic = f"{self.base_topic}/cmd/fan_mode"

        # Runtime state
        self.loop: asyncio.AbstractEventLoop | None = None
        self.stove_writer: asyncio.StreamWriter | None = None
        self.cmd_lock = asyncio.Lock()
        self.operation_lock = asyncio.Lock()
        self.poll_generation = 0
        self.intent_generation = 0
        self.requested_power: str | None = None
        self.power_command_status = "idle"
        self.pending_power: dict[str, Any] | None = None
        self.last_phase: int | None = None
        self.pending_reply: asyncio.Future[tuple[str, bool]] | None = None
        self.pending_command: str | None = None
        self.last_power_level: str = "P1"

        # Setup MQTT client
        # Alpine 3.19 ships Paho 1.x, which has no versioned callback API.
        # Our connect callback accepts both the four- and five-argument forms.
        client_options: dict[str, Any] = {"client_id": "duepi_stove_bridge"}
        callback_api = getattr(mqtt, "CallbackAPIVersion", None)
        if callback_api is not None:
            client_options["callback_api_version"] = callback_api.VERSION2
        self.mqtt = mqtt.Client(**client_options)
        mqtt_user = os.environ.get("MQTT_USER") or config.get("mqtt_user")
        mqtt_pass = os.environ.get("MQTT_PASSWORD") or config.get("mqtt_password")
        if mqtt_user:
            self.mqtt.username_pw_set(mqtt_user, mqtt_pass)

        self.mqtt.will_set(self.availability_topic, payload="offline", qos=1, retain=True)
        self.mqtt.on_connect = self._on_mqtt_connect
        self.mqtt.on_message = self._on_mqtt_message

    # --- MQTT Discovery & Callbacks ---

    def build_discovery_payload(self) -> dict[str, Any]:
        """Construct the Home Assistant MQTT Climate discovery payload."""
        return {
            "name": "Duepi Pellet Stove",
            "unique_id": "duepi_pellet_stove_climate",
            "object_id": "duepi_stove",
            "availability_topic": self.availability_topic,
            "payload_available": "online",
            "payload_not_available": "offline",
            "modes": ["off", "heat"],
            "mode_state_topic": self.state_mode_topic,
            "mode_command_topic": self.cmd_mode_topic,
            "action_topic": self.state_action_topic,
            "current_temperature_topic": self.state_curr_temp_topic,
            "temperature_state_topic": self.state_targ_temp_topic,
            "temperature_command_topic": self.cmd_temp_topic,
            "min_temp": 16,
            "max_temp": 35,
            "temp_step": 1,
            "temperature_unit": "C",
            "fan_modes": ["P1", "P2", "P3", "P4", "P5"],
            "fan_mode_state_topic": self.state_fan_mode_topic,
            "fan_mode_command_topic": self.cmd_fan_topic,
            "json_attributes_topic": self.state_attrs_topic,
            "device": {
                "identifiers": ["duepi_stove_local"],
                "name": "Duepi Pellet Stove",
                "manufacturer": "Duepi Group",
                "model": "EVO / MyDPremote Wi-Fi",
            },
        }

    def _on_mqtt_connect(self, client: mqtt.Client, userdata: Any, flags: Any, rc: Any, properties: Any = None) -> None:
        log.info("Connected to MQTT broker (rc=%s). Publishing discovery to %s", rc, self.discovery_topic)
        payload = self.build_discovery_payload()
        client.publish(self.discovery_topic, json.dumps(payload), qos=1, retain=True)
        client.subscribe([
            (self.cmd_mode_topic, 1),
            (self.cmd_temp_topic, 1),
            (self.cmd_fan_topic, 1),
        ])
        # Mark online if stove socket is already connected
        status = "online" if self.stove_writer is not None else "offline"
        client.publish(self.availability_topic, status, qos=1, retain=True)

    def _on_mqtt_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        payload = msg.payload.decode("utf-8", errors="ignore").strip()
        log.info("MQTT command received on %s: %s", msg.topic, payload)
        if self.loop is not None:
            asyncio.run_coroutine_threadsafe(self.handle_mqtt_command(msg.topic, payload), self.loop)

    async def handle_mqtt_command(self, topic: str, payload: str) -> None:
        """Translate Home Assistant MQTT climate commands into encrypted Duepi frames."""
        writer = self.stove_writer
        power = None
        if topic == self.cmd_mode_topic and payload in ("off", "heat"):
            power = self.last_power_level if payload == "heat" else None
            if payload == "heat":
                pending = self.pending_power
                if (pending is not None and pending["writer"] is writer
                        and monotonic() < pending["deadline"]):
                    power = pending["power"]
                elif self.power_command_status == "queued" and self.requested_power:
                    power = self.requested_power
            cmd6 = POWER_COMMANDS[power] if power else CMD_POWER_OFF
        elif topic == self.cmd_fan_topic and payload in POWER_COMMANDS:
            power = payload
            cmd6 = POWER_COMMANDS[power]
        elif topic == self.cmd_temp_topic:
            try:
                target_c = max(16, min(35, round(float(payload))))
            except (ValueError, OverflowError):
                return
            cmd6 = f"RF2{target_c:02X}0"
        else:
            return

        if writer is None or writer.is_closing():
            log.warning("Ignoring command while stove is disconnected: %s", topic)
            return

        # Invalidate deferred work BEFORE waiting behind an in-progress poll.
        is_power = topic in (self.cmd_mode_topic, self.cmd_fan_topic)
        if is_power:
            self.intent_generation += 1
            self.pending_power = None
            self.requested_power = power
            self.power_command_status = "queued" if power else "cancelled"
        generation = self.intent_generation
        try:
            async with self.operation_lock:
                if writer is None or writer is not self.stove_writer:
                    return
                if is_power and generation != self.intent_generation:
                    return
                await self.send_command(cmd6)
                if power and generation == self.intent_generation:
                    self.power_command_status = "acknowledged"
                    self.pending_power = {
                        "power": power, "writer": writer, "generation": generation,
                        "deadline": monotonic() + POWER_REQUEST_LIFETIME,
                        "startup_seen": self.last_phase in (0x01, 0x02),
                        "awaiting_startup": topic == self.cmd_mode_topic and payload == "heat",
                        "resent": False,
                    }
                # Publish physical readback, not the setting we merely requested.
                await self._poll_locked(writer)
        except Exception as exc:
            if is_power and generation == self.intent_generation:
                self.pending_power = None
                self.power_command_status = "unknown"
            log.error("Failed to execute MQTT command (%s -> %s): %s", topic, payload, exc)

    # --- Stove TCP Server & Frame Exchange ---

    def close_session(self, writer: asyncio.StreamWriter, error: Exception) -> None:
        """Invalidate the current session before releasing any waiting command."""
        if self.stove_writer is writer:
            self.stove_writer = None
            self.intent_generation += 1
            if self.pending_power is not None or self.power_command_status == "queued":
                self.power_command_status = "unknown"
            self.pending_power = None
            self.last_phase = None
            self.mqtt.publish(self.availability_topic, "offline", qos=1, retain=True)
            if self.pending_reply is not None and not self.pending_reply.done():
                self.pending_reply.set_exception(error)
        writer.close()

    async def send_command(self, cmd6: str, timeout: float = 4.0) -> str:
        """Serialize commands and never carry queued work across a reconnect."""
        writer = self.stove_writer
        async with self.cmd_lock:
            if writer is None or writer is not self.stove_writer or writer.is_closing():
                raise RuntimeError("Stove TCP socket is not connected")

            assert self.loop is not None
            pending = self.loop.create_future()
            self.pending_reply = pending
            self.pending_command = cmd6
            try:
                if cmd6.startswith("RF0"):
                    log.info("Sending power command: %s", cmd6)
                writer.write(encrypt_frame(cmd6, self.key))
                await asyncio.wait_for(writer.drain(), timeout=timeout)
                body6, valid = await asyncio.wait_for(pending, timeout=timeout)
                if not valid or not re.fullmatch(r"[0-9A-Fa-f]{6}", body6):
                    raise ValueError("Invalid stove reply")
                if cmd6.startswith("RF0") or cmd6 == CMD_GET_POWER:
                    log.info("Stove reply: command=%s body=%s checksum=valid", cmd6, body6)
                return body6
            except BaseException:
                # No request IDs exist on this wire protocol. Reconnect after any
                # uncertain exchange so a late reply cannot satisfy another command.
                pending.cancel()
                self.close_session(writer, ConnectionError("Stove exchange interrupted"))
                raise
            finally:
                if self.pending_reply is pending:
                    self.pending_reply = None
                    self.pending_command = None

    async def handle_tcp_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        try:
            address = ipaddress.ip_address(peer[0])
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                address = address.ipv4_mapped
        except (ValueError, TypeError, IndexError):
            writer.close()
            return
        if address != self.stove_ip or self.stove_writer is not None:
            writer.close()
            return

        log.info("Stove TCP connection established from %s", peer)
        self.stove_writer = writer
        self.mqtt.publish(self.availability_topic, "online", qos=1, retain=True)
        buffer = b""
        try:
            while self.stove_writer is writer:
                chunk = await reader.read(256)
                if not chunk:
                    break
                if self.stove_writer is not writer:
                    break
                buffer += chunk
                while buffer:
                    esc_idx = buffer.find(b"\x1b")
                    plaintext = buffer if esc_idx < 0 else buffer[:esc_idx]
                    # Preserve partial handshakes without logging credentials.
                    if b"master:" in plaintext:
                        raise ValueError("App clients are not supported")
                    if esc_idx < 0:
                        break
                    buffer = buffer[esc_idx:]
                    amp_idx = buffer.find(b"&")
                    if amp_idx < 0:
                        break
                    frame = buffer[buffer.rfind(b"\x1b", 0, amp_idx):amp_idx + 1]
                    buffer = buffer[amp_idx + 1:]
                    try:
                        body6, valid = decrypt_frame(frame, self.key)
                        if not valid or not re.fullmatch(r"[0-9A-Fa-f]{6}", body6):
                            continue
                        if self.pending_reply is not None and not self.pending_reply.done():
                            self.pending_reply.set_result((body6, valid))
                    except (ValueError, UnicodeError) as exc:
                        log.warning(
                            "Invalid frame from %s: %s (frame_bytes=%d base64_bytes=%d pending_command=%s)",
                            peer, exc, len(frame), len(frame[1:-1]), self.pending_command or "none",
                        )
                if len(buffer) > 4096:
                    raise ValueError("TCP buffer limit exceeded")
        except Exception as exc:
            log.warning("TCP connection error (%s): %s", peer, exc)
        finally:
            self.close_session(writer, ConnectionError("Stove disconnected"))
            await writer.wait_closed()

    # --- Periodic Polling Loop ---

    async def poll_once(self) -> None:
        """Coalesce concurrent refreshes and keep each snapshot uninterrupted."""
        writer = self.stove_writer
        generation = self.poll_generation
        async with self.operation_lock:
            if generation != self.poll_generation:
                return
            if writer is None or writer is not self.stove_writer or writer.is_closing():
                return
            await self._poll_locked(writer)

    async def _poll_locked(self, writer: asyncio.StreamWriter) -> None:
        """Read a complete snapshot while operation_lock is held."""
        replies = []
        for command in (CMD_GET_STATUS, CMD_GET_AMBIENT_TEMP, CMD_GET_SETPOINT,
                        CMD_GET_POWER, CMD_GET_ERROR):
            if writer is not self.stove_writer:
                raise ConnectionError("Stove session changed during polling")
            replies.append(await self.send_command(command))
        if writer is not self.stove_writer:
            raise ConnectionError("Stove session changed during polling")
        status_raw, ambient_raw, setpoint_raw, power_raw, error_raw = replies
        self.poll_generation += 1

        phase = int(status_raw[0:2], 16)
        ambient_c = int(ambient_raw[0:4], 16) / 10.0
        setpoint_c = int(setpoint_raw[0:4], 16)
        power_lvl = int(power_raw[0:4], 16)
        error_code = int(error_raw[0:4], 16)
        # Never fabricate P1 from an unknown/invalid zero power register.
        fan_mode = f"P{power_lvl}" if 1 <= power_lvl <= 5 else None
        previous_phase = self.last_phase
        self.last_phase = phase
        request = self.pending_power
        resend = False
        if request is not None:
            waiting_for_ignition = (phase == 0 and request["awaiting_startup"]
                                   and not request["startup_seen"])
            if (request["writer"] is not writer
                    or request["generation"] != self.intent_generation
                    or error_code
                    or (phase not in (0x01, 0x02, 0x04, 0x40) and not waiting_for_ignition)):
                self.pending_power = None
                self.power_command_status = "cancelled"
            elif monotonic() >= request["deadline"]:
                self.pending_power = None
                self.power_command_status = "expired"
            else:
                request["startup_seen"] |= phase in (0x01, 0x02)
                self.power_command_status = (
                    "confirmed" if fan_mode == request["power"] else "mismatched"
                )
                resend = (fan_mode is not None and fan_mode != request["power"]
                          and request["startup_seen"] and not request["resent"]
                          and previous_phase in (0x01, 0x02) and phase == 0x04)
                if phase not in (0x01, 0x02) and not resend and not waiting_for_ignition:
                    self.pending_power = None

        self._publish_snapshot(phase, ambient_c, setpoint_c, power_lvl, error_code, fan_mode)
        if resend:
            # No await between final intent/session checks and entering send_command.
            # The TCP layer independently rejects a session change while queued.
            if (self.pending_power is not request
                    or request["generation"] != self.intent_generation
                    or writer is not self.stove_writer
                    or monotonic() >= request["deadline"]):
                return
            request["resent"] = True
            try:
                await self.send_command(POWER_COMMANDS[request["power"]])
            except BaseException:
                if self.pending_power is request:
                    self.pending_power = None
                if request["generation"] == self.intent_generation:
                    self.power_command_status = "unknown"
                raise
            if self.pending_power is request:
                self.power_command_status = "acknowledged"
                self._publish_snapshot(phase, ambient_c, setpoint_c, power_lvl, error_code, fan_mode)

    def _publish_snapshot(self, phase: int, ambient_c: float, setpoint_c: int,
                          power_lvl: int, error_code: int, fan_mode: str | None) -> None:
        """Publish observed state and separate command diagnostics."""
        if self.stove_writer is None:
            return

        # Map Duepi phase bitmask to Home Assistant climate mode & hvac_action
        # Phases 0x01 (Flame wait), 0x02 (Flame present), 0x04 (Running) => heat / heating
        # Phase 0x40 (Eco stop) => heat / idle
        # Phases 0x00, 0x08 (Off / Cooling), 0x10, 0x20, 0x80 => off / off
        if phase in (0x01, 0x02, 0x04):
            ha_mode = "heat"
            ha_action = "heating"
        elif phase == 0x40:
            ha_mode = "heat"
            ha_action = "idle"
        else:
            ha_mode = "off"
            ha_action = "off"

        log.info(
            "Power readback: decoded=%d reported=%s phase=0x%02X requested=%s outcome=%s",
            power_lvl, fan_mode, phase, self.requested_power, self.power_command_status,
        )
        if fan_mode is not None:
            self.last_power_level = fan_mode

        self.mqtt.publish(self.state_mode_topic, ha_mode, qos=1, retain=True)
        self.mqtt.publish(self.state_action_topic, ha_action, qos=1, retain=True)
        self.mqtt.publish(self.state_curr_temp_topic, f"{ambient_c:.1f}", qos=1, retain=True)
        self.mqtt.publish(self.state_targ_temp_topic, str(setpoint_c), qos=1, retain=True)
        if fan_mode is not None:
            self.mqtt.publish(self.state_fan_mode_topic, fan_mode, qos=1, retain=True)
        self.mqtt.publish(
            self.state_attrs_topic,
            json.dumps({
                "raw_phase": f"0x{phase:02X}",
                "error_code": error_code,
                "power_level": power_lvl,
                "requested_power": self.requested_power,
                "power_command_status": self.power_command_status,
                "power_request_pending": self.pending_power is not None,
            }),
            qos=1,
            retain=True,
        )

    async def poll_loop(self) -> None:
        while True:
            try:
                await self.poll_once()
            except Exception as exc:
                log.warning("Polling cycle error: %s: %s", type(exc).__name__, exc)
            await asyncio.sleep(self.poll_interval)

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        mqtt_host = os.environ.get("MQTT_HOST") or self.config.get("mqtt_host", "core-mosquitto")
        mqtt_port = int(os.environ.get("MQTT_PORT") or self.config.get("mqtt_port", 1883))

        log.info("Connecting to MQTT broker at %s:%d ...", mqtt_host, mqtt_port)
        self.mqtt.connect_async(mqtt_host, mqtt_port, keepalive=60)
        self.mqtt.loop_start()

        server = await asyncio.start_server(self.handle_tcp_client, "0.0.0.0", self.listen_port)
        log.info("Listening for Duepi stove TCP connections on 0.0.0.0:%d", self.listen_port)

        poll_task = asyncio.create_task(self.poll_loop())
        try:
            async with server:
                await server.serve_forever()
        finally:
            poll_task.cancel()
            self.mqtt.publish(self.availability_topic, "offline", qos=1, retain=True)
            self.mqtt.loop_stop()
            self.mqtt.disconnect()


def load_config() -> dict[str, Any]:
    if len(sys.argv) > 1 and Path(sys.argv[1]).is_file():
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "device_code": os.environ.get("DUEPI_CODE", ""),
        "stove_ip": os.environ.get("DUEPI_STOVE_IP", ""),
        "listen_port": int(os.environ.get("LISTEN_PORT", "3000")),
        "poll_interval": int(os.environ.get("POLL_INTERVAL", "15")),
        "mqtt_host": os.environ.get("MQTT_HOST", "localhost"),
        "mqtt_port": int(os.environ.get("MQTT_PORT", "1883")),
        "mqtt_user": os.environ.get("MQTT_USER", ""),
        "mqtt_password": os.environ.get("MQTT_PASSWORD", ""),
        "discovery_prefix": os.environ.get("DISCOVERY_PREFIX", "homeassistant"),
    }


if __name__ == "__main__":
    cfg = load_config()
    if not cfg.get("device_code"):
        log.error("Missing 'device_code' in /data/options.json or DUEPI_CODE environment variable.")
        sys.exit(1)
    asyncio.run(DuepiStoveBridge(cfg).run())
