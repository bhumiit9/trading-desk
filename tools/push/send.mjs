// Sends the notifications queued in data/outbox/ to every registered device.
// Runs inside GitHub Actions (see .github/workflows/notify.yml).
//
// Secret PUSH_KEYS = "v1.<vapid private key>.<device-code private key>" (base64url).
// Devices live in push/subs/*.txt as encrypted "device codes" copied from the app.
import { execFileSync } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..', '..');
const cfg = JSON.parse(fs.readFileSync(path.join(root, 'push/config.json'), 'utf8'));
const DRY = process.env.DRY_RUN === '1';
const MAX_AGE_MS = 3 * 3600 * 1000;

function decodeDevice(code, encPriv) {
  const parts = code.trim().split('.');
  if (parts[0] !== 'SFD1' || parts.length !== 3) throw new Error('not a device code');
  const raw = Buffer.from(parts[2], 'base64url');
  const ephPub = raw.subarray(0, 65), iv = raw.subarray(65, 77), body = raw.subarray(77);
  const ecdh = crypto.createECDH('prime256v1');
  ecdh.setPrivateKey(encPriv);
  const key = crypto.createHash('sha256').update(ecdh.computeSecret(ephPub)).digest();
  const d = crypto.createDecipheriv('aes-256-gcm', key, iv);
  d.setAuthTag(body.subarray(body.length - 16));
  const txt = Buffer.concat([d.update(body.subarray(0, body.length - 16)), d.final()]).toString('utf8');
  return JSON.parse(txt);
}

function newOutboxFiles() {
  const before = process.env.BEFORE || '', after = process.env.AFTER || 'HEAD';
  let out = '';
  try {
    if (/^[0-9a-f]{40}$/.test(before) && !/^0+$/.test(before)) {
      out = execFileSync('git', ['diff', '--name-only', '--diff-filter=A', before, after, '--', 'data/outbox'], { cwd: root }).toString();
    } else {
      out = execFileSync('git', ['show', '--name-only', '--diff-filter=A', '--format=', after, '--', 'data/outbox'], { cwd: root }).toString();
    }
  } catch (e) {
    console.log('could not list new outbox files:', e.message);
  }
  return out.split('\n').map(s => s.trim()).filter(s => s.endsWith('.json'));
}

const secret = (process.env.PUSH_KEYS || '').trim();
if (!secret) { console.log('PUSH_KEYS secret is not set: nothing sent.'); process.exit(0); }
const [ver, vapidPriv, encPrivB64] = secret.split('.');
if (ver !== 'v1' || !vapidPriv || !encPrivB64) { console.error('PUSH_KEYS has the wrong format.'); process.exit(1); }
const encPriv = Buffer.from(encPrivB64, 'base64url');

// messages
let messages = [];
if (process.env.TEST_BODY) {
  messages.push({ title: process.env.TEST_TITLE || 'Set & Forget Desk', body: process.env.TEST_BODY, tag: 'test-' + Date.now(), url: './' });
} else {
  for (const f of newOutboxFiles()) {
    try {
      const m = JSON.parse(fs.readFileSync(path.join(root, f), 'utf8'));
      if (m.at && Date.now() - Date.parse(m.at) > MAX_AGE_MS) { console.log('skip (too old):', f); continue; }
      messages.push({ title: String(m.title || 'Set & Forget Desk').slice(0, 80), body: String(m.body || '').slice(0, 900), tag: m.tag, url: m.url || './' });
    } catch (e) { console.log('skip (unreadable):', f); }
  }
}
if (!messages.length) { console.log('No new notifications to send.'); process.exit(0); }

// devices
const subsDir = path.join(root, 'push/subs');
const devices = [];
for (const f of fs.readdirSync(subsDir).filter(f => f.endsWith('.txt'))) {
  try { devices.push({ name: f.replace(/\.txt$/, ''), sub: decodeDevice(fs.readFileSync(path.join(subsDir, f), 'utf8'), encPriv) }); }
  catch (e) { console.log(`device ${f}: could not be read (${e.message})`); }
}
console.log(`${messages.length} notification(s), ${devices.length} device(s)`);
if (!devices.length) { console.log('No devices registered yet.'); process.exit(0); }
if (DRY) { for (const d of devices) console.log('device', d.name, 'ok:', new URL(d.sub.endpoint).host); process.exit(0); }

const webpush = (await import('web-push')).default;
webpush.setVapidDetails('https://bhumiit9.github.io/trading-desk/', cfg.vapidPublicKey, vapidPriv);
let failed = 0;
for (const m of messages) {
  for (const d of devices) {
    try {
      const r = await webpush.sendNotification(d.sub, JSON.stringify(m), { TTL: 3600, urgency: 'high' });
      console.log(`sent "${m.title}" -> ${d.name} (${r.statusCode})`);
    } catch (e) {
      const code = e.statusCode || 0;
      if (code === 404 || code === 410) console.log(`device ${d.name}: registration expired (${code}). Turn notifications on again in the app and send the new device code.`);
      else { failed++; console.log(`device ${d.name}: failed (${code || e.message}) ${String(e.body || '').slice(0, 200)}`); }
    }
  }
}
process.exit(failed ? 1 : 0);
