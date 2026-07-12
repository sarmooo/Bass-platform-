"""Operator console — a self-contained single-page UI served by the API.

Vanilla HTML/CSS/JS (no build step, no external assets). It talks to the same
JSON endpoints an integration would use (`/v1/runs`, `/v1/runs/{id}/trace`, the
approve/cancel routes), authenticating with a bearer token the operator pastes
in. Served at `GET /` (see bass/api.py). This is the docs' Admin/Builder console
in minimal form: watch runs, read the trace, and act on approvals.
"""

from __future__ import annotations

CONSOLE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bass · Operator Console</title>
<style>
  :root {
    --bg:#0f1115; --panel:#171a21; --panel2:#1e222b; --line:#2a2f3a;
    --ink:#e7ecf3; --muted:#93a0b4; --accent:#38bdf8;
    --ok:#4ade80; --warn:#f59e0b; --bad:#f87171; --wait:#a78bfa;
  }
  @media (prefers-color-scheme: light) {
    :root { --bg:#f5f7fa; --panel:#fff; --panel2:#eef1f6; --line:#dde3ec;
            --ink:#16202e; --muted:#5a6b80; --accent:#0e7490; }
  }
  * { box-sizing:border-box; }
  body { margin:0; font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
         background:var(--bg); color:var(--ink); }
  .mono { font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace; }
  header { display:flex; gap:12px; align-items:center; padding:12px 18px;
           border-bottom:1px solid var(--line); background:var(--panel); flex-wrap:wrap; }
  header h1 { font-size:15px; margin:0; letter-spacing:.02em; }
  header h1 b { color:var(--accent); }
  header .sp { flex:1; }
  input, select, button { font:inherit; color:var(--ink); background:var(--panel2);
    border:1px solid var(--line); border-radius:8px; padding:7px 10px; }
  button { cursor:pointer; }
  button.primary { background:var(--accent); color:#04121a; border-color:var(--accent); font-weight:650; }
  #token { width:260px; }
  main { display:grid; grid-template-columns:minmax(280px,380px) 1fr; gap:0; height:calc(100vh - 58px); }
  @media (max-width:720px){ main{ grid-template-columns:1fr; height:auto; } }
  .col { overflow:auto; padding:14px; }
  .col.left { border-right:1px solid var(--line); }
  .bar { display:flex; gap:8px; align-items:center; margin-bottom:10px; }
  .bar .sp { flex:1; }
  .run { border:1px solid var(--line); border-radius:10px; padding:10px 12px; margin-bottom:8px;
         background:var(--panel); cursor:pointer; }
  .run:hover { border-color:var(--accent); }
  .run.sel { border-color:var(--accent); box-shadow:0 0 0 1px var(--accent) inset; }
  .run .id { font-size:12px; color:var(--muted); }
  .run .row { display:flex; align-items:center; gap:8px; margin-top:4px; }
  .pill { font-size:11px; padding:2px 8px; border-radius:999px; border:1px solid; letter-spacing:.03em; }
  .p-succeeded{ color:var(--ok); border-color:var(--ok); }
  .p-failed{ color:var(--bad); border-color:var(--bad); }
  .p-waiting_approval{ color:var(--wait); border-color:var(--wait); }
  .p-running,.p-pending{ color:var(--accent); border-color:var(--accent); }
  .p-canceled{ color:var(--muted); border-color:var(--muted); }
  .cost { font-size:12px; color:var(--muted); margin-left:auto; }
  .ev { display:grid; grid-template-columns:44px 190px 1fr; gap:10px; padding:7px 0;
        border-top:1px solid var(--line); font-size:13px; align-items:baseline; }
  .ev .seq{ color:var(--muted); } .ev .ty{ color:var(--accent); }
  .ev .pl{ color:var(--muted); overflow:hidden; text-overflow:ellipsis; }
  .actions { display:flex; gap:8px; margin:6px 0 14px; }
  .empty { color:var(--muted); padding:20px; text-align:center; }
  .toast { position:fixed; bottom:16px; left:50%; transform:translateX(-50%);
           background:var(--panel); border:1px solid var(--line); border-radius:8px;
           padding:8px 14px; font-size:13px; opacity:0; transition:opacity .2s; }
  .toast.show { opacity:1; }
  h2 { font-size:13px; text-transform:uppercase; letter-spacing:.12em; color:var(--muted); margin:0 0 10px; }
</style>
</head>
<body>
<header>
  <h1><b>Bass</b> · Operator Console</h1>
  <div class="sp"></div>
  <input id="token" class="mono" placeholder="paste bearer token (JWT)" />
  <button class="primary" onclick="connect()">Connect</button>
</header>
<main>
  <section class="col left">
    <div class="bar">
      <h2 style="margin:0">Runs</h2><div class="sp"></div>
      <select id="filter" onchange="loadRuns()">
        <option value="">all</option>
        <option>waiting_approval</option><option>succeeded</option>
        <option>failed</option><option>canceled</option>
      </select>
      <button onclick="loadRuns()">Refresh</button>
    </div>
    <div id="runs"><div class="empty">Connect with a token to load runs.</div></div>
  </section>
  <section class="col right">
    <h2>Trace</h2>
    <div id="detail"><div class="empty">Select a run.</div></div>
  </section>
</main>
<div id="toast" class="toast"></div>
<script>
let TOKEN = localStorage.getItem("bass_token") || "";
let SEL = null;
document.getElementById("token").value = TOKEN;

function toast(m){ const t=document.getElementById("toast"); t.textContent=m;
  t.classList.add("show"); setTimeout(()=>t.classList.remove("show"),1800); }
function connect(){ TOKEN=document.getElementById("token").value.trim();
  localStorage.setItem("bass_token",TOKEN); loadRuns(); }
async function api(path, opts){
  const r = await fetch(path, Object.assign({headers:{Authorization:"Bearer "+TOKEN}}, opts||{}));
  if(!r.ok) throw new Error(r.status+" "+(await r.text()));
  return r.status===204?null:r.json();
}
function pill(s){ return '<span class="pill p-'+s+'">'+s+'</span>'; }

async function loadRuns(){
  const f=document.getElementById("filter").value;
  try{
    const data=await api("/v1/runs"+(f?("?status="+f):""));
    const el=document.getElementById("runs");
    if(!data.runs.length){ el.innerHTML='<div class="empty">No runs.</div>'; return; }
    el.innerHTML=data.runs.map(r=>`
      <div class="run ${SEL===r.run_id?'sel':''}" onclick="openRun('${r.run_id}')">
        <div class="id mono">${r.run_id}</div>
        <div class="row">${pill(r.status)}<span class="cost mono">$${(r.cost_usd||0).toFixed(4)}</span></div>
      </div>`).join("");
  }catch(e){ toast("Load failed: "+e.message); }
}

async function openRun(id){
  SEL=id; loadRuns();
  try{
    const [run,tr]=await Promise.all([api("/v1/runs/"+id), api("/v1/runs/"+id+"/trace")]);
    const canApprove=run.status==="waiting_approval";
    const canCancel=["pending","running","waiting_approval"].includes(run.status);
    let approvalId=null;
    (tr.events||[]).forEach(e=>{ if(e.type==="approval_requested" && e.payload) approvalId=e.payload.approval_id; });
    const actions=`<div class="actions">
      ${canApprove&&approvalId?`<button class="primary" onclick="resolve('${id}','${approvalId}',true)">Approve</button>
        <button onclick="resolve('${id}','${approvalId}',false)">Reject</button>`:''}
      ${canCancel?`<button onclick="cancelRun('${id}')">Cancel</button>`:''}</div>`;
    const rows=(tr.events||[]).map(e=>`
      <div class="ev"><span class="seq mono">#${e.seq}</span>
        <span class="ty mono">${e.type}</span>
        <span class="pl mono">${e.step_id?('['+e.step_id+'] '):''}${JSON.stringify(e.payload)}</span></div>`).join("");
    document.getElementById("detail").innerHTML=
      `<div class="row" style="margin-bottom:8px">${pill(run.status)}
        <span class="cost mono">$${(run.cost_usd||0).toFixed(4)}</span></div>${actions}${rows}`;
  }catch(e){ toast("Open failed: "+e.message); }
}

async function resolve(id,appr,ok){
  try{ await api("/v1/runs/"+id+"/approvals/"+appr,{method:"POST",
      headers:{Authorization:"Bearer "+TOKEN,"Content-Type":"application/json"},
      body:JSON.stringify({approved:ok})});
    toast(ok?"Approved":"Rejected"); openRun(id);
  }catch(e){ toast("Action failed: "+e.message); }
}
async function cancelRun(id){
  try{ await api("/v1/runs/"+id+"/cancel",{method:"POST"}); toast("Canceled"); openRun(id);
  }catch(e){ toast("Cancel failed: "+e.message); }
}
if(TOKEN) loadRuns();
</script>
</body>
</html>
"""
