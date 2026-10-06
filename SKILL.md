---
name: chart-corpus
description: Draw a linked-note corpus as a portolan chart — territories sized by population and washed by staleness, rhumb lines for traffic across boundaries, named ports for the most-linked notes, and an honest gazetteer. Use when asked to see the shape of a vault, wiki, or documentation set, to find which regions are stale or disconnected, or to render any link graph as an instrument rather than a dashboard. Also use to redraw or extend an existing chart.
argument-hint: "[corpus path] [what you want to see]"
allowed-tools: Bash, Read, Write, Edit
user-invocable: true
---

# chart-corpus

An instrument for looking at a body of linked notes as a *shape*. Built for
Joseph's Obsidian vault, in the visual idiom he established for the Metamorphoses
and Liaozhai reading instruments. Not a dashboard: a chart plate.

```bash
/usr/bin/python3 ~/.agents/skills/chart-corpus/scripts/redraw.py \
  [--vault PATH] [--out PATH] [--graph FILE]
```

Runs in about five seconds under launchd (490 notes, October 2026); an interactive
run while Obsidian is busy can take one to three minutes, all of it in the REST
fetch. Output defaults to `90 Meta/Charts/carta-del-cervello.html`, **the published
chart**: always pass `--out` to a scratch path when testing. `--graph FILE` draws a
pre-built corpus (the porting contract below, as JSON) without touching Obsidian or
git. Runs weekly, unattended, Sunday 08:45
via `com.joseph.carta-redraw` (fifteen minutes ahead of `brain-steward` so the
steward reads a fresh chart).

## The encoding

| Chart element | Data |
|---|---|
| Territory arc length | notes in that PARA region |
| Wash (verdigris / ochre / grey) | median days since last commit |
| Vermilion tick, seaward side | orphaned share of the territory |
| Rhumb line weight | links crossing that border (drawn at ≥4) |
| Named port | a note with ≥30 backlinks |
| Landward hatching | portolan convention for a charted coast |

## Measure the property, not a proxy

This is the part that matters, and the part that was nearly got wrong.

**File mtimes cannot be trusted here.** On this machine they were rewritten in
bulk when the vault moved out of iCloud; right after the move every territory
read about two days old, a vault with no staleness anywhere: beautiful and
entirely false. They have since drifted back level with git (October 2026), but
they keep no record of their own and the next bulk copy would reset them again.
The plate's marginalia reports what they say on each redraw.

**Frontmatter dates cover most notes but not all** (97% in October 2026, up from
~85% in May), and coverage is uneven by region, which biases any comparison
between territories. The marginalia prints the current figure.

**Git history is used, and it is honest, but censored.** The repository was
recreated 2026-05-26, so nothing can be seen before then. Every silt figure is a
**floor, never a ceiling**, and the gazetteer caption says so. The `cen` column
counts notes sitting at that censor.

If you port this to another corpus, redo this check first. Three signals, keep the
honest one, and state the censoring in the artifact itself rather than in a commit
message nobody reads.

**Use the resolved link graph, never grep.** Obsidian's Local REST API returns
`links` and `backlinks` per note under
`Accept: application/vnd.olrapi.note+json`. Text-scraping for `...` misses
aliases and heading links and invents edges that do not resolve.

## Two traps in the automation

Both cost real time, both are silent.

1. **Encode directory paths when walking the REST API.** PARA folders contain
   spaces (`10 Projects/`). Without `urllib.parse.quote` the recursive listing
   silently returns only the root: 41 files instead of 382, with no error.

2. **TCC will hang, not fail.** A launchd agent reading `~/Documents` needs Full
   Disk Access. On this machine `/bin/zsh` has it and `/opt/homebrew/bin/python3`
   does not, and the Homebrew binary **hangs indefinitely** instead of raising
   `PermissionError`, so the job stalls with an empty log and a live PID. The
   working pattern, matching `brain-health` and `vault-autocommit`:

   ```
   ProgramArguments: /bin/zsh -c <wrapper.sh>
   wrapper calls /usr/bin/python3    # Apple's, not Homebrew's
   ```

   Keep `redraw.py` stdlib-only and **Python 3.9-compatible** so Apple's
   interpreter suffices: no `match`, no `X | None` hints, and no f-string that
   reuses its own quote inside `{}` (3.12+ only; one slipped in on 2026-10-05 and
   would have crashed the Sunday job). Check with `/usr/bin/python3 -m py_compile
   scripts/redraw.py`, not Homebrew's python. No in-process
   preflight can rescue this; a hang is not catchable.

