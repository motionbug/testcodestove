const { test: nodeTest } = require('node:test');
const test = (name, fn) => nodeTest(name, { timeout: 500 }, fn);
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const codec = require('../dpremote/stove');
const CODE = '0123456789';
const key = codec.deriveKey(CODE);
const tick = () => new Promise(resolve => setImmediate(resolve));

// Only network I/O and clocks are replaced; execute the real server and HTTP handlers.
function setup(ip = '192.0.2.10') {
  let tcp, http;
  const timers = new Set();
  const sandbox = {
    require: name => name === 'net' ? { ...require('net'), createServer: cb => { tcp = cb; return { listen() {} }; } }
      : name === 'http' ? { createServer: cb => { http = cb; return { listen() {} }; } } : require(name),
    module: { exports: {} }, __dirname: path.resolve(__dirname, '../dpremote'),
    process: { env: { DUEPI_CODE: CODE, DUEPI_STOVE_IP: ip } }, Buffer, URL,
    console: { log() {}, error() {}, warn() {} },
    setTimeout: fn => { timers.add(fn); return fn; }, clearTimeout: fn => timers.delete(fn),
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../dpremote/stove.js'), 'utf8') + '\nstartServer();', sandbox);
  function connect(address = '192.0.2.10') {
    const socket = new EventEmitter();
    Object.assign(socket, { remoteAddress: address, remotePort: 1234, destroyed: false, writes: [] });
    socket.write = wire => socket.writes.push(codec.decryptFrame(wire, key).body);
    socket.destroy = () => { if (!socket.destroyed) { socket.destroyed = true; socket.emit('close'); } };
    tcp(socket);
    socket.emit('data', Buffer.from('hello#'));
    return socket;
  }
  function request(url, method = 'POST') {
    let end;
    const done = new Promise(resolve => { end = resolve; });
    const res = { statusCode: 200, setHeader() {}, end(body) { end({ status: this.statusCode, body: JSON.parse(body) }); } };
    http({ method, url }, res);
    return done;
  }
  return { connect, request, expire: () => { for (const fn of [...timers]) fn(); } };
}
function reply(body = '000000', checksum) {
  const payload = body + (checksum ?? codec.checksumHex(body));
  const iv = Buffer.alloc(16);
  const cipher = crypto.createCipheriv('aes-128-cbc', key, iv);
  cipher.setAutoPadding(false);
  return Buffer.from('\x1b' + Buffer.concat([iv, cipher.update(Buffer.from(payload.padEnd(16, '\0'), 'latin1')), cipher.final()]).toString('base64') + '&', 'latin1');
}

test('retains a reply split at every byte boundary', async () => {
  for (let split = 1; split < 46; split++) {
    const app = setup(), socket = app.connect();
    const result = app.request('/api/power?level=1');
    await tick();
    const frame = reply();
    socket.emit('data', frame.subarray(0, split));
    socket.emit('data', frame.subarray(split));
    app.expire();
    assert.equal((await result).status, 200, `split ${split}`);
  }
});
test('handles plaintext and malformed frame before a valid frame in one chunk', async () => {
  const app = setup(), socket = app.connect(), result = app.request('/api/power?level=1');
  await tick();
  socket.emit('data', Buffer.concat([Buffer.from('hello#\x1bbad&'), reply()]));
  app.expire(); assert.equal((await result).status, 200);
});
test('serializes overlapping requests and delivers each reply once', async () => {
  const app = setup(), socket = app.connect();
  const a = app.request('/api/power?level=1'), b = app.request('/api/power?level=2');
  await tick(); assert.deepEqual(socket.writes, ['RF0010']);
  socket.emit('data', reply()); await tick();
  assert.deepEqual(socket.writes, ['RF0010', 'RF0020']);
  socket.emit('data', reply());
  assert.equal((await a).body.cmd, 'RF0010'); assert.equal((await b).body.cmd, 'RF0020');
});
test('does not use an extra coalesced frame for the next queued request', async () => {
  const app = setup(), socket = app.connect();
  const a = app.request('/api/power?level=1'), b = app.request('/api/power?level=2');
  await tick(); socket.emit('data', Buffer.concat([reply(), reply('001800')]));
  await a; await tick(); socket.emit('data', reply());
  assert.equal((await b).body.reply, '00000020');
});
test('timeout closes the session and rejects queued commands', async () => {
  const app = setup(), socket = app.connect();
  const a = app.request('/api/power?level=1'), b = app.request('/api/power?level=2');
  await tick(); app.expire(); await tick();
  assert.equal(socket.destroyed, true); assert.equal((await a).status, 500); assert.equal((await b).status, 500);
  assert.deepEqual(socket.writes, ['RF0010']);
});
test('disconnect promptly rejects the pending request', async () => {
  const app = setup(), socket = app.connect(), result = app.request('/api/power?level=1');
  await tick(); socket.destroy();
  assert.equal((await result).status, 500);
});
test('corrupt and non-hex replies cannot resolve a request', async () => {
  const app = setup(), socket = app.connect(), result = app.request('/api/power?level=1');
  await tick(); let done = false; result.then(() => { done = true; });
  socket.emit('data', Buffer.concat([reply('000000', '00'), reply('ZZZZZZ')]));
  await tick(); assert.equal(done, false);
  socket.emit('data', reply()); assert.equal((await result).status, 200);
});
test('oversized incomplete frames disconnect the client', () => {
  const app = setup(), socket = app.connect();
  socket.emit('data', Buffer.from('\x1b' + 'A'.repeat(5000)));
  assert.equal(socket.destroyed, true);
});
test('unmatched peers and duplicate sessions cannot replace the stove', async () => {
  const app = setup(), stove = app.connect('::ffff:192.0.2.10');
  const intruder = app.connect('192.0.2.11'), duplicate = app.connect();
  assert.equal(intruder.destroyed, true); assert.equal(duplicate.destroyed, true);
  const result = app.request('/api/power?level=1'); await tick();
  stove.emit('data', reply()); assert.equal((await result).status, 200);
});
test('rejects split app login without logging credentials', () => {
  const app = setup(), socket = app.connect();
  socket.emit('data', Buffer.from('mas')); socket.emit('data', Buffer.from('ter:0123456789#'));
  assert.equal(socket.destroyed, true);
});
test('accepts a new session after disconnect with no old buffered bytes', async () => {
  const app = setup(), old = app.connect(); old.emit('data', reply().subarray(0, 20)); old.destroy();
  const current = app.connect(), result = app.request('/api/power?level=1'); await tick();
  old.emit('data', reply()); current.emit('data', reply());
  assert.equal((await result).status, 200);
});
test('requires a configured valid stove IP', () => {
  assert.throws(() => setup('')); assert.throws(() => setup('not-an-ip'));
});
test('HTTP rejects invalid parameters without writing commands', async () => {
  const app = setup(), socket = app.connect();
  for (const url of ['/api/power', '/api/power?level=', '/api/power?level=1.5', '/api/power?level=6', '/api/power?level=NaN', '/api/temp', '/api/temp?c=', '/api/temp?c=bad', '/api/temp?c=NaN', '/api/temp?c=Infinity']) {
    const result = app.request(url); await tick(); app.expire();
    assert.equal((await result).status, 400, url);
  }
  assert.deepEqual(socket.writes, []);
});
test('valid temperatures round and clamp and power zero remains explicit', async () => {
  const app = setup(), socket = app.connect();
  for (const [url, command] of [['/api/temp?c=15','RF2100'], ['/api/temp?c=23.6','RF2180'], ['/api/temp?c=40','RF2230'], ['/api/power?level=0','RF0000']]) {
    const result = app.request(url); await tick(); assert.equal(socket.writes.at(-1), command);
    socket.emit('data', reply()); assert.equal((await result).status, 200);
  }
  assert.throws(() => codec.COMMANDS.setTemp('bad'));
});

test('codec matches the fixed cross-language vector and rejects malformed frames', () => {
  assert.equal(key.toString('hex'), '5be4823cf94bd2e58553ec201fa25ec6');
  const vector = '\x1bAAAAAAAAAAAAAAAAAAAAALLifCC1HkBdciXX+5IoTxY=&';
  assert.equal(codec.encryptFrame('RD9000', key, Buffer.alloc(16)), vector);
  assert.equal(codec.decryptFrame(vector, key).body, 'RD9000');
  for (const frame of ['\x1b' + '!'.repeat(44) + '&', '\x1bAAAA&']) {
    assert.throws(() => codec.decryptFrame(frame, key));
  }
  assert.equal(codec.decryptFrame(reply('0000'), key).valid, false);
});
test('queued commands from a disconnected session never reach a new session', async () => {
  const app = setup(), old = app.connect();
  const a = app.request('/api/power?level=1'), b = app.request('/api/power?level=2');
  await tick(); old.destroy(); const current = app.connect();
  assert.equal((await a).status, 500); assert.equal((await b).status, 500);
  assert.deepEqual(current.writes, []);
  const c = app.request('/api/power?level=3'); await tick();
  old.emit('data', reply('001800')); current.emit('data', reply());
  assert.equal((await c).body.reply, '00000020');
});
test('accepts captured ESP reply layout with RAM bytes after the NUL terminator', () => {
  // SPEC.md: ESP replies contain payload8, NUL, then seven uninitialized RAM bytes.
  const wire = codec.encryptFrame('00000020\0\x26\0\x88\0\0\x54\x20', key, Buffer.alloc(16));
  const decoded = codec.decryptFrame(wire, key);
  assert.equal(decoded.valid, true); assert.equal(decoded.body, '000000');
});
