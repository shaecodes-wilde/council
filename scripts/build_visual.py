#!/usr/bin/env python3
"""Build the council breakdown page from data.json (schema: references/visual.md).

usage: build_visual.py <data.json> <out.html>

Sections render only when their data is present. If data.json has no "tally"
key and r3/tally.json sits next to it, that file is used. The page uses T3's
theme variables, so it follows light/dark mode inside html_render.
"""
import html
import json
import pathlib
import sys

CSS = """
:root{--s1:var(--chart-1,#2a78d6);--s2:var(--chart-2,#eb6834);--s3:var(--chart-3,#1baf7a);--s4:var(--chart-4,#eda100);--s5:var(--chart-5,#e87ba4);--s6:var(--chart-6,#6250d6)}
body{font-family:var(--font-sans);color:var(--foreground);margin:0}
.wrap{display:flex;flex-direction:column;gap:24px;padding:4px 0 12px}
h2{font-size:15px;margin:0 0 8px;font-weight:650}
.sub{color:var(--muted-foreground);font-size:12.5px;margin:-4px 0 10px;line-height:1.45}
.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:12.5px;color:var(--muted-foreground);align-items:center}
.legend span{display:inline-flex;align-items:center;gap:6px}
.legend .stats{margin-left:auto}
.sw{width:10px;height:10px;border-radius:50%;display:inline-block;flex:none}
.lt{font-size:10.5px;color:var(--muted-foreground);font-weight:600;margin-left:2px}
.rounds{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:6px}
.rd{border:1px solid var(--border);border-radius:var(--radius);padding:9px 10px;background:var(--card);font-size:12px;line-height:1.4}
.rd b{display:block;font-size:12.5px;margin-bottom:2px}.rd span{color:var(--muted-foreground)}
.rd.hl{border-color:var(--s1)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}
.cc{border:1px solid var(--border);border-top:3px solid;border-radius:var(--radius);padding:12px;background:var(--card);font-size:12.5px;line-height:1.45}
.cc h3{margin:0 0 4px;font-size:13.5px}.cc .idea{font-style:italic;margin-bottom:6px}
.cc .k{color:var(--muted-foreground);font-size:11px;text-transform:uppercase;letter-spacing:.03em;margin-top:6px}
.badge{display:inline-block;font-size:11px;font-weight:650;padding:2px 7px;border-radius:8px;background:var(--accent-surface);color:var(--accent-surface-foreground);margin-left:4px;vertical-align:1px}
.chip{display:inline-block;font-size:11px;font-weight:600;padding:1px 7px;border-radius:8px;white-space:nowrap}
.ok{background:var(--accent-surface);color:var(--accent-surface-foreground)}
.warn{background:var(--warning-surface,var(--secondary));color:var(--warning-foreground,var(--foreground))}
.bad{background:var(--destructive-surface,var(--secondary));color:var(--destructive,var(--foreground))}
.mut{background:var(--secondary);color:var(--secondary-foreground)}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th,td{text-align:left;vertical-align:top;padding:7px 8px;border-bottom:1px solid var(--border);line-height:1.4}
th{font-weight:600;color:var(--muted-foreground);font-size:11px;text-transform:uppercase;letter-spacing:.03em}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
td.v{font-weight:600}
.own{color:var(--muted-foreground)}
.top{font-weight:700;text-decoration:underline;text-underline-offset:3px}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:3px;vertical-align:0}
.off{opacity:.18}
.chg{color:var(--muted-foreground);font-size:11px}
.verd{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}
.vd{border:1px solid var(--border);border-top:3px solid;border-radius:var(--radius);padding:12px;background:var(--card);font-size:12.5px;line-height:1.45}
.vd b{display:block;font-size:13px}.vd .big{font-size:18px;font-weight:700;margin:4px 0}
.gates{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}
.gate{border:1px solid var(--border);border-radius:var(--radius);padding:12px;background:var(--card);font-size:12.5px;line-height:1.45}
.gate b{display:block;font-size:13px;margin-bottom:4px}
.gate ul{margin:4px 0 0;padding-left:18px}
svg text{fill:var(--muted-foreground);font-size:11.5px;font-family:var(--font-sans)}
svg .dim{fill:var(--foreground);font-weight:600;font-size:12.5px}
.grid{stroke:var(--border);stroke-width:1}
.tip{position:fixed;pointer-events:none;background:var(--popover);color:var(--popover-foreground);border:1px solid var(--border);border-radius:6px;padding:6px 8px;font-size:12px;opacity:0;transition:opacity .1s;z-index:9}
.foot{font-size:11.5px;color:var(--muted-foreground)}
.foot code{font-family:var(--font-mono)}
"""