3. Fan the note fetches out with a thread pool. One HTTPS round-trip per note is
   minutes sequentially and about four seconds at twelve workers.

## Porting it to another corpus

`redraw.py` assumes Obsidian plus git. To chart something else, replace
`fetch_graph()` and `git_last_touched()` and keep everything downstream. The
contract those two must satisfy:

```python
notes = { "path": {"links": [...], "backlinks": [...],
                   "mtime": unix_ts, "fm_date": bool} }   # last two optional
gitlast = { "path": unix_ts }          # last genuine touch
genesis = unix_ts | None               # where the record is censored (earliest commit)
```

Saved as JSON (`{"notes":…, "gitlast":…, "genesis":…}`), the same contract is what
`--graph` reads, so a ported corpus can be drawn without editing the script.
`mtime` and `fm_date` only feed the marginalia's comparison of the rejected clocks;
without them it says they were not measured.

`region()` decides territories; it currently splits on the top-level folder.
For a corpus without folders, cluster on tag or link community instead.

## The template

`scripts/template.html` is the chart with injection markers: `/*__DATA__*/`,
`__DATE__`, `__TOTAL__`, `__STANDFIRST_LEAD__`, `__MTIME_CLAUSE__`, `__DESC__`,
`__PORTS_MIN__`, `__PORTS_MAX__`, `__LANES_MIN__`, `__RECORD__`, `__FINDINGS__`,
`__INSTRUMENTS__`. Self-contained: no CDN, no build step, works under the Artifact
CSP. Themed for light and dark through CSS custom properties.

**The template holds no numbers and no findings.** Until October 2026 it did: a
centre caption, the SVG `<desc>`, "Eight territories", "Twenty-four rhumb lines" and
findings i–v were typed in on the first drawing and went stale the next Sunday,
while the plate around them redrew. Every sentence is now written by `redraw.py`
from the redraw's own numbers (`findings()`, `describe()`, `instruments()`), and the
template's script reads its thresholds and centre caption from `DATA.meta`. A
marker with no value raises instead of shipping a literal `__NAME__`. Keep it that
way: if a new sentence needs a number, compute it in Python.

**Labels near the rose.** Port labels run radially inward and long names reach the
hub. The wind rose is drawn first, the centre caption tries one, two or three lines
at several offsets and keeps the placement that costs labels least, then each port
slides along its own coast to clear both; only what still does not fit is set
smaller (floor 9px) and then shortened, with the full name kept as a tooltip. The
`viewBox` then grows to whatever was drawn, so no territory label is cut off.
Rhumb lines are drawn beneath all labels, and every chart `text` carries a
plate-coloured halo (`paint-order:stroke`, 3px), so a lane crossing a name breaks
behind each letter instead of striking through it, in both themes.

**Verify a redraw before trusting it:**

```bash
npx impeccable detect <out.html>          # design anti-patterns
~/.claude/skills/gstack/browse/dist/browse goto file://<out.html>
~/.claude/skills/gstack/browse/dist/browse js 'document.querySelectorAll("#chart *").length'
```

Expect one gazetteer row per territory and one `.rhumb` per lane in the script's
`[carta]` line, and a centre caption that repeats that line's numbers. To prove
nothing is hard-coded, also draw a small synthetic corpus with `--graph` and check
that no vault name or figure appears on it. Remaining `low-contrast` findings from
`impeccable` are analyzer artifacts: it cannot evaluate the dark-theme media query
and so pairs dark tokens against white. All eight real pairs measure ≥4.6:1.

## Related

`minimalist-skill` for the editorial register ·
`orchestrate` routed this build · the visual idiom comes from my Metamorphoses and Liaozhai reading instruments.
