#!/usr/bin/env node
/**
 * Local controller, protocol codec, and local relay/HTTP server for Duepi / MyDPremote stove.
 *
 * Recovered crypto specification (from DPremote v3.1.6 ARM64 binary):
 *   - Function 0x100085e98 (key derivation):
 *       key16 = MD5(utf8(deviceCode)) XOR Buffer.from("23FADC18A422678312C86AC29281AC01", "hex")
 *   - Functions 0x1000863fc / 0x100096f70 (encrypt) & 0x100086820 / 0x100098edc (decrypt):
 *       Uses QAESEncryption(AES_128, CBC, Padding::ZERO).
 *       Inner payload is 6-char ASCII command + 2-char uppercase hex checksum (sum of bytes & 0xFF),
 *       zero-padded to 16 bytes.
 *       Wire frame: 0x1B + base64( IV_16B || AES_128_CBC(key16, IV_16B, payload_16B) ) + "&" (46 bytes).
 */

const crypto = require('crypto');
const fs = require('fs');
const net = require('net');
const http = require('http');
const path = require('path');

const KEY_MASK_HEX = '23FADC18A422678312C86AC29281AC01';
const DEFAULT_PCAP = path.join(__dirname, 'relay.pcap');

function deriveKey(code) {
  const cleanCode = code.replace(/#+$/, '');
  const md5 = crypto.createHash('md5').update(cleanCode, 'utf8').digest();
  const mask = Buffer.from(KEY_MASK_HEX, 'hex');
  const key = Buffer.alloc(16);
  for (let i = 0; i < 16; i++) key[i] = md5[i] ^ mask[i];
  return key;
}

function checksumHex(str) {
  let sum = 0;
  for (let i = 0; i < str.length; i++) sum = (sum + str.charCodeAt(i)) & 0xff;
  return sum.toString(16).toUpperCase().padStart(2, '0');
}

function verifyChecksum(str8) {
  if (!str8 || !/^[A-Z0-9]{6}[0-9A-Fa-f]{2}$/.test(str8)) return { valid: false, body: str8, expected: null, actual: null };
  const body = str8.slice(0, -2);
  const actual = str8.slice(-2).toUpperCase();
  const expected = checksumHex(body);
  return { valid: actual === expected, body, expected, actual };
}

function encryptFrame(cmd6, key, fixedIv = null) {
  const fullCmd = cmd6.length === 6 ? cmd6 + checksumHex(cmd6) : cmd6;
  const pt = Buffer.alloc(16, 0);
  pt.write(fullCmd, 0, 'latin1');
  const iv = fixedIv || crypto.randomBytes(16);
  const cipher = crypto.createCipheriv('aes-128-cbc', key, iv);
  cipher.setAutoPadding(false);
  const ct = Buffer.concat([cipher.update(pt), cipher.final()]);
  const wireB64 = Buffer.concat([iv, ct]).toString('base64');
  return `\x1b${wireB64}&`;
}

function decryptFrame(frameStrOrBuf, key) {
  const s = Buffer.isBuffer(frameStrOrBuf) ? frameStrOrBuf.toString('latin1') : frameStrOrBuf;
  const clean = s.replace(/^\x1b/, '').replace(/&$/, '');
  if (!/^[A-Za-z0-9+/]{43}=$/.test(clean)) throw new Error('Invalid frame encoding');
  const raw = Buffer.from(clean, 'base64');
  if (raw.length !== 32) {
    throw new Error(`Invalid ciphertext byte length: ${raw.length}`);
  }
  const iv = raw.slice(0, 16);
  const body = raw.slice(16);
  const decipher = crypto.createDecipheriv('aes-128-cbc', key, iv);
  decipher.setAutoPadding(false);
  const pt = Buffer.concat([decipher.update(body), decipher.final()]);
  const zeroIdx = pt.indexOf(0);
  const str = (zeroIdx >= 0 ? pt.slice(0, zeroIdx) : pt).toString('latin1');
  return { text: str, raw: pt, ...verifyChecksum(str) };
}

function loadCodeFromPcap(pcapPath = DEFAULT_PCAP) {
  if (process.env.DUEPI_CODE) return process.env.DUEPI_CODE.replace(/#+$/, '');
  const buf = fs.readFileSync(pcapPath);
  let off = 24;
  while (off + 16 <= buf.length) {
    const inclLen = buf.readUInt32LE(off + 8);
    const pkt = buf.slice(off + 16, off + 16 + inclLen);
    off += 16 + inclLen;
    if (pkt.length < 34) continue;
    const ip = pkt.slice(14);
    if ((ip[0] >> 4) !== 4 || ip[9] !== 6) continue;
    const ihl = (ip[0] & 0x0f) * 4;
    const tcp = ip.slice(ihl);
    const thl = (tcp[12] >> 4) * 4;
    const pl = tcp.slice(thl).toString('latin1');
    if (pl.startsWith('master:')) {
      return pl.split(':')[1].replace(/#+$/, '');
    }
  }
  throw new Error('Could not find master:<code ># handshake in pcap and DUEPI_CODE is not set.');
}

const COMMANDS = {
  status: 'RD9000',
  ambientTemp: 'RD1000',
  flueGasTemp: 'RD0000',
  waterTemp: 'RD7000',
  boardTemp: 'RDF000',
  pelletSpeed: 'RD4000',
  fanSpeed: 'RD3000',
  exhaustSpeed: 'RD2000',
  errorCode: 'RDA000',
  cycleTime: 'RD8000',
  rpm: 'REF000',
  getPower: 'RC5000',
  getSetpoint: 'RC6000',
  firmware: 'RDC000',
  boardType: 'RC4000',
  resetError: 'RD6000',
  powerOff: 'RF0000',
  power1: 'RF0010',
  power2: 'RF0020',
  power3: 'RF0030',
  power4: 'RF0040',
  power5: 'RF0050',
  setTemp: (degC) => {
    const value = Number(degC);
    if (degC == null || String(degC).trim() === '' || !Number.isFinite(value)) {
      throw new TypeError('Temperature must be a finite number');
    }
    const t = Math.max(16, Math.min(35, Math.round(value)));
    return `RF2${t.toString(16).toUpperCase().padStart(2, '0')}0`;
  },
};

const CYCLE_PHASES = {
  0x00: 'Off',
  0x01: 'Starting (Flame wait)',
  0x02: 'Starting (Flame present)',
  0x04: 'Running (Work)',
  0x08: 'Standby / Cooling / Off',
  0x10: 'Cleaning Brazier',
  0x20: 'Final Cleaning',
  0x40: 'Eco Stop / Standby',
  0x80: 'Alarm',
};

function parseReply(cmd6, body6) {
  if (!body6 || body6.length < 4) return { raw: body6 };
  switch (cmd6) {
    case 'RD9000': {
      const phase = parseInt(body6.slice(0, 2), 16);
      const sub = parseInt(body6.slice(2, 4), 16);
      return { phase, sub, phaseName: CYCLE_PHASES[phase] || `Phase 0x${body6.slice(0, 2)}` };
    }
    case 'RD1000':
    case 'RD7000':
    case 'RDF000':
      return { tempC: parseInt(body6.slice(0, 4), 16) / 10.0 };
    case 'RD0000':
      return { tempC: parseInt(body6.slice(0, 4), 16) };
    case 'RC6000':
      return { setpointC: parseInt(body6.slice(0, 4), 16) };
    case 'RC5000':
      return { powerLevel: parseInt(body6.slice(0, 4), 16) };
    case 'RD4000':
    case 'RD3000':
    case 'RD2000':
    case 'RDA000':
      return { value: parseInt(body6.slice(0, 4), 16) };
    case 'REF000':
      return { rpm: parseInt(body6.slice(0, 4), 16) * 10 };
    default:
      return { raw: body6 };
  }
}

function cmdDecodePcap(pcapPath = DEFAULT_PCAP) {
  const code = loadCodeFromPcap(pcapPath);
  const key = deriveKey(code);
  const buf = fs.readFileSync(pcapPath);
  let off = 24;
  let total = 0;
  let validCount = 0;
  let lastCmd = null;
  const transcript = [];

  while (off + 16 <= buf.length) {
    const inclLen = buf.readUInt32LE(off + 8);
    const pkt = buf.slice(off + 16, off + 16 + inclLen);
    off += 16 + inclLen;
    if (pkt.length < 34) continue;
    const ip = pkt.slice(14);
    if ((ip[0] >> 4) !== 4 || ip[9] !== 6) continue;
    const ihl = (ip[0] & 0x0f) * 4;
    const tcp = ip.slice(ihl);
    const dstPort = tcp.readUInt16BE(2);
    const thl = (tcp[12] >> 4) * 4;
    const pl = tcp.slice(thl);
    if (!pl.length) continue;
    const dir = dstPort === 3000 ? 'App -> Stove' : 'Stove -> App';
    if (pl[0] === 0x1b && pl[pl.length - 1] === 0x26) {
      total++;
      const dec = decryptFrame(pl, key);
      if (dec.valid) validCount++;
      if (dir === 'App -> Stove') {
        lastCmd = dec.body;
        transcript.push({ index: total, dir, frame: dec.text, cmd: dec.body, checksumOk: dec.valid });
      } else {
        const parsed = parseReply(lastCmd, dec.body);
        transcript.push({ index: total, dir, frame: dec.text, replyTo: lastCmd, parsed, checksumOk: dec.valid });
      }
    }
  }

  console.log(`Decoded ${validCount}/${total} frames with valid Duepi checksums.`);
  for (const item of transcript.slice(0, 20)) {
    if (item.dir === 'App -> Stove') {
      console.log(`  #${String(item.index).padStart(3)} [${item.dir}] ${item.frame} (cmd=${item.cmd})`);
    } else {
      console.log(`  #${String(item.index).padStart(3)} [${item.dir}] ${item.frame} (reply to ${item.replyTo}: ${JSON.stringify(item.parsed)})`);
    }
  }
  const setCmds = transcript.filter((x) => x.cmd && x.cmd.startsWith('RF'));
  if (setCmds.length) {
    console.log('\nControl commands observed in capture:');
    for (const c of setCmds) {
      console.log(`  #${c.index} [${c.dir}] ${c.frame}`);
    }
  }
}

/**
 * Local TCP Relay & HTTP Controller Server (Route B: Hardware-free local takeover).
 *
 * - Listens on TCP port (default 3000) for:
 *     1. The stove module (192.0.2.10) connecting outbound when DNS `duepiwebserver.com`
 *        points to this machine.
 *     App connections are rejected; this is a direct stove controller.
 * - Exposes a local HTTP server (default 8080) with REST API + Web UI to inspect & control the stove.
 */
function startServer({ tcpPort = 3000, httpPort = 8080, pcapPath = DEFAULT_PCAP,
  stoveIp = process.env.DUEPI_STOVE_IP } = {}) {
  const ipVersion = net.isIP(stoveIp || '');
  if (!ipVersion) throw new Error('Set DUEPI_STOVE_IP to the stove IP address');
  const allowedPeer = new net.BlockList();
  allowedPeer.addAddress(stoveIp, ipVersion === 4 ? 'ipv4' : 'ipv6');
  const code = loadCodeFromPcap(pcapPath);
  const key = deriveKey(code);

  let stoveSocket = null;
  let pendingReply = null;
  let commandQueue = Promise.resolve();

  function closeSession(sock, error) {
    if (stoveSocket === sock) stoveSocket = null;
    if (pendingReply && pendingReply.socket === sock) {
      const pending = pendingReply;
      pendingReply = null;
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    sock.destroy();
  }

  function sendToStove(cmd6, timeoutMs = 4000) {
    // Capture the session at enqueue time: queued work must never cross a reconnect.
    const socket = stoveSocket;
    const result = commandQueue.then(() => new Promise((resolve, reject) => {
      if (!socket || socket !== stoveSocket || socket.destroyed) {
        reject(new Error('Stove is not connected to local relay yet'));
        return;
      }
      const timer = setTimeout(() => {
        closeSession(socket, new Error(`Timeout waiting for reply to ${cmd6}`));
      }, timeoutMs);
      pendingReply = { socket, timer, reject, resolve: dec => resolve({
        cmd: cmd6, reply: dec.text, parsed: parseReply(cmd6, dec.body), valid: true,
      }) };
      try {
        socket.write(encryptFrame(cmd6, key));
      } catch (error) {
        closeSession(socket, error);
      }
    }));
    commandQueue = result.catch(() => {});
    return result;
  }

  const tcpServer = net.createServer((sock) => {
    const peer = `${sock.remoteAddress}:${sock.remotePort}`;
    sock.on('error', err => closeSession(sock, err));
    const version = net.isIP(sock.remoteAddress || '');
    if (!version || !allowedPeer.check(sock.remoteAddress, version === 4 ? 'ipv4' : 'ipv6') || stoveSocket) {
      sock.destroy();
      return;
    }
    stoveSocket = sock;
    console.log(`[TCP] Stove connected from ${peer}`);
    let buffer = '';

    sock.on('data', (chunk) => {
      if (sock !== stoveSocket || sock.destroyed) return;
      buffer += chunk.toString('latin1');
      while (buffer.length) {
        const esc = buffer.indexOf('\x1b');
        const plaintext = esc < 0 ? buffer : buffer.slice(0, esc);
        // Retain partial plaintext so a split master: login is still rejected.
        // Do not log plaintext: even partial handshakes can contain credentials.
        if (plaintext.includes('master:')) {
          closeSession(sock, new Error('App clients are not supported'));
          return;
        }
        if (esc < 0) break;
        buffer = buffer.slice(esc);
        const amp = buffer.indexOf('&');
        if (amp < 0) break;
        const frame = buffer.slice(buffer.lastIndexOf('\x1b', amp), amp + 1);
        buffer = buffer.slice(amp + 1);
        try {
          const dec = decryptFrame(frame, key);
          if (!dec.valid || !/^[0-9A-Fa-f]{6}$/.test(dec.body)) continue;
          if (pendingReply && pendingReply.socket === sock) {
            const pending = pendingReply;
            pendingReply = null;
            clearTimeout(pending.timer);
            pending.resolve(dec);
          }
        } catch (error) {
          console.error(`[TCP] Invalid frame from ${peer}: ${error.message}`);
        }
      }
      if (buffer.length > 4096) closeSession(sock, new Error('TCP buffer limit exceeded'));
    });

    sock.on('close', () => {
      buffer = '';
      closeSession(sock, new Error('Stove disconnected'));
    });
  });

  const httpServer = http.createServer(async (req, res) => {
    const url = new URL(req.url, `http://localhost:${httpPort}`);
    res.setHeader('Content-Type', 'application/json');

    try {
      if (req.method === 'GET' && url.pathname === '/api/state') {
        if (!stoveSocket || stoveSocket.destroyed) {
          res.end(JSON.stringify({ connected: false }));
          return;
        }
        const status = await sendToStove(COMMANDS.status);
        const ambient = await sendToStove(COMMANDS.ambientTemp);
        const setpoint = await sendToStove(COMMANDS.getSetpoint);
        const power = await sendToStove(COMMANDS.getPower);
        res.end(JSON.stringify({
          connected: true,
          status: status.parsed,
          ambientTempC: ambient.parsed.tempC,
          setpointC: setpoint.parsed.setpointC,
          powerLevel: power.parsed.powerLevel,
        }));
      } else if (req.method === 'POST' && url.pathname === '/api/power') {
        const raw = url.searchParams.get('level');
        const level = Number(raw);
        if (raw === null || raw.trim() === '' || !Number.isInteger(level) || level < 0 || level > 5) {
          throw Object.assign(new Error('Invalid power level (0-5)'), { statusCode: 400 });
        }
        const cmd = level === 0 ? COMMANDS.powerOff : COMMANDS[`power${level}`];
        if (!cmd) throw new Error('Invalid power level (0-5)');
        const r = await sendToStove(cmd);
        res.end(JSON.stringify(r));
      } else if (req.method === 'POST' && url.pathname === '/api/temp') {
        const raw = url.searchParams.get('c');
        if (raw === null || raw.trim() === '' || !Number.isFinite(Number(raw))) {
          throw Object.assign(new Error('Temperature must be a finite number'), { statusCode: 400 });
        }
        const r = await sendToStove(COMMANDS.setTemp(raw));
        res.end(JSON.stringify(r));
      } else {
        res.setHeader('Content-Type', 'text/html; charset=utf-8');
        res.end(`<!doctype html><html><head><meta charset="utf-8"><title>Duepi Local Stove Controller</title>
<style>body{font-family:system-ui;max-width:520px;margin:2rem auto;padding:0 1rem;background:#111;color:#eee}
.card{background:#1e1e1e;padding:1.25rem;border-radius:12px;margin-bottom:1rem}
button{padding:.6rem 1rem;margin:.25rem;border-radius:8px;border:1px solid #444;background:#2b2b2b;color:#fff;cursor:pointer}
button:hover{background:#3c3c3c}</style></head><body>
<h2>Duepi Stove Local Controller</h2>
<div class="card" id="state">Loading...</div>
<div class="card">
  <h3>Power Level</h3>
  <button onclick="setPower(0)">OFF</button>
  <button onclick="setPower(1)">P1</button>
  <button onclick="setPower(2)">P2</button>
  <button onclick="setPower(3)">P3</button>
  <button onclick="setPower(4)">P4</button>
  <button onclick="setPower(5)">P5</button>
</div>
<div class="card">
  <h3>Temperature Setpoint (°C)</h3>
  <input id="temp" type="number" min="16" max="35" value="22" style="padding:.5rem;width:4rem">
  <button onclick="setTemp()">Set</button>
</div>
<script>
async function refresh(){
  const r = await fetch('/api/state').then(x=>x.json());
  document.getElementById('state').innerHTML = r.connected
    ? \`<b>Phase:</b> \${r.status.phaseName}<br><b>Ambient:</b> \${r.ambientTempC} °C<br><b>Setpoint:</b> \${r.setpointC} °C<br><b>Power:</b> P\${r.powerLevel}\`
    : '<b>Waiting for configured stove to connect on TCP port ${tcpPort}...</b>';
}
async function setPower(n){ await fetch('/api/power?level='+n,{method:'POST'}); refresh(); }
async function setTemp(){ const c=document.getElementById('temp').value; await fetch('/api/temp?c='+c,{method:'POST'}); refresh(); }
refresh(); setInterval(refresh, 5000);
</script></body></html>`);
      }
    } catch (e) {
      res.statusCode = e.statusCode || 500;
      res.end(JSON.stringify({ error: e.message }));
    }
  });

  tcpServer.listen(tcpPort, '0.0.0.0', () => {
    console.log(`[Relay] Listening for stove/app TCP connections on 0.0.0.0:${tcpPort}`);
  });
  httpServer.listen(httpPort, '127.0.0.1', () => {
    console.log(`[HTTP]  Local controller UI at http://127.0.0.1:${httpPort}`);
  });
}

if (require.main === module) {
  const mode = process.argv[2] || 'decode-pcap';
  if (mode === 'decode-pcap') {
    cmdDecodePcap(process.argv[3] || DEFAULT_PCAP);
  } else if (mode === 'serve') {
    const tcpPort = Number(process.argv[3] || 3000);
    const httpPort = Number(process.argv[4] || 8080);
    startServer({ tcpPort, httpPort });
  } else {
    console.log('Usage:\n  node stove.js decode-pcap [relay.pcap]\n  node stove.js serve [tcpPort=3000] [httpPort=8080]');
  }
}

module.exports = { deriveKey, encryptFrame, decryptFrame, checksumHex, verifyChecksum, COMMANDS, parseReply };