CHART_JS = """
const SC=__SC__;const tip=document.getElementById('tip');
const svg=document.getElementById('sc'),NS='http://www.w3.org/2000/svg';
function el(n,a){const e=document.createElementNS(NS,n);for(const k in a)e.setAttribute(k,a[k]);svg.appendChild(e);return e}
function show(e,h){tip.innerHTML=h;tip.style.opacity=1;tip.style.left=(e.clientX+12)+'px';tip.style.top=(e.clientY+10)+'px'}
function draw(){svg.innerHTML='';const W=svg.clientWidth||700,Lx=Math.min(190,W*.34),R=W-30,
lo=SC.min,hi=SC.max,x=v=>Lx+(v-lo)/(hi-lo)*(R-Lx),top=30,rh=44,n=SC.dimensions.length;
svg.setAttribute('height',top+n*rh-6);
for(let v=lo;v<=hi;v++){el('line',{x1:x(v),x2:x(v),y1:top-10,y2:top+n*rh-20,class:'grid'});const t=el('text',{x:x(v),y:12,'text-anchor':'middle'});t.textContent=v}
SC.dimensions.forEach((d,i)=>{const y=top+i*rh;const lb=el('text',{x:0,y:y+4,class:'dim'});lb.textContent=d.length>28?d.slice(0,27)+'…':d;
const vs=SC.series.map(s=>s.values[i]).filter(v=>v!=null);
if(vs.length>1)el('line',{x1:x(Math.min(...vs)),x2:x(Math.max(...vs)),y1:y,y2:y,stroke:'var(--border)','stroke-width':3,'stroke-linecap':'round'});
const seen={};SC.series.forEach(s=>{const v=s.values[i];if(v==null)return;const k=(+v).toFixed(2),j=seen[k]=(seen[k]||0)+1,dy=j==1?0:(j%2?1:-1)*Math.ceil((j-1)/2)*7;
const c=el('circle',{cx:x(v),cy:y+dy,r:6,fill:s.hollow?'var(--background)':s.color,stroke:s.color,'stroke-width':2.5});
c.addEventListener('mousemove',e=>show(e,'<b>'+s.label+'</b> · '+d+'<br>'+(+v).toFixed(2)));c.addEventListener('mouseleave',()=>tip.style.opacity=0)})});}
draw();addEventListener('resize',draw);
"""

RESULT_CHIP = {"true": "ok", "false": "bad", "partly": "warn", "unverifiable": "mut"}
VERDICT_CHIP = {"ship": "ok", "ship-after-fixes": "warn", "block": "bad"}
STATUS = {"settled": ("Settled", "ok"), "split": ("Split", "warn"), "user": ("Your call", "bad")}


