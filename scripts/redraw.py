#!/usr/bin/env python3
"""Redraw the portolan chart of a linked-note corpus.

Pulls Obsidian's RESOLVED link graph over the Local REST API (not text scraped
for double brackets), measures silt from git history (not file mtimes, which the
iCloud relocation rewrote in bulk), and injects the result into template.html.

Usage:  redraw.py [--vault PATH] [--out PATH] [--graph FILE]
--graph loads a pre-built corpus (the porting contract below, as JSON) instead of
querying Obsidian and git, which is how a synthetic or non-Obsidian corpus is drawn.
Every number and sentence on the plate is computed here; the template holds none.
Exit 0 on success. Exit 2 if Obsidian is not running (the REST API is the only
source of the resolved graph; there is no honest fallback). Exit 4 if the fetch
stalls past RUN_DEADLINE (stacks dumped to stderr).
"""
import argparse, faulthandler, glob, html, json, os, re, signal, ssl, statistics, subprocess, sys, urllib.parse, urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_VAULT = os.path.expanduser(os.environ.get("CARTA_VAULT", "~/vault"))
SKIP = ("output/", "tmp/", "node_modules/", ".obsidian/")
# Vendored third-party repos live inside the vault, and their README/LICENSE/
# CHANGELOG files are not notes. Counting them inflated the first chart by 41
# and invented 27 phantom orphans in 10 Projects.
PKG_DOC = re.compile(r"(^|/)(README|readme|Readme|CHANGELOG|LICENCE|LICENSE|"
                     r"CONTRIBUTING|CODE_OF_CONDUCT|SECURITY)\.md$")
RUN_DEADLINE = 600      # seconds for the whole live run; a stalled REST call must not hang the weekly job
PORTS_MIN = 30          # a note is a named port at this many backlinks
PORTS_MAX = 6           # and at most this many ports are named
LANES_MIN = 4           # a rhumb line is drawn at this many crossing links
SILT_MONTH, SILT_GONE = 25, 70
BALANCED = 0.8          # a lane "runs both ways" when the return leg is >= this share
# Frontmatter keys that count as a date for the coverage figure in the marginalia.
DATE_KEYS = {"created", "date", "updated", "modified", "last_updated", "last-updated"}


def api_key(vault):
    hits = glob.glob(os.path.join(vault, ".obsidian/plugins/*local-rest-api*/data.json"))
    if not hits:
        sys.exit("no Local REST API plugin data.json found in vault")
    return json.load(open(hits[0]))["apiKey"]


def fetch_graph(vault):
    key = api_key(vault)
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    base = "https://127.0.0.1:27124"

    def get(path, note=False):
        r = urllib.request.Request(base + path)
        r.add_header("Authorization", "Bearer " + key)
        if note:
            r.add_header("Accept", "application/vnd.olrapi.note+json")
        with urllib.request.urlopen(r, context=ctx, timeout=25) as resp:
            return json.loads(resp.read().decode())

    def enc(p):
        return urllib.parse.quote(p, safe="/")   # PARA folders contain spaces

    def walk(prefix=""):
        out = []
        try:
            listing = get("/vault/" + enc(prefix))
        except Exception:
            return out
        for f in listing.get("files", []):
            full = prefix + f
            if f.endswith("/"):
                if not any(full.startswith(s) for s in SKIP):
                    out += walk(full)
            elif f.endswith(".md"):
                if not (PKG_DOC.search(full) and full.count("/") >= 2):
                    out.append(full)
        return out

    try:
        files = walk()
    except Exception as e:
        sys.exit(2)
    if not files:
        sys.exit(2)

    # One HTTPS round-trip per note. Sequentially that is minutes on a 380-note
    # vault, which is too slow for an unattended weekly job. The REST API is
    # local and read-only here, so fan out.
    def one(f):
        try:
            n = get("/vault/" + enc(f), note=True)
            rec = {"links": n.get("links") or [], "backlinks": n.get("backlinks") or []}
            # Same response, no extra round-trip: file mtime and whether the
            # frontmatter carries a date. Both feed the marginalia, which reports
            # how the two rejected clocks compare with git on this redraw.
            mt = (n.get("stat") or {}).get("mtime")
            if isinstance(mt, (int, float)):
                rec["mtime"] = mt / 1000.0
            fm = n.get("frontmatter")
            if isinstance(fm, dict):
                rec["fm_date"] = any(str(k).lower() in DATE_KEYS and v not in (None, "", [])
                                     for k, v in fm.items())
            return f, rec
        except Exception:
            return f, None

    notes = {}
    with ThreadPoolExecutor(max_workers=12) as pool:
        for f, rec in pool.map(one, files):
            if rec is not None:
                notes[f] = rec
    return notes


