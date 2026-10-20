import asyncio
import base64
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

spec = importlib.util.spec_from_file_location('stove_bridge', Path(__file__).resolve().parents[1] / 'duepi_stove_addon/stove_bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
CODE = '0123456789'


class Writer:
    def __init__(self, ip='192.0.2.10'):
        self.ip = ip
        self.closed = False
        self.frames = asyncio.Queue()

    def get_extra_info(self, name):
        return (self.ip, 1234)

    def is_closing(self):
        return self.closed

    def write(self, data):
        self.frames.put_nowait(data)

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.b = bridge.DuepiStoveBridge({'device_code': CODE, 'stove_ip': '192.0.2.10'})
        # Record MQTT output at the external broker boundary; keep bridge logic real.
        self.b.mqtt.publish = Mock()
        self.b.loop = asyncio.get_running_loop()
        self.tasks = []
        self.readers = []

    async def asyncTearDown(self):
        for reader in self.readers:
            reader.feed_eof()
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def connect(self, ip='192.0.2.10'):
        reader, writer = asyncio.StreamReader(), Writer(ip)
        self.readers.append(reader)
        task = asyncio.create_task(self.b.handle_tcp_client(reader, writer))
        self.tasks.append(task)
        await asyncio.sleep(0)
        return reader, writer

    async def command(self):
        task = asyncio.create_task(self.b.send_command('RF0010', timeout=0.2))
        self.tasks.append(task)
        await asyncio.sleep(0)
        return task

    def frame(self, body='000000', checksum=None):
        payload = (body + (checksum if checksum is not None else bridge.duepi_checksum(body))).encode().ljust(16, b'\0')
        iv = bytes(16)
        cipher = Cipher(algorithms.AES(bridge.derive_key(CODE)), modes.CBC(iv)).encryptor()
        return b'\x1b' + base64.b64encode(iv + cipher.update(payload) + cipher.finalize()) + b'&'

    async def test_all_fragment_boundaries(self):
        reader, writer = await self.connect()
        frame = self.frame()
        for split in range(1, 46):
            with self.subTest(split=split):
                task = await self.command()
                reader.feed_data(frame[:split]); await asyncio.sleep(0)
                reader.feed_data(frame[split:])
                self.assertEqual(await task, '000000')

    async def test_plaintext_and_malformed_frame_before_reply(self):
        reader, _ = await self.connect()
        task = await self.command()
        reader.feed_data(b'hello#\x1bbad&' + self.frame())
        self.assertEqual(await task, '000000')

    async def test_corrupt_reply_never_completes_command(self):
        reader, _ = await self.connect()
        task = await self.command()
        reader.feed_data(self.frame(checksum='00') + self.frame('ZZZZZZ'))
        await asyncio.sleep(0); await asyncio.sleep(0)
        self.assertFalse(task.done())
        reader.feed_data(self.frame())
        self.assertEqual(await task, '000000')

    async def test_timeout_closes_session_before_next_command(self):
        reader, writer = await self.connect()
        with self.assertRaises(asyncio.TimeoutError):
            await self.b.send_command('RF0010', timeout=0.001)
        self.assertTrue(writer.closed)
        with self.assertRaises(RuntimeError):
            await self.b.send_command('RF0020', timeout=0.001)

    async def test_disconnect_rejects_pending_without_waiting_for_timeout(self):
        reader, _ = await self.connect()
        task = await self.command()
        reader.feed_eof()
        with self.assertRaises(ConnectionError):
            await task

    async def test_rejects_unmatched_and_duplicate_connections(self):
        reader, current = await self.connect('::ffff:192.0.2.10')
        _, intruder = await self.connect('192.0.2.11')
        _, duplicate = await self.connect()
        self.assertTrue(intruder.closed)
        self.assertTrue(duplicate.closed)
        self.assertIs(self.b.stove_writer, current)
        self.assertFalse(current.closed)

    async def test_oversized_incomplete_frame_disconnects(self):
        reader, writer = await self.connect()
        reader.feed_data(b'\x1b' + b'A' * 5000)
        for _ in range(30):
            await asyncio.sleep(0)
        self.assertTrue(writer.closed)

    async def test_split_app_login_disconnects(self):
        reader, writer = await self.connect()
        reader.feed_data(b'mas'); await asyncio.sleep(0)
        reader.feed_data(b'ter:0123456789#')
        await asyncio.sleep(0); await asyncio.sleep(0)
        self.assertTrue(writer.closed)

    async def test_reconnect_does_not_accept_previous_session_data(self):
        old_reader, _ = await self.connect()
        old_reader.feed_data(self.frame()[:20]); await asyncio.sleep(0)
        old_reader.feed_eof(); await asyncio.sleep(0)
        reader, _ = await self.connect()
        task = await self.command()
        reader.feed_data(self.frame())
        self.assertEqual(await task, '000000')

    async def test_requires_valid_stove_ip(self):
        for config in [{'device_code': CODE}, {'device_code': CODE, 'stove_ip': 'not-an-ip'}]:
            with self.assertRaises(ValueError):
                bridge.DuepiStoveBridge(config)

    async def test_invalid_reply_cannot_publish_mqtt_state(self):
        reader, _ = await self.connect()
        self.b.mqtt.publish.reset_mock()
        task = asyncio.create_task(self.b.poll_once()); self.tasks.append(task)
        await asyncio.sleep(0)
        reader.feed_data(self.frame('040000', checksum='00'))
        await asyncio.sleep(0); await asyncio.sleep(0)
        self.b.mqtt.publish.assert_not_called()
        self.assertFalse(task.done())

    async def test_invalid_frame_diagnostic_identifies_request_without_credentials(self):
        reader, _ = await self.connect()
        task = await self.command()
        # A malformed frame might contain sensitive handshake bytes. Log sizes only.
        bad = b'\x1b' + CODE.encode() + b'!' * 34 + b'&'
        with self.assertLogs('duepi_stove', level='WARNING') as captured:
            reader.feed_data(bad)
            await asyncio.sleep(0)
        text = '\n'.join(captured.output)
        self.assertIn('frame_bytes=46', text)
        self.assertIn('pending_command=RF0010', text)
        self.assertNotIn(CODE, text)
        self.assertFalse(task.done())
        reader.feed_data(self.frame())
        self.assertEqual(await task, '000000')

    async def test_coalesced_frames_do_not_complete_next_command(self):
        reader, writer = await self.connect()
        first = await self.command()
        second = asyncio.create_task(self.b.send_command('RF0020', timeout=0.2))
        self.tasks.append(second)
        reader.feed_data(self.frame() + self.frame('001800'))
        self.assertEqual(await first, '000000')
        await asyncio.sleep(0)
        self.assertFalse(second.done())
        reader.feed_data(self.frame())
        self.assertEqual(await second, '000000')

    async def test_queued_commands_cannot_cross_reconnect(self):
        reader, writer = await self.connect()
        first = await self.command()
        second = asyncio.create_task(self.b.send_command('RF0020', timeout=0.2))
        self.tasks.append(second)
        await asyncio.sleep(0)
        reader.feed_eof()
        with self.assertRaises(ConnectionError):
            await first
        new_reader, new_writer = await self.connect()
        with self.assertRaises(RuntimeError):
            await second
        self.assertTrue(new_writer.frames.empty())
        third = await self.command()
        new_reader.feed_data(self.frame())
        self.assertEqual(await third, '000000')

    async def test_codec_matches_fixed_cross_language_vector(self):
        key = bridge.derive_key(CODE)
        self.assertEqual(key.hex(), '5be4823cf94bd2e58553ec201fa25ec6')
        vector = b'\x1bAAAAAAAAAAAAAAAAAAAAALLifCC1HkBdciXX+5IoTxY=&'
        self.assertEqual(bridge.encrypt_frame('RD9000', key, bytes(16)), vector)
        self.assertEqual(bridge.decrypt_frame(vector, key), ('RD9000', True))
        for frame in [b'\x1b' + b'!' * 44 + b'&', b'\x1bAAAA&']:
            with self.assertRaises(ValueError):
                bridge.decrypt_frame(frame, key)
        self.assertFalse(bridge.decrypt_frame(self.frame('0000'), key)[1])

    async def test_accepts_captured_esp_ram_tail_after_terminator(self):
        # The captured device leaves uninitialized bytes after the first NUL.
        payload = bytes.fromhex('30303030303032300026008800005420')
        key, iv = bridge.derive_key(CODE), bytes(16)
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        frame = b'\x1b' + base64.b64encode(iv + cipher.update(payload) + cipher.finalize()) + b'&'
        reader, _ = await self.connect()
        task = await self.command()
        reader.feed_data(frame)
        self.assertEqual(await task, '000000')

    async def stove(self, phase=1, power=2, error=0):
        reader, writer = await self.connect()
        state = {'phase': phase, 'power': power, 'error': error,
                 'commands': [], 'hold': None, 'entered': asyncio.Event(),
                 'release': asyncio.Event(), 'drop': None}

        async def respond():
            while True:
                frame = await writer.frames.get()
                cmd, _ = bridge.decrypt_frame(frame, self.b.key)
                state['commands'].append(cmd)
                if cmd == state['hold']:
                    state['entered'].set()
                    await state['release'].wait()
                if cmd == state['drop']:
                    reader.feed_eof()
                    continue
                value = {'RD9000': f"{state['phase']:02X}0000",
                         'RD1000': '00C800', 'RC6000': '001600',
                         'RC5000': f"{state['power']:04X}00",
                         'RDA000': f"{state['error']:04X}00"}.get(cmd, '000000')
                if not reader.at_eof():
                    reader.feed_data(self.frame(value))

        self.tasks.append(asyncio.create_task(respond()))
        return state, reader, writer

    def attrs(self):
        return json.loads([c.args[1] for c in self.b.mqtt.publish.call_args_list
                           if c.args[0] == self.b.state_attrs_topic][-1])

    async def test_requested_power_does_not_publish_unconfirmed_fan_state(self):
        state, _, _ = await self.stove()
        await self.b.handle_mqtt_command(self.b.cmd_fan_topic, 'P5')
        published = [c.args[1] for c in self.b.mqtt.publish.call_args_list
                     if c.args[0] == self.b.state_fan_mode_topic]
        self.assertEqual(published, ['P2'])
        self.assertEqual(self.attrs()['requested_power'], 'P5')
        self.assertEqual(self.attrs()['power_command_status'], 'mismatched')

    async def test_startup_request_resends_once_then_confirms_by_readback(self):
        state, _, _ = await self.stove()
        await self.b.handle_mqtt_command(self.b.cmd_fan_topic, 'P5')
        state['phase'] = 4
        await self.b.poll_once()
        self.assertEqual(state['commands'].count('RF0050'), 2)
        state['power'] = 5
        await self.b.poll_once()
        self.assertEqual(self.attrs()['power_command_status'], 'confirmed')
        state['power'], state['phase'] = 2, 1
        await self.b.poll_once()
        state['phase'] = 4
        await self.b.poll_once()
        self.assertEqual(state['commands'].count('RF0050'), 2)

    async def test_expiry_alarm_and_off_each_cancel_startup_resend(self):
        state, _, _ = await self.stove()
        for cancel in ('expiry', 'alarm', 'off'):
            with self.subTest(cancel=cancel):
                state['phase'], state['error'] = 1, 0
                await self.b.handle_mqtt_command(self.b.cmd_fan_topic, 'P5')
                before = state['commands'].count('RF0050')
                if cancel == 'expiry':
                    with patch.object(bridge, 'monotonic', return_value=10**12):
                        state['phase'] = 4
                        await self.b.poll_once()
                elif cancel == 'alarm':
                    state['error'] = 1
                    await self.b.poll_once()
                    state['phase'], state['error'] = 4, 0
                    await self.b.poll_once()
                else:
                    await self.b.handle_mqtt_command(self.b.cmd_mode_topic, 'off')
                    state['phase'] = 4
                    await self.b.poll_once()
                self.assertEqual(state['commands'].count('RF0050'), before)

    async def test_newer_power_supersedes_previous_startup_request(self):
        state, _, _ = await self.stove()
        await self.b.handle_mqtt_command(self.b.cmd_fan_topic, 'P5')
        await self.b.handle_mqtt_command(self.b.cmd_fan_topic, 'P3')
        state['phase'] = 4
        await self.b.poll_once()
        self.assertEqual(state['commands'].count('RF0050'), 1)
        self.assertEqual(state['commands'].count('RF0030'), 2)

    async def test_poll_coalesces_and_off_cancels_before_waiting_for_poll(self):
        state, _, _ = await self.stove()
        await self.b.handle_mqtt_command(self.b.cmd_fan_topic, 'P5')
        state['commands'].clear()
        state['phase'], state['hold'] = 4, 'RD1000'
        first = asyncio.create_task(self.b.poll_once())
        self.tasks.append(first)
        await state['entered'].wait()
        second = asyncio.create_task(self.b.poll_once())
        off = asyncio.create_task(self.b.handle_mqtt_command(self.b.cmd_mode_topic, 'off'))
        self.tasks.extend([second, off])
        await asyncio.sleep(0)
        state['release'].set()
        await asyncio.gather(first, second, off)
        self.assertNotIn('RF0050', state['commands'])
        # No nested poll interleaves with the complete five-read snapshot.
        self.assertEqual(state['commands'][:6],
                         ['RD9000', 'RD1000', 'RC6000', 'RC5000', 'RDA000', 'RF0000'])
        self.assertEqual(state['commands'].count('RD9000'), 2)

    async def test_pending_request_does_not_cross_session_or_failed_write(self):
        state, reader, writer = await self.stove()
        await self.b.handle_mqtt_command(self.b.cmd_fan_topic, 'P5')
        self.b.close_session(writer, ConnectionError('test disconnect'))
        reader.feed_eof()
        await asyncio.sleep(0)
        new, _, _ = await self.stove(phase=4)
        await self.b.poll_once()
        self.assertNotIn('RF0050', new['commands'])
        new['drop'] = 'RF0030'
        await self.b.handle_mqtt_command(self.b.cmd_fan_topic, 'P3')
        await asyncio.sleep(0)
        again, _, _ = await self.stove(phase=4)
        await self.b.poll_once()
        self.assertNotIn('RF0030', again['commands'])

    async def test_heat_preserves_pending_startup_power_instead_of_old_readback(self):
        state, _, _ = await self.stove()
        await self.b.handle_mqtt_command(self.b.cmd_fan_topic, 'P5')
        await self.b.handle_mqtt_command(self.b.cmd_mode_topic, 'heat')
        self.assertNotIn('RF0020', state['commands'])
        self.assertEqual(self.attrs()['requested_power'], 'P5')

    async def test_newest_queued_power_is_only_one_sent_after_poll(self):
        state, _, _ = await self.stove()
        state['hold'] = 'RD1000'
        poll = asyncio.create_task(self.b.poll_once())
        self.tasks.append(poll)
        await state['entered'].wait()
        old = asyncio.create_task(self.b.handle_mqtt_command(self.b.cmd_fan_topic, 'P5'))
        new = asyncio.create_task(self.b.handle_mqtt_command(self.b.cmd_fan_topic, 'P3'))
        self.tasks.extend([old, new])
        await asyncio.sleep(0)
        state['release'].set()
        await asyncio.gather(poll, old, new)
        self.assertNotIn('RF0050', state['commands'])
        self.assertEqual(state['commands'].count('RF0030'), 1)

    async def test_startup_confirmation_does_not_erase_intent_before_normal_running(self):
        state, _, _ = await self.stove(power=5)
        await self.b.handle_mqtt_command(self.b.cmd_fan_topic, 'P5')
        self.assertTrue(self.attrs()['power_request_pending'])
        state['power'], state['phase'] = 2, 4
        await self.b.poll_once()
        self.assertEqual(state['commands'].count('RF0050'), 2)
        # A valid but mismatched readback after the sole resend ends the request.
        await self.b.poll_once()
        self.assertEqual(self.attrs()['power_command_status'], 'mismatched')
        self.assertFalse(self.attrs()['power_request_pending'])

    async def test_idle_power_command_cannot_automatically_restart_later(self):
        state, _, _ = await self.stove(phase=0)
        await self.b.handle_mqtt_command(self.b.cmd_fan_topic, 'P5')
        self.assertFalse(self.attrs()['power_request_pending'])
        state['phase'] = 1
        await self.b.poll_once()
        state['phase'] = 4
        await self.b.poll_once()
        self.assertEqual(state['commands'].count('RF0050'), 1)

    async def test_explicit_heat_can_wait_for_ignition_before_startup_recovery(self):
        state, _, _ = await self.stove(phase=0, power=2)
        self.b.last_power_level = 'P5'
        await self.b.handle_mqtt_command(self.b.cmd_mode_topic, 'heat')
        state['phase'] = 1
        await self.b.poll_once()
        state['phase'] = 4
        await self.b.poll_once()
        self.assertEqual(state['commands'].count('RF0050'), 2)

    async def test_offline_power_request_is_not_inherited_by_heat_after_connect(self):
        await self.b.handle_mqtt_command(self.b.cmd_fan_topic, 'P5')
        state, _, _ = await self.stove(phase=0)
        await self.b.handle_mqtt_command(self.b.cmd_mode_topic, 'heat')
        self.assertNotIn('RF0050', state['commands'])
        self.assertIn('RF0010', state['commands'])


class MQTTStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_installed_paho_client_announces_stove_when_connected(self):
        controller = bridge.DuepiStoveBridge({'device_code': CODE, 'stove_ip': '192.0.2.10'})
        client = controller.mqtt
        client.publish = Mock()
        client.subscribe = Mock()
        if hasattr(bridge.mqtt, 'CallbackAPIVersion'):
            client.on_connect(client, None, {}, 0, None)
        else:
            client.on_connect(client, None, {}, 0)
        discovery = next(call for call in client.publish.call_args_list
                         if call.args[0] == 'homeassistant/climate/stove/config')
        payload = json.loads(discovery.args[1])
        self.assertEqual(payload['modes'], ['off', 'heat'])
        self.assertEqual(payload['temperature_command_topic'], 'duepi_stove/cmd/target_temperature')
        self.assertTrue(discovery.kwargs['retain'])
        client.subscribe.assert_called_once_with([
            ('duepi_stove/cmd/mode', 1),
            ('duepi_stove/cmd/target_temperature', 1),
            ('duepi_stove/cmd/fan_mode', 1),
        ])
        client.publish.assert_any_call('duepi_stove/availability', 'offline', qos=1, retain=True)


if __name__ == '__main__':
    unittest.main()
