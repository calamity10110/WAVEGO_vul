"""Dashboard — read-only control surface with a single write: STOP.

Purpose:
    FastAPI app exposing robot state to an operator. The dashboard is a
    window, not a door: every endpoint is read-only except /api/estop,
    which drives validator.halt() directly (bypasses the agent loop).
    Token auth with constant-time comparison; naive per-IP rate limit;
    the estop endpoint is deliberately exempt from rate limiting.

Dependencies:
    fastapi, uvicorn, PyYAML, pyserial (via validator).

Interface:
    create_app(validator, token, broadcast_interval) -> FastAPI
    run(validator, token, host, port) — blocking uvicorn serve

Expected outcome:
    GET  /            dashboard HTML
    GET  /health      liveness ping (no auth, no state)
    GET  /api/status  state snapshot (auth)
    GET  /api/audit   validator audit tail (auth)
    POST /api/estop   immediate halt (auth, never rate-limited)
    WS   /ws          live status push every broadcast_interval s (auth)
"""

import asyncio
import hmac
import os
import sqlite3
import time

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WAVEGO Agent</title>
<style>
  :root { --bg:#0d1117; --panel:#161b22; --edge:#30363d; --fg:#e6edf3;
          --dim:#8b949e; --ok:#3fb950; --warn:#d29922; --bad:#f85149; }
  * { box-sizing:border-box; margin:0; }
  body { background:var(--bg); color:var(--fg);
         font:14px/1.5 "SF Mono",Consolas,monospace; padding:16px; }
  h1 { font-size:15px; letter-spacing:2px; text-transform:uppercase;
       color:var(--dim); margin-bottom:12px; }
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; max-width:900px; }
  .panel { background:var(--panel); border:1px solid var(--edge);
           border-radius:6px; padding:14px; }
  .panel h2 { font-size:11px; text-transform:uppercase; letter-spacing:1px;
              color:var(--dim); margin-bottom:10px; }
  .row { display:flex; justify-content:space-between; padding:3px 0; }
  .row .k { color:var(--dim); }
  .code { font-size:26px; font-weight:700; }
  .estop-panel { grid-column:1 / -1; text-align:center; }
  button#estop { background:var(--bad); color:#fff; border:none;
                 border-radius:6px; padding:18px 64px; font-size:20px;
                 font-weight:700; letter-spacing:3px; cursor:pointer; }
  button#estop:hover { filter:brightness(1.15); }
  table { width:100%; border-collapse:collapse; font-size:12px; }
  td, th { padding:3px 6px; border-bottom:1px solid var(--edge); text-align:left; }
  th { color:var(--dim); font-weight:400; }
  .ok { color:var(--ok); } .warn { color:var(--warn); } .bad { color:var(--bad); }
  #conn { font-size:11px; color:var(--dim); margin-top:10px; }
  input { background:var(--bg); border:1px solid var(--edge); color:var(--fg);
          border-radius:4px; padding:6px 10px; width:260px; font:inherit; }
</style>
</head>
<body>
<h1>WAVEGO Agent Console</h1>
<div class="grid">
  <div class="panel estop-panel">
    <h2>emergency stop — the only write</h2>
    <input id="tok" type="password" placeholder="auth token">
    <div style="height:12px"></div>
    <button id="estop" onclick="doEstop()">ESTOP</button>
    <div id="estopMsg" class="warn"></div>
  </div>
  <div class="panel">
    <h2>state</h2>
    <div class="row"><span class="k">current code</span><span class="code" id="code">------</span></div>
    <div class="row"><span class="k">action</span><span id="name">—</span></div>
    <div class="row"><span class="k">battery</span><span id="batt">—</span></div>
    <div class="row"><span class="k">bad-code streak</span><span id="bad">0</span></div>
    <div class="row"><span class="k">estops issued</span><span id="stops">0</span></div>
    <div class="row"><span class="k">mode</span><span id="mode">—</span></div>
    <div class="row"><span class="k">uptime</span><span id="up">—</span></div>
  </div>
  <div class="panel">
    <h2>audit tail</h2>
    <table><thead><tr><th>time</th><th>code</th><th>src</th><th>outcome</th></tr></thead>
    <tbody id="audit"></tbody></table>
  </div>
</div>
<div id="conn">connecting…</div>
<script>
let token = localStorage.getItem("wavego_token") || "";
document.getElementById("tok").value = token;
document.getElementById("tok").oninput = e => {
  token = e.target.value; localStorage.setItem("wavego_token", token);
};