def git_last_touched(vault):
    """Last commit date per file. Honest, but censored at repo genesis."""
    try:
        out = subprocess.run(
            ["git", "-C", vault, "log", "--pretty=format:C|%ct", "--name-only", "--", "*.md"],
            capture_output=True, text=True, timeout=180).stdout
    except Exception:
        return {}, None
    last, ts, first = {}, None, None
    for line in out.splitlines():
        if line.startswith("C|"):
            ts = int(line[2:])
            first = ts if first is None else min(first, ts)
        elif line.strip() and ts:
            last.setdefault(line, ts)
    # The record begins at the earliest commit, which is not always the earliest
    # last-touch: once every note has been edited since, those two part ways.
    genesis = first if last else None
    return last, genesis


def region(p):
    return p.split("/")[0] if "/" in p else "(root)"


def days_ago(ts, today):
    return (today - datetime.fromtimestamp(ts).date()).days


def build(notes, gitlast, genesis, today):
    terr = defaultdict(lambda: {"n": 0, "orph": 0, "ages": [], "fm": 0, "fm_known": 0})
    all_ages, mt_ages = [], []
    for p, v in notes.items():
        r = terr[region(p)]
        r["n"] += 1
        if not v["links"] and not v["backlinks"]:
            r["orph"] += 1
        ts = gitlast.get(p)
        if ts:
            r["ages"].append(days_ago(ts, today))
            all_ages.append(r["ages"][-1])
        if isinstance(v.get("mtime"), (int, float)):
            mt_ages.append(days_ago(v["mtime"], today))
        if "fm_date" in v:
            r["fm_known"] += 1
            r["fm"] += 1 if v["fm_date"] else 0

    cen_days = days_ago(genesis, today) - 1 if genesis else None
    rows = []
    for name, d in sorted(terr.items(), key=lambda kv: (-kv[1]["n"], kv[0])):
        silt = round(statistics.median(d["ages"])) if d["ages"] else None
        rows.append({"name": name, "notes": d["n"],
                     "silt": silt if silt is not None else cen_days,
                     "cen": sum(1 for a in d["ages"] if cen_days is not None and a >= cen_days),
                     "orph": d["orph"], "read": reading(name, d, silt, cen_days),
                     "fm": d["fm"], "fm_known": d["fm_known"]})

    cross = Counter()
    for p, v in notes.items():
        a = region(p)
        for l in v["links"]:
            b = region(l)
            if a != b:
                cross[(a, b)] += 1
    # a lane into a folder with no charted notes has no coast to land on, so it
    # is neither drawn nor counted
    charted = {region(p) for p in notes}
    lanes = [[a, b, n] for (a, b), n in sorted(cross.items(), key=lambda kv: (-kv[1], kv[0]))
             if n >= LANES_MIN and a in charted and b in charted]

    ranked = sorted(((os.path.basename(p)[:-3], len(v["backlinks"]), region(p))
                     for p, v in notes.items()), key=lambda x: (-x[1], x[0]))
    ports = [list(x) for x in ranked if x[1] >= PORTS_MIN][:PORTS_MAX]

    total_links = sum(len(v["links"]) for v in notes.values())
    meta = {"total": len(notes), "links": total_links, "cross": sum(cross.values()),
            "nterr": len(rows), "nlanes": len(lanes), "censor": cen_days,
            "genesis": genesis, "median_age": round(statistics.median(all_ages)) if all_ages else None,
            "median_mtime": round(statistics.median(mt_ages)) if mt_ages else None,
            "ranked": ranked[:3], "cross_pairs": cross}
    meta["cross_pct"] = round(meta["cross"] / total_links * 100) if total_links else 0
    data = {"terr": [{k: r[k] for k in ("name", "notes", "silt", "cen", "orph", "read")} for r in rows],
            "lanes": lanes, "ports": ports,
            # Read by the template's script. No number on the plate is typed into the template.
            "meta": {"hub": hub_caption(meta), "silt_month": SILT_MONTH, "silt_gone": SILT_GONE}}
    return data, meta, rows


