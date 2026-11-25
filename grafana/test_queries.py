"""Run every dashboard query (variables first, then the main query) against a Grafana.

Fills in dropdowns with their defaults ("All" expands to every option), the way the
Infinity plugin would, and prints option lists and row counts. Usage:
    python grafana/test_queries.py http://127.0.0.1:3000
Point it at a scratch Grafana with the Infinity plugin and a datasource of uid dg3hq8oc1y8e8c.
"""
import json, re, sys, urllib.request, collections
from pathlib import Path
L = sys.argv[1]
DASH_DIR = Path(__file__).resolve().parent / "dashboards"
def run(target, vals):
    t = json.loads(json.dumps(target))
    def sub(s):
        return re.sub(r"\$\{(\w+):doublequote\}", lambda m: ",".join(f'"{v}"' for v in vals[m.group(1)]), s)
    import time, datetime as dt
    iso = lambda ms: dt.datetime.fromtimestamp(ms/1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    now = time.time()*1000
    t["root_selector"] = sub(t["root_selector"]).replace("${__from:date:iso}", iso(now-30*86400e3)).replace("${__to:date:iso}", iso(now))
    body = json.dumps({"queries": [t], "from": "now-30d", "to": "now"}).encode()
    req = urllib.request.Request(L + "/api/ds/query", body, {"Content-Type": "application/json"})
    try:
        r = json.load(urllib.request.urlopen(req, timeout=120))
    except urllib.error.HTTPError as e:
        return {"error": e.read().decode()[:600]}
    res = next(iter(r["results"].values()))
    if res.get("error"): return {"error": res["error"][:600]}
    fr = res["frames"][0]; names = [f["name"] for f in fr["schema"]["fields"]]
    return [dict(zip(names, row)) for row in zip(*fr["data"]["values"])] if fr["data"]["values"] else []
for uid in ("speculators-ci-bench", "speculators-ci-acceptance"):
    d = json.load(open(DASH_DIR / f"{uid}.json")); print("=====", uid)
    vals = {}
    for v in d["templating"]["list"]:
        rows = run(v["query"]["infinityQuery"], vals)
        if isinstance(rows, dict): print(" VAR", v["name"], "ERROR", rows["error"]); sys.exit(1)
        opts = [r["v"] for r in rows]
        cur = v["current"]["value"]; chosen = opts if cur == ["$__all"] else cur
        vals[v["name"]] = chosen
        print(f" VAR {v['name']}: {len(opts)} options {opts[:8]}{' ...' if len(opts)>8 else ''} -> using {chosen[:3]}{'...' if len(chosen)>3 else ''}")
    rows = run(d["panels"][0]["targets"][0], vals)
    if isinstance(rows, dict): print(" SOURCE ERROR", rows["error"]); sys.exit(1)
    print(" SOURCE rows", len(rows))
    print("  series sample", sorted({r["series"] for r in rows})[:12])
    print("  part values", sorted({str(r["part"]) for r in rows})[:12], " pos values", sorted({str(r["pos"]) for r in rows})[:10])
    print("  metrics", collections.Counter(r["metric"] for r in rows).most_common(4))
    print("  sample row", {k: rows[0][k] for k in ("Time","test","env","file","series","branch","metric","value")})
