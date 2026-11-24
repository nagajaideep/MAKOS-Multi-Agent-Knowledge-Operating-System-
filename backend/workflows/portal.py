"""A small portal-style vendor page with NO API - the target of the browser agent.

Real portals (MCA, GeM) use CAPTCHAs and do not allow automation, so the demo uses this
local page instead. It is plain HTML forms only, like an older government system.
"""
import html

from fastapi import APIRouter, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from backend.database import db
from backend.services import audit

router = APIRouter()

STYLE = """<style>
body{font-family:'Noto Sans',system-ui,sans-serif;background:#F5F7FA;margin:0;color:#1d2733}
.top{background:#fff;border-bottom:3px solid #1F4E79;padding:10px 24px}
.top b{color:#1F4E79;font-size:18px}.bar{height:4px;background:linear-gradient(90deg,#FF9933 33%,#fff 33% 66%,#138808 66%)}
main{max-width:960px;margin:20px auto;background:#fff;border:1px solid #DDE3EA;padding:20px}
table{border-collapse:collapse;width:100%}th,td{border:1px solid #DDE3EA;padding:6px 8px;text-align:left;font-size:14px}
th{background:#EAF1F8}input,textarea{font:inherit;padding:6px;border:1px solid #b8c4d0}button{background:#1F4E79;color:#fff;border:0;padding:7px 14px;cursor:pointer}
.s{font-weight:600}.hold{color:#C62828}.note{font-size:12px;color:#5b6b7b}
</style>"""


def _page(body):
    return HTMLResponse(f"<!doctype html><html><head><meta charset='utf-8'><title>Vendor Portal (demo)</title>{STYLE}</head>"
                        f"<body><div class='top'><b>Vendor Enlistment Portal</b> &nbsp;·&nbsp; Deccan Power Ltd (fictional demo)</div>"
                        f"<div class='bar'></div><main>{body}<p class='note'>Demo portal with no API, used by the MAKOS browser agent. "
                        f"Not a government website.</p></main></body></html>")


def _status(vendor_id):
    with db.state() as s:
        r = s.execute("SELECT status, hold_reason, updated_at FROM portal_vendors WHERE vendor_id=?", (vendor_id,)).fetchone()
    return dict(r) if r else {"status": "Enlisted", "hold_reason": None, "updated_at": None}


@router.get("/portal", response_class=HTMLResponse)
def portal_home(q: str = Query("")):
    rows = ""
    if q.strip():
        with db.work(readonly=True) as w:
            found = w.execute("""SELECT vendor_id, name, cin FROM vendors WHERE name LIKE ? AND vendor_id IN (SELECT vendor_id FROM awards)
                                 ORDER BY name LIMIT 20""", (f"%{q.strip()}%",)).fetchall()
        for v in found:
            st = _status(v["vendor_id"])["status"]
            rows += (f"<tr><td><a data-vendor='{v['vendor_id']}' href='/portal/vendor/{v['vendor_id']}'>{html.escape(v['name'])}</a></td>"
                     f"<td>{v['cin'] or '-'}</td><td>{st}</td></tr>")
        rows = rows or "<tr><td colspan=3>No vendor found.</td></tr>"
    return _page(f"""<h3>Search enlisted vendors</h3>
      <form method='get' action='/portal'><input id='q' name='q' value='{html.escape(q)}' size=40>
      <button id='search' type='submit'>Search</button></form><br>
      <table><tr><th>Vendor</th><th>CIN</th><th>Portal status</th></tr>{rows}</table>""")


@router.get("/portal/vendor/{vendor_id}", response_class=HTMLResponse)
def portal_vendor(vendor_id: int):
    with db.work(readonly=True) as w:
        v = w.execute("SELECT name, cin FROM vendors WHERE vendor_id=?", (vendor_id,)).fetchone()
    if not v:
        return _page("<p>Vendor not found.</p>")
    st = _status(vendor_id)
    cls = "s hold" if st["status"] == "On Hold" else "s"
    reason = f"<p>Reason: {html.escape(st['hold_reason'])}</p>" if st["hold_reason"] else ""
    return _page(f"""<h3>{html.escape(v['name'])}</h3><table><tr><th>CIN</th><td>{v['cin'] or '-'}</td></tr>
      <tr><th>Portal status</th><td id='status' class='{cls}'>{st['status']}</td></tr>
      <tr><th>Last updated</th><td>{st['updated_at'] or '-'}</td></tr></table>{reason}
      <h4>Put vendor on hold</h4><form method='post' action='/portal/vendor/{vendor_id}/hold'>
      <textarea id='reason' name='reason' rows=3 cols=70 required></textarea><br><br>
      <button id='hold' type='submit'>Put on hold</button></form>""")


@router.post("/portal/vendor/{vendor_id}/hold")
def portal_hold(vendor_id: int, reason: str = Form(...)):
    with db.work(readonly=True) as w:
        v = w.execute("SELECT name FROM vendors WHERE vendor_id=?", (vendor_id,)).fetchone()
    with db.state() as s:
        s.execute("INSERT OR REPLACE INTO portal_vendors(vendor_id, name, status, hold_reason, updated_at) VALUES(?,?,?,?,?)",
                  (vendor_id, v["name"] if v else "", "On Hold", reason, audit.now()))
    return RedirectResponse(f"/portal/vendor/{vendor_id}?saved=1", status_code=303)