def reading(name, d, silt, cen):
    pct = d["orph"] / d["n"] * 100 if d["n"] else 0
    if d["orph"] and pct >= 20:
        return f"worked, yet {round(pct)} percent unreachable"
    if silt is not None and silt >= SILT_GONE:
        return "silted, beyond the record"
    if d["orph"] == 0:
        return "no orphans, a clean territory"
    return "current"


# ---- prose -----------------------------------------------------------------
# Every sentence below is written from the numbers of this redraw. The template
# used to carry findings, a centre caption and an accessibility description typed
# in by hand on the first drawing, and they went stale the following Sunday.

ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
        "fourteen fifteen sixteen seventeen eighteen nineteen").split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def words(n):
    """Spell out 0 to 100 (house style), numerals with separators above."""
    if n is None:
        return "an unknown number of"
    n = int(n)
    if 0 <= n < 20:
        return ONES[n]
    if 20 <= n < 100:
        return TENS[n // 10] + ("-" + ONES[n % 10] if n % 10 else "")
    if n == 100:
        return "one hundred"
    return f"{n:,}"


def cap(s):
    return s[:1].upper() + s[1:]


def count(n, one, many=None):
    return f"{words(n)} {one if n == 1 else (many or one + 's')}"


def pct(a, b):
    return round(a * 100 / b) if b else 0


def esc(s):
    return html.escape(str(s), quote=True)


def short(name):
    return re.sub(r"^\d+\s", "", name)


def T(name):
    return f'<span class="terr">{esc(name)}</span>'


def N(name):
    return f"<i>{esc(name)}</i>"


def roman(i):
    return ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"][i]


def series(items):
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def nice_date(ts):
    return datetime.fromtimestamp(ts).strftime("%-d %B %Y")


def hub_caption(meta):
    """The centre caption, as parts; the template joins them on one line or two."""
    return [f"{meta['total']:,} {'note' if meta['total'] == 1 else 'notes'}",
            f"{meta['links']:,} {'link' if meta['links'] == 1 else 'links'}",
            f"{meta['cross_pct']}% cross a border"]


def share_head(p):
    if p >= 0.9:
        return "Nearly all traffic crosses a border"
    if p >= 0.67:
        return "Most traffic crosses a border"
    for floor, phrase in ((0.53, "More than half"), (0.47, "Half"), (0.38, "Nearly half"),
                          (0.28, "About a third"), (0.2, "About a quarter")):
        if p >= floor:
            return f"{phrase} of all traffic crosses a border"
    return "Little traffic crosses a border"


def is_archive(name):
    return re.search(r"archiv", name, re.I) is not None


def findings(rows, data, meta):
    out = []
    total = meta["total"]

    # i. The most silted coast, read against the censor.
    cen_rows = [r for r in rows if r["cen"] > 0]
    silt_terr = None
    if cen_rows:
        t = max(cen_rows, key=lambda r: (r["cen"] / r["notes"], r["cen"]))
        silt_terr = t["name"]
        body = (f"{cap(words(t['cen']))} of {count(t['notes'], 'holding')} in {T(t['name'])} "
                f"sit at the censor, untouched for the whole life of the record.")
        if is_archive(t["name"]):
            out.append(("The archive is silted, correctly", body +
                        " In an archive that is the intended behaviour rather than decay. "
                        "It is the one coast that should read this way."))
        else:
            tail = ""
            if t["silt"] is not None and meta["median_age"] is not None:
                tail = (f" Its median note was last committed {count(t['silt'], 'day')} ago, "
                        f"against {words(meta['median_age'])} across the whole corpus.")
            out.append((f"{T(t['name'])} is the most silted coast", body + tail))
    elif meta["genesis"] and rows:
        t = max(rows, key=lambda r: (r["silt"] or 0))
        silt_terr = t["name"]
        out.append(("Nothing sits at the censor",
                    f"Every note has been committed since the record began on "
                    f"{nice_date(meta['genesis'])}. The oldest coast is {T(t['name'])}, at a "
                    f"median of {count(t['silt'], 'day')}."))

    # ii. The orphans: largest share, preferring territories big enough to mean it.
    orows = [r for r in rows if r["orph"] > 0]
    if not orows:
        out.append(("Every note is reachable",
                    "No note in any territory is cut off: each one links out, is linked to, or both."))
    else:
        pool = [r for r in orows if r["notes"] >= 5] or orows
        t = max(pool, key=lambda r: (r["orph"] / r["notes"], r["orph"]))
        head = (f"The real silt is in {T(t['name'])}" if t["name"] != silt_terr
                else f"{T(t['name'])} is also cut off")
        verb = ("links to nothing and is linked from nothing" if t["orph"] == 1
                else "link to nothing and are linked from nothing")
        body = (f"{cap(words(t['orph']))} of {count(t['notes'], 'note')} in {T(t['name'])}, "
                f"{words(pct(t['orph'], t['notes']))} percent of the territory, {verb}.")
        if t["silt"] is not None and t["silt"] < SILT_MONTH:
            body += " The coast is worked constantly, yet these notes cannot be reached from any other shore."
        big = max(orows, key=lambda r: (r["orph"], r["notes"]))
        if big["name"] != t["name"]:
            body += (f" By count the most orphans lie in {T(big['name'])}: "
                     f"{words(big['orph'])} of {words(big['notes'])}.")
        out.append((head, body))

    # iii. The principal port.
    ranked = meta["ranked"]
    if ranked and ranked[0][1] > 0:
        (n1, b1, r1) = ranked[0]
        if len(ranked) > 1 and ranked[1][1] == b1:
            head = "Two notes share the most backlinks"
            body = (f"{N(n1)} and {N(ranked[1][0])} carry {words(b1)} backlink{'' if b1 == 1 else 's'} each, "
                    f"more than any other note.")
        else:
            head = (f"{N(n1)} is the principal port" if data["ports"]
                    else f"{N(n1)} is the most-linked note")
            body = (f"{N(n1)} carries {count(b1, 'backlink')}, more than any other note, "
                    f"from its berth in {T(r1)}.")
            if len(ranked) > 1 and ranked[1][1] > 0:
                body += f" The next is {N(ranked[1][0])} at {words(ranked[1][1])}."
        if not data["ports"]:
            body += (f" No note reaches the {words(PORTS_MIN)} backlinks that would name it a "
                     f"port, so none is drawn on this plate.")
        out.append((head, body))

    # iv. Where the ports cluster.
    ports = data["ports"]
    if len(ports) >= 2:
        by = Counter(r for _, _, r in ports)
        reg, k = by.most_common(1)[0]
        tn = next((r["notes"] for r in rows if r["name"] == reg), 0)
        if k == len(ports):
            out.append((f"Every port lies in {T(reg)}",
                        f"All {words(len(ports))} named ports lie in {T(reg)}, which holds "
                        f"{words(pct(tn, total))} percent of the notes."))
        elif k >= 2:
            out.append((f"The ports cluster in {T(reg)}",
                        f"{cap(words(k))} of the {words(len(ports))} named ports lie in {T(reg)}, "
                        f"which holds {words(pct(tn, total))} percent of the notes."))
        else:
            out.append(("The harbours are spread",
                        f"The {words(len(ports))} named ports lie in {words(len(by))} different "
                        f"territories; no single coast holds the harbours."))

    # v. Which lanes run both ways, and where the traffic goes.
    cross = meta["cross_pairs"]
    if not data["lanes"]:
        if len(rows) > 1:
            out.append(("No lane is drawn",
                        f"No border carries {words(LANES_MIN)} or more links, so the plate shows "
                        f"its territories without rhumb lines."))
    else:
        seen, both = set(), []
        for a, b, n in data["lanes"]:
            back = cross.get((b, a), 0)
            key = tuple(sorted((a, b)))
            if key in seen or back < LANES_MIN:
                continue
            seen.add(key)
            if min(n, back) / max(n, back) >= BALANCED:
                both.append((a, b, n, back))
        if both:
            a, b, x, y = both[0]
            head = ("One lane runs both ways" if len(both) == 1
                    else f"{cap(words(len(both)))} lanes run both ways")
            body = (f"{T(a)} sends {count(x, 'sailing')} to {T(b)} and receives {words(y)} back, "
                    + ("the only route in near balance." if len(both) == 1
                       else f"the busiest of {words(len(both))} routes in near balance."))
        else:
            head = "Every lane flows one way"
            body = "No drawn route carries back as much as four-fifths of what it sends."
        inbound = Counter()
        for (a, b), n in cross.items():
            inbound[b] += n
        dest, din = inbound.most_common(1)[0]
        body += (f" {cap(words(pct(din, meta['cross'])))} percent of all border traffic flows "
                 f"toward {T(dest)}.")
        out.append((head, body))

    # vi. How much traffic crosses a border at all.
    if meta["links"]:
        p = meta["cross"] / meta["links"]
        head = share_head(p)
        if meta["cross"]:
            body = (f"{meta['cross']:,} of {meta['links']:,} links, {words(meta['cross_pct'])} "
                    f"percent, leave their territory.")
        else:
            body = "No link leaves its territory."
        if p >= 0.35:
            body += (" A brain filed in folders but read as a graph: the folders describe where "
                     "things sit, not how they are used.")
        else:
            body += " The folders hold: most links stay inside the territory they start from."
        out.append((head, body))
    else:
        out.append(("No links are charted",
                    "The corpus has no resolved links, so there is no traffic to draw."))
    return out


def findings_html(items):
    return "\n".join(
        f'      <div class="finding"><h3><b>{roman(i)}</b>{h}</h3>\n        <p>{p}</p></div>'
        for i, (h, p) in enumerate(items))


def describe(rows, data, meta):
    """Plain-text accessibility description of the plate actually drawn."""
    s = [f"{cap(count(meta['nterr'], 'territory', 'territories'))} arranged around a compass rose, "
         f"drawn from {count(meta['total'], 'note')} and {count(meta['links'], 'link')}. "
         f"Arc length is note count, wash is silt, and the red outer tick is the orphaned share."]
    if meta["nlanes"]:
        s.append(f"{cap(count(meta['nlanes'], 'rhumb line'))} show link traffic across territory "
                 f"borders, {meta['cross_pct']} percent of all links.")
    else:
        s.append("No border carries enough traffic to draw a rhumb line.")
    silted = [short(r["name"]) for r in rows if r["silt"] is not None and r["silt"] >= SILT_GONE]
    if silted:
        s.append(f"{series(silted)} {'is' if len(silted) == 1 else 'are'} drawn silted.")
    orows = [r for r in rows if r["orph"] > 0]
    if orows:
        t = max(orows, key=lambda r: (r["orph"], -r["notes"]))
        s.append(f"{short(t['name'])} carries the longest orphan tick, "
                 f"{count(t['orph'], 'unlinked note')}.")
    else:
        s.append("No territory has an orphaned note.")
    if data["ports"]:
        n, b, _ = data["ports"][0]
        s.append(f"{cap(count(len(data['ports']), 'port'))} "
                 f"{'is' if len(data['ports']) == 1 else 'are'} named; the largest is {n}, "
                 f"with {count(b, 'backlink')}.")
    else:
        s.append("No port is named.")
    return esc(" ".join(s))


def standfirst_lead(rows, meta):
    lead = (f"{cap(count(meta['nterr'], 'territory', 'territories'))}, "
            + (f"{count(meta['cross'], 'sailing')} between them" if meta["cross"]
               else "no sailings between them"))
    gone = [r for r in rows if r["notes"] and r["cen"] / r["notes"] >= 0.5]
    if len(gone) == 1:
        lead += (f", and one coast, {T(gone[0]['name'])}, that has mostly gone unvisited since "
                 f"the charts were recopied.")
    elif gone:
        lead += (f", and {words(len(gone))} coasts that have mostly gone unvisited since the "
                 f"charts were recopied.")
    else:
        lead += ", and no coast left unvisited since the charts were recopied."
    return lead


def record_sentence(meta):
    if not meta["genesis"]:
        return ("No commit history was found, so silt cannot be measured and no figure here "
                "should be read as an age.")
    return (f"The record begins {nice_date(meta['genesis'])}, with the earliest commit the "
            f"repository holds. “At censor” counts notes whose true age cannot be known because "
            f"it predates that date, so every figure here is a floor and never a ceiling.")


def mtime_agrees(meta):
    m, g = meta["median_mtime"], meta["median_age"]
    return m is not None and g is not None and abs(m - g) <= max(3, 0.15 * g)


def mtime_clause(meta):
    """Close the standfirst's sentence about file timestamps with what they say today."""
    m, g = meta["median_mtime"], meta["median_age"]
    if m is not None and g is not None and m < 0.5 * g:
        return "and now report every shore as new."
    if mtime_agrees(meta):
        return ("and, though they now agree with git, a bulk copy could reset them again "
                "without leaving a record.")
    return "and a bulk copy can reset them again without leaving a record."


def instruments(rows, meta):
    s = []
    if mtime_agrees(meta):
        s.append(f"File modification times were rewritten in bulk by the move out of iCloud. On "
                 f"this redraw they put the median note at {count(meta['median_mtime'], 'day')} old, "
                 f"level with git, but they keep no record of their own and a second bulk copy "
                 f"would erase them again.")
    elif meta["median_mtime"] is not None and meta["median_age"] is not None:
        s.append(f"File modification times were rewritten in bulk by the move out of iCloud; on "
                 f"this redraw they put the median note at {count(meta['median_mtime'], 'day')} old, "
                 f"against {words(meta['median_age'])} by git.")
    else:
        s.append("File modification times were not measured on this redraw; on this vault they "
                 "were rewritten in bulk by the move out of iCloud.")
    known = sum(r["fm_known"] for r in rows)
    if known:
        have = sum(r["fm"] for r in rows)
        line = f"Frontmatter dates cover {words(pct(have, known))} percent of notes"
        big = [r for r in rows if r["fm_known"] >= 5]
        if len(big) >= 2:
            lo = min(big, key=lambda r: r["fm"] / r["fm_known"])
            hi = max(big, key=lambda r: r["fm"] / r["fm_known"])
            if pct(hi["fm"], hi["fm_known"]) - pct(lo["fm"], lo["fm_known"]) >= 10:
                line += (f", from {words(pct(lo['fm'], lo['fm_known']))} percent in {T(lo['name'])} "
                         f"to {words(pct(hi['fm'], hi['fm_known']))} in {T(hi['name'])}, which would "
                         f"bias any comparison between territories")
        s.append(line + ".")
    else:
        s.append("Frontmatter dates were not measured on this redraw.")
    if meta["genesis"]:
        s.append(f"Git history is used here and is honest, but cannot see before "
                 f"{nice_date(meta['genesis'])}.")
    else:
        s.append("Git history would be the honest clock, but none was found for this corpus.")
    return " ".join(s)


def render(data, meta, rows, today, out_path):
    tpl = open(os.path.join(HERE, "template.html")).read()
    tokens = {
        "DATE": today.strftime("%-d %B %Y"),
        "TOTAL": f"{meta['total']:,}",
        "SOUNDINGS": "sounding" if meta["total"] == 1 else "soundings",
        "STANDFIRST_LEAD": standfirst_lead(rows, meta),
        "MTIME_CLAUSE": mtime_clause(meta),
        "DESC": describe(rows, data, meta),
        "PORTS_MIN": words(PORTS_MIN),
        "PORTS_MAX": words(PORTS_MAX),
        "LANES_MIN": words(LANES_MIN),
        "RECORD": record_sentence(meta),
        "FINDINGS": findings_html(findings(rows, data, meta)),
        "INSTRUMENTS": instruments(rows, meta),
    }
    # One pass over the template, before the data goes in, so a note title that
    # happens to contain a marker can never be substituted.
    def sub(m):
        if m.group(1) in ("DATA", "ENDDATA"):
            return m.group(0)
        if m.group(1) not in tokens:
            raise KeyError(f"template marker __{m.group(1)}__ has no value")
        return tokens[m.group(1)]
    html_out = re.sub(r"__([A-Z_]+?)__", sub, tpl)
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    html_out, n = re.subn(r"/\*__DATA__\*/\{\}/\*__ENDDATA__\*/", lambda _: payload, html_out)
    if n != 1:
        raise RuntimeError("template data marker not found exactly once")
    if os.path.dirname(out_path):
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html_out)
    return html_out


