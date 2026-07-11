// k6 load-smoke for the Bass API container.
//
// Drives liveness plus an authenticated fire under short concurrent load and
// gates on latency + error-rate thresholds. Self-contained: it mints its own
// HS256 bearer (matching bass.auth) with k6's crypto module, so no package
// install is needed on the runner.
//
//   docker run --rm --network host \
//     -e BASE_URL=http://localhost:8000 -e JWT_SECRET=ci-secret \
//     -v "$PWD/loadtest:/scripts" grafana/k6 run /scripts/smoke.js
//
// A non-zero exit (threshold breach) fails the CI job.

import http from 'k6/http';
import crypto from 'k6/crypto';
import encoding from 'k6/encoding';
import { check } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const SECRET = __ENV.JWT_SECRET || 'ci-secret';

export const options = {
  scenarios: {
    fire: { executor: 'constant-vus', vus: 10, duration: '15s' },
  },
  thresholds: {
    // Fail the job if error rate or tail latency regress.
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<1500'],
    'checks': ['rate>0.99'],
  },
};

function b64url(str) {
  return encoding.b64encode(str, 'rawurl');
}

// Mint an HS256 JWT that bass.auth.decode_token accepts (it re-signs the
// received header.payload, so key ordering is irrelevant).
function token() {
  const now = Math.floor(Date.now() / 1000);
  const header = b64url(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const payload = b64url(JSON.stringify({
    sub: 'k6', tenant_id: 'loadtest', roles: ['builder'],
    iat: now, exp: now + 3600,
  }));
  const signingInput = `${header}.${payload}`;
  const sig = crypto.hmac('sha256', SECRET, signingInput, 'base64rawurl');
  return `${signingInput}.${sig}`;
}

const BEARER = token();

export default function () {
  // Liveness — cheap, always available.
  const health = http.get(`${BASE_URL}/healthz`);
  check(health, { 'healthz 200': (r) => r.status === 200 });

  // An authenticated fire of a small ($100) invoice: policy auto-allows it, so
  // the run completes end-to-end (no approval pause) — a real durable execution
  // per request, not just a ping.
  const uniq = `${__VU}-${__ITER}`;
  const body = JSON.stringify({
    trigger: {
      source: 'email', vendor: 'Globex Corp',
      invoice_number: `INV-${uniq}`, amount: 100.0,
      message_id: `<load-${uniq}@loadtest>`,
    },
  });
  const res = http.post(`${BASE_URL}/v1/workflows/invoice-triage/runs`, body, {
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${BEARER}` },
  });
  check(res, {
    'fire 200': (r) => r.status === 200,
    'fire succeeded': (r) => {
      try { return JSON.parse(r.body).status === 'succeeded'; } catch (e) { return false; }
    },
  });
}