async function poll() {
  try {
    const r = await fetch("/api/status", {headers:{"X-Auth-Token": token}});
    if (r.status === 401) { document.getElementById("conn").textContent = "unauthorized — enter token"; return; }
    const s = await r.json();
    document.getElementById("code").textContent = s.code;
    document.getElementById("name").textContent = s.name;
    document.getElementById("batt").textContent = s.battery_pct == null ? "n/a" : s.battery_pct.toFixed(1) + "%";
    const badEl = document.getElementById("bad");
    badEl.textContent = s.consecutive_bad;
    badEl.className = s.consecutive_bad > 0 ? "bad" : "ok";
    document.getElementById("stops").textContent = s.estop_count;
    document.getElementById("mode").textContent = s.simulate ? "SIMULATE" : "HARDWARE";
    document.getElementById("up").textContent = Math.floor(s.uptime_s) + "s";
    document.getElementById("conn").textContent = "live · " + new Date().toLocaleTimeString();

    const a = await (await fetch("/api/audit?limit=12", {headers:{"X-Auth-Token": token}})).json();
    document.getElementById("audit").innerHTML = a.entries.map(e =>
      `<tr><td>${e.t}</td><td>${e.code}</td><td>${e.source}</td>` +
      `<td class="${e.outcome === "APPROVED" ? "ok" : "bad"}">${e.outcome}</td></tr>`).join("");
  } catch (err) {
    document.getElementById("conn").textContent = "offline — " + err;
  }
}

async function doEstop() {
  const msg = document.getElementById("estopMsg");
  try {
    const r = await fetch("/api/estop", {method:"POST", headers:{"X-Auth-Token": token}});
    const j = await r.json();
    msg.textContent = r.ok ? "STOPPED · " + j.detail : "REJECTED · " + (j.detail || r.status);
    msg.className = r.ok ? "ok" : "bad";
  } catch (err) { msg.textContent = "failed: " + err; msg.className = "bad"; }
}

setInterval(poll, 2000);
poll();
</script>
</body>
</html>"""


def create_app(validator, auth_token, broadcast_interval=1.0):
    app = FastAPI(title="WAVEGO Agent Dashboard", docs_url=None, redoc_url=None)
    started = time.time()
    estop_count = {"n": 0}
    request_times_by_ip = {}
    RATE_LIMIT = 30
    RATE_WINDOW = 10.0

    def check_token(request: Request):
        supplied = request.headers.get("X-Auth-Token", "")
        if not hmac.compare_digest(supplied, auth_token):
            raise HTTPException(status_code=401, detail="bad token")

    def rate_ok(request: Request) -> bool:
        ip = request.client.host if request.client else "?"
        now = time.time()
        window = [t for t in request_times_by_ip.get(ip, []) if now - t < RATE_WINDOW]
        if len(window) >= RATE_LIMIT:
            request_times_by_ip[ip] = window
            return False
        window.append(now)
        request_times_by_ip[ip] = window
        return True

    def snapshot():
        battery = None
        try:
            battery = validator.read_battery()
        except Exception:
            pass
        return {
            "code": validator.prev_code,
            "name": validator.codebook.get(validator.prev_code, {}).get("name", "?"),
            "consecutive_bad": validator.consecutive_bad,
            "battery_pct": battery,
            "simulate": validator.simulate,
            "estop_count": estop_count["n"],
            "uptime_s": round(time.time() - started, 1),
        }

    @app.get("/", response_class=HTMLResponse)
    def index():
        return DASHBOARD_HTML

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/api/status")
    def status(request: Request):
        check_token(request)
        if not rate_ok(request):
            return JSONResponse({"detail": "rate limited"}, status_code=429)
        return snapshot()

    @app.get("/api/audit")
    def audit(request: Request, limit: int = 20):
        check_token(request)
        if not rate_ok(request):
            return JSONResponse({"detail": "rate limited"}, status_code=429)
        limit = max(1, min(int(limit), 200))
        db_path = os.path.join(os.path.dirname(validator.config_path), "state.db")
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT ts, code, source, outcome FROM validator_log "
                "ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        finally:
            conn.close()
        entries = [{"t": time.strftime("%H:%M:%S", time.localtime(ts)),
                    "code": code, "source": source, "outcome": outcome}
                   for ts, code, source, outcome in rows]
        return {"entries": entries}

    @app.post("/api/estop")
    def estop(request: Request):
        check_token(request)
        result = validator.halt(source="dashboard_estop")
        estop_count["n"] += 1
        return {"ok": True, "detail": f"halted: {result}"}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket, token: str = ""):
        if not hmac.compare_digest(token, auth_token):
            await websocket.close(code=4401)
            return
        await websocket.accept()
        try:
            while True:
                await websocket.send_json(snapshot())
                await asyncio.sleep(broadcast_interval)
        except WebSocketDisconnect:
            pass

    return app


def run(validator, token, host="0.0.0.0", port=8100, broadcast_interval=1.0):
    import uvicorn
    app = create_app(validator, token, broadcast_interval)
    uvicorn.run(app, host=host, port=port, log_level="warning")


def main():
    import argparse
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from agent.validator import Validator

    parser = argparse.ArgumentParser(description="Agent dashboard")
    parser.add_argument("--config", default="state_table.yaml")
    parser.add_argument("--token", required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--simulate", action="store_true")
    args = parser.parse_args()

    v = Validator(args.config, simulate=args.simulate)
    try:
        run(v, args.token, host=args.host, port=args.port)
    finally:
        v.close()


if __name__ == "__main__":
    main()