def e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    src = pathlib.Path(sys.argv[1])
    D = json.loads(src.read_text(encoding="utf-8"))
    tally = D.get("tally")
    tpath = src.parent / "r3" / "tally.json"
    if tally is None and tpath.exists():
        tally = json.loads(tpath.read_text(encoding="utf-8"))

    seats = {s["letter"]: s for s in D.get("seats", [])}
    order = [s["letter"] for s in D.get("seats", [])]

    def col(L):
        return f"var(--s{seats.get(L, {}).get('color', 1)})"

    def name(L):
        return e(seats.get(L, {}).get("name", L))

    def short(L):
        return e(seats.get(L, {}).get("short") or seats.get(L, {}).get("name", L))

    def tag(L):
        return f'<i class="dot" style="background:{col(L)}"></i>{short(L)}'

    out = []
    # Legend
    leg = "".join(
        f'<span><i class="sw" style="background:{col(L)}"></i>{name(L)}<span class="lt">{e(L)}</span>'
        + (f' <span class="chip mut">dropped {e(", ".join(seats[L]["dropped"]))}</span>' if seats[L].get("dropped") else "")
        + "</span>" for L in order)
    if D.get("stats"):
        leg += f'<span class="stats">{e(D["stats"])}</span>'
    out.append(f'<div class="legend">{leg}</div>')

    if D.get("rounds"):
        rd = "".join(f'<div class="rd{" hl" if r.get("highlight") else ""}"><b>{e(r["name"])}</b><span>{e(r.get("note", ""))}</span></div>' for r in D["rounds"])
        out.append(f'<section><h2>How the debate ran</h2><div class="rounds">{rd}</div></section>')

    if D.get("proposals"):
        cards = []
        for p in D["proposals"]:
            L = p["letter"]
            b = f'<span class="badge">{e(p["badge"])}</span>' if p.get("badge") else ""
            body = f'<div class="idea">“{e(p.get("headline", ""))}”</div>' if p.get("headline") else ""
            if p.get("r1"):
                body += f'<div class="k">Round 1</div>{e(p["r1"])}'
            if p.get("final"):
                body += f'<div class="k">After the debate</div>{e(p["final"])}'
            cards.append(f'<div class="cc" style="border-top-color:{col(L)}"><h3>{name(L)}{b}</h3>{body}</div>')
        out.append(f'<section><h2>{e(D.get("proposals_title", "The proposals"))}</h2><div class="cards">{"".join(cards)}</div></section>')

    # Score chart: explicit, or derived from the tally's peer averages per proposal
    sc = D.get("score_chart")
    if not sc and tally and tally.get("dimensions"):
        sc = {"title": "Peer scores by dimension",
              "note": "Average score each proposal got from the other panelists (self-scores excluded).",
              "dimensions": tally["dimensions"],
              "series": [{"label": seats.get(r["letter"], {}).get("name", r["letter"]), "letter": r["letter"],
                          "values": [r["peer_dim_avg"].get(d) for d in tally["dimensions"]]} for r in tally["proposals"]]}
    chart_js = ""
    if sc:
        for s in sc["series"]:
            s.setdefault("color", col(s["letter"]) if s.get("letter") else "var(--muted-foreground)")
        vals = [v for s in sc["series"] for v in s["values"] if v is not None]
        sc.setdefault("min", max(0, int(min(vals)) - 1) if vals else 0)
        sc.setdefault("max", 10)
        note = f'<div class="sub">{e(sc["note"])}</div>' if sc.get("note") else ""
        legend = "".join(
            f'<span><i class="sw" style="{"background:var(--background);box-shadow:inset 0 0 0 2px " if s.get("hollow") else "background:"}{s["color"]}"></i>{e(s["label"])}</span>'
            for s in sc["series"])
        out.append(f'<section><h2>{e(sc.get("title", "Scores"))}</h2>{note}<svg id="sc" width="100%" height="300" role="img" aria-label="Scores by dimension"></svg><div class="legend" style="margin-top:4px">{legend}</div></section>')
        chart_js = CHART_JS.replace("__SC__", json.dumps(sc).replace("</", "<\\/"))

    if tally and tally.get("proposals"):
        voters = sorted({v for r in tally["proposals"] for v in r["by"]}, key=lambda L: order.index(L) if L in order else 99)
        head = "".join(f'<th class="n">by {short(v)}</th>' for v in voters)
        rows = []
        for r in tally["proposals"]:
            L = r["letter"]
            cells = ""
            for v in voters:
                t = r["by"].get(v)
                cls = "n" + (" own" if v == L else "") + (" top" if tally["top_picks"].get(v) == L else "")
                cells += f'<td class="{cls}">{"" if t is None else t}</td>'
            win = ' <span class="badge">Winner</span>' if tally.get("winner") == L else ""
            rows.append(f'<tr><td class="v">{tag(L)}{win}</td>{cells}<td class="n v">{"" if r["peer_avg"] is None else r["peer_avg"]}</td><td class="n v">{r["borda"]}</td><td class="n">{r["first_place"]}</td></tr>')
        tie = f' <span class="chip warn">Tie: {e(", ".join(seats.get(x, {}).get("name", x) for x in tally["tied"]))}</span>' if tally.get("tied") else ""
        inv = f'<div class="sub">Invalid ballots left out: {e("; ".join(f"{seats.get(k, {}).get("name", k)}: {v}" for k, v in tally["invalid"].items()))}</div>' if tally.get("invalid") else ""
        out.append(f'<section><h2>Round 3: scores and ranked vote{tie}</h2><div class="sub">Totals out of {tally["max_total"]}. Grey = scoring itself. Underlined = that scorer\'s first choice. Borda: each panelist ranked the others (it could not rank itself).</div>{inv}'
                   f'<div class="scroll"><table><thead><tr><th>Proposal</th>{head}<th class="n">Peer avg</th><th class="n">Borda</th><th class="n">1st</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>')

    if D.get("consensus"):
        rows = []
        for c in D["consensus"]:
            dots = "".join(f'<i class="dot{"" if L in c.get("agree", []) else " off"}" style="background:{col(L)}" title="{name(L)}"></i>' for L in order)
            n = len(c.get("agree", []))
            chip = "ok" if n == len(order) else "warn"
            rows.append(f'<tr><td>{e(c["text"])}</td><td style="white-space:nowrap">{dots}</td><td><span class="chip {chip}">{n}/{len(order)}</span></td></tr>')
        out.append(f'<section><h2>Where they agree</h2><table><tbody>{"".join(rows)}</tbody></table></section>')

    if D.get("disputes"):
        head = "".join(f'<th><i class="dot" style="background:{col(L)}"></i>{short(L)}</th>' for L in order)
        rows = []
        for d in D["disputes"]:
            cells = ""
            for L in order:
                p = d.get("positions", {}).get(L)
                if p:
                    ch = ' <span class="chg" title="changed position">↻</span>' if p.get("changed") else ""
                    cells += f'<td>{e(p.get("label", ""))}{ch}</td>'
                else:
                    cells += '<td class="own">–</td>'
            lab, cls = STATUS.get(d.get("status", "split"), STATUS["split"])
            rows.append(f'<tr><td class="v">{e(d["id"])} · {e(d["question"])}</td>{cells}<td>{e(d.get("outcome", ""))} <span class="chip {cls}">{lab}</span></td></tr>')
        out.append(f'<section><h2>The disputes</h2><div class="sub">Final position of each panelist. ↻ = changed its mind during the debate.</div>'
                   f'<div class="scroll"><table><thead><tr><th>Dispute</th>{head}<th>Outcome</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>')

    rt = D.get("redteam")
    if rt:
        cards = "".join(
            f'<div class="vd" style="border-top-color:{col(v["letter"])}"><b>{name(v["letter"])}</b>'
            f'<div class="big"><span class="chip {VERDICT_CHIP.get(v["verdict"], "mut")}" style="font-size:13px">{e(v["verdict"])}</span></div>{e(v.get("summary", ""))}</div>'
            for v in rt.get("verdicts", []))
        critics = [v["letter"] for v in rt.get("verdicts", [])]
        head = "".join(f'<th>{short(L)}</th>' for L in critics)
        rows = "".join(
            f'<tr><td class="v">{e(i["issue"])}</td><td>{e(i.get("fix", ""))}</td>'
            + "".join(f'<td>{e(i.get("raised", {}).get(L, ""))}</td>' for L in critics) + "</tr>"
            for i in rt.get("issues", []))
        table = f'<div class="scroll" style="margin-top:12px"><table><thead><tr><th>Issue</th><th>Fix</th>{head}</tr></thead><tbody>{rows}</tbody></table></div>' if rows else ""
        out.append(f'<section><h2>Red team: {name(rt["winner"])}\'s merged plan</h2><div class="verd">{cards}</div>{table}</section>')

    if D.get("factcheck"):
        rows = "".join(
            f'<tr><td>{e(f["claim"])}</td><td style="white-space:nowrap">{tag(f["by"]) if f.get("by") else ""}</td>'
            f'<td><span class="chip {RESULT_CHIP.get(f.get("result"), "mut")}">{e(f.get("result", ""))}</span></td><td>{e(f.get("evidence", ""))}</td></tr>'
            for f in D["factcheck"])
        out.append(f'<section><h2>Fact-checks</h2><div class="sub">Verifiable claims the moderator checked against the sources. Opinions were not rated.</div>'
                   f'<div class="scroll"><table><thead><tr><th>Claim</th><th>By</th><th>Result</th><th>Evidence</th></tr></thead><tbody>{rows}</tbody></table></div></section>')

    gates = []
    for u in D.get("user_calls", []):
        opts = "".join(f"<li>{e(o)}</li>" for o in u.get("options", []))
        by = ", ".join(seats.get(L, {}).get("short") or seats.get(L, {}).get("name", L) for L in u.get("raised_by", []))
        raised = f'<div class="sub" style="margin:6px 0 0">Raised by {e(by)}</div>' if by else ""
        gates.append(f'<div class="gate"><b>{e(u["question"])}</b>{f"<ul>{opts}</ul>" if opts else ""}{raised}</div>')
    for k in D.get("unknowns", []):
        gates.append(f'<div class="gate"><b>{e(k["title"])}</b>{e(k.get("text", ""))}</div>')
    if gates:
        out.append(f'<section><h2>Still open: your call</h2><div class="gates">{"".join(gates)}</div></section>')

    if D.get("folder"):
        out.append(f'<div class="foot">All rounds, briefs and fact-checks are in <code>{e(D["folder"])}</code></div>')

    page = (f'<!doctype html><html><head><meta charset="utf-8"><title>{e(D.get("title", "Council"))}</title><style>{CSS}</style></head>'
            f'<body><div class="wrap">{"".join(out)}</div><div class="tip" id="tip"></div>'
            + (f"<script>{chart_js}</script>" if chart_js else "") + "</body></html>")
    pathlib.Path(sys.argv[2]).write_text(page, encoding="utf-8")
    print(f"wrote {sys.argv[2]} ({len(page):,} chars)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
