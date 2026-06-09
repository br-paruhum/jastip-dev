/**
 * Jastip.me WhatsApp microservice (Baileys).
 *
 * Exposes a tiny HTTP API the Django app calls to send WhatsApp messages.
 * On first run it prints a QR code — scan it with the admin WhatsApp account.
 * Credentials persist in ./auth_info so it stays logged in across restarts.
 *
 *   POST /send   { "to": "+62812...", "message": "..." }   (Bearer token)
 *   GET  /status                                           (Bearer token)
 *   GET  /healthz                                          (no auth)
 */
require('dotenv').config();

// Node 18 doesn't expose the WebCrypto API as a global (Node 20+ does), but
// Baileys relies on globalThis.crypto. Polyfill it before loading Baileys.
const { webcrypto } = require('crypto');
if (!globalThis.crypto) globalThis.crypto = webcrypto;

const express = require('express');
const pino = require('pino');
const qrcode = require('qrcode-terminal');
const { Boom } = require('@hapi/boom');
const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
} = require('@whiskeysockets/baileys');

const PORT = process.env.PORT || 8090;
const TOKEN = process.env.WHATSAPP_BOT_TOKEN || 'change-me';

// Baileys' own logger is extremely chatty (dumps full stack traces). Keep it
// silent and print our own clean, human-readable status lines instead.
const baileysLogger = pino({ level: 'silent' });
const log = (msg) => console.log(`[${new Date().toISOString()}] ${msg}`);

let sock = null;
let connected = false;
let starting = false;

async function startSock() {
  if (starting) return;
  starting = true;
  const { state, saveCreds } = await useMultiFileAuthState('auth_info');
  const { version } = await fetchLatestBaileysVersion();

  sock = makeWASocket({
    version,
    auth: state,
    logger: baileysLogger,
    printQRInTerminal: false,
    browser: ['Jastip.me', 'Chrome', '1.0'],
    syncFullHistory: false,
    markOnlineOnConnect: false,
  });
  starting = false;

  sock.ev.on('creds.update', saveCreds);
  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect, qr } = update;
    if (qr) {
      console.log('\n  Scan this QR with the Jastip admin WhatsApp');
      console.log('  (WhatsApp → Linked devices → Link a device):\n');
      qrcode.generate(qr, { small: true });
      console.log('\n  Waiting for you to scan… (a new QR appears every ~20s)\n');
    }
    if (connection === 'connecting') {
      log('Connecting to WhatsApp…');
    }
    if (connection === 'open') {
      connected = true;
      log('✅ WhatsApp connection OPEN — bot is ready to send messages.');
    }
    if (connection === 'close') {
      connected = false;
      const code = new Boom(lastDisconnect?.error)?.output?.statusCode;
      const loggedOut = code === DisconnectReason.loggedOut;
      if (loggedOut) {
        log('⚠️  Logged out. Delete auth_info/ and restart to re-scan the QR.');
        return;
      }
      // Transient (e.g. 408 timeout during init sync, 515 restart-required):
      // reconnect using the saved credentials after a short backoff.
      log(`Connection closed (code ${code}) — reconnecting in 3s…`);
      setTimeout(() => startSock().catch((e) => log(`reconnect failed: ${e}`)), 3000);
    }
  });
}

// Convert "+6281234" / "081234" to a Baileys JID. Numbers must be E.164-ish.
function toJid(raw) {
  let n = String(raw).replace(/[^\d]/g, '');
  if (!n) return null;
  return `${n}@s.whatsapp.net`;
}

const app = express();
app.use(express.json());

function auth(req, res, next) {
  const header = req.headers.authorization || '';
  const token = header.startsWith('Bearer ') ? header.slice(7) : '';
  if (token !== TOKEN) return res.status(401).json({ ok: false, error: 'unauthorized' });
  next();
}

app.get('/healthz', (req, res) => res.json({ ok: true, connected }));

app.get('/status', auth, (req, res) => res.json({ ok: true, connected }));

app.post('/send', auth, async (req, res) => {
  try {
    const { to, message } = req.body || {};
    if (!to || !message) return res.status(400).json({ ok: false, error: 'to and message required' });
    if (!connected || !sock) return res.status(503).json({ ok: false, error: 'whatsapp not connected' });
    const jid = toJid(to);
    if (!jid) return res.status(400).json({ ok: false, error: 'invalid number' });
    const result = await sock.sendMessage(jid, { text: message });
    res.json({ ok: true, id: result?.key?.id });
  } catch (err) {
    log(`send failed: ${err}`);
    res.status(500).json({ ok: false, error: String(err) });
  }
});

app.listen(PORT, () => log(`WhatsApp bot HTTP API on :${PORT}`));
startSock().catch((e) => log(`failed to start socket: ${e}`));