def load_graph(path):
    """A pre-built corpus: {"notes": {...}, "gitlast": {...}, "genesis": ts|null}."""
    g = json.load(open(path, encoding="utf-8"))
    notes = {p: {"links": v.get("links") or [], "backlinks": v.get("backlinks") or [],
                 **{k: v[k] for k in ("mtime", "fm_date") if k in v}}
             for p, v in g["notes"].items()}
    return notes, {p: int(t) for p, t in (g.get("gitlast") or {}).items()}, g.get("genesis")


def arm_deadline():
    """Whole-run watchdog. urlopen's timeout is per socket read, so a connection that
    stays open without answering (seen 2026-10-05: open TCP to Obsidian :27124, no CPU,
    no output for 10 min) is never cut off. SIGALRM runs on the main thread even while
    pool workers are blocked; it dumps every thread's stack to stderr, then hard-exits
    (a normal exit would wait on the blocked workers)."""
    def stuck(signum, frame):
        print(f"[carta] FATAL: no result after {RUN_DEADLINE}s; thread stacks follow", file=sys.stderr)
        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        sys.stderr.flush()
        os._exit(4)
    signal.signal(signal.SIGALRM, stuck)
    signal.alarm(RUN_DEADLINE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", default=DEFAULT_VAULT)
    ap.add_argument("--out", default=os.path.join(DEFAULT_VAULT, "90 Meta/Charts/carta-del-cervello.html"))
    ap.add_argument("--graph", help="draw a pre-built corpus (JSON, the porting contract) "
                                    "instead of querying Obsidian and git")
    a = ap.parse_args()
    today = date.today()

    if a.graph:
        notes, gitlast, genesis = load_graph(a.graph)
    else:
        # Preflight. A launchd agent has no Full Disk Access by default, so reading a
        # vault under ~/Documents returns nothing and the job would otherwise stall or
        # write an empty chart. Fail loudly with the actual remedy instead.
        if not os.access(a.vault, os.R_OK) or not os.listdir(a.vault):
            print(f"[carta] FATAL: cannot read {a.vault}", file=sys.stderr)
            print("[carta] macOS TCC is blocking this process from ~/Documents. Grant Full Disk "
                  "Access to the interpreter in System Settings > Privacy & Security > Full Disk "
                  "Access, or run this from a context that already has it.", file=sys.stderr)
            sys.exit(3)
        arm_deadline()
        notes = fetch_graph(a.vault)
        gitlast, genesis = git_last_touched(a.vault)
    if not notes:
        sys.exit("[carta] no notes to chart")
    signal.alarm(0)   # fetching done; drawing is local and bounded
    data, meta, rows = build(notes, gitlast, genesis, today)
    render(data, meta, rows, today, a.out)

    print(f"[carta] {meta['total']} notes · {meta['links']} links · "
          f"{meta['cross_pct']}% cross · {len(data['lanes'])} lanes")
    print(f"[carta] wrote {a.out}")
    worst = max(data["terr"], key=lambda t: t["orph"] / max(1, t["notes"]))
    print(f"[carta] most orphaned: {worst['name']} "
          f"({worst['orph']}/{worst['notes']}, {pct(worst['orph'], worst['notes'])}%)")


if __name__ == "__main__":
    main()
