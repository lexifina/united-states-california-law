"""The California history's phase 1 (which text each section has on each day), as a function, with the renderer
fixes found by queue verify job 458 (2026-11-05). runner wave lexifina-75.

    plan(snaps, fixes=False)  reproduces the published build (build/build.py at 8063f4e) exactly;
    plan(snaps, fixes=True)   applies the fixes below.

The fixes (each is in the published builder's version selection, not in the source):
  F1  A version's end ("Repealed as of", "Inoperative on") is read from the newest archive that still holds it.
      The published build kept the end from the first archive it saw, so a sunset the Legislature later moved
      (Probate Code 5606: 2021 -> 2032) still ended the section on the old day.
  F2  "Conditionally inoperative on <day>" is a condition, not an end: the database keeps the text in force, and the
      section is not ended on that day (Food and Agricultural Code 6047.10).
  F3  The database sometimes renumbers a version's row id between archives (Constitution art. I, sec. 2:
      CONSSEC. 2.1980772.I -> CONSSEC. 2.1978772.I). The published build saw a new version that ranked below the old
      one and an old one that had gone, and wrote a "not in the database" notice. A row whose id is new is taken as
      the same version as one of the section's versions that the previous archive held and this one does not, when
      both have the same start day and the same statute, history line or text.
  F4  When the version in force ends or leaves the database and every other live version ranks below it, the build
      wrote a "not in the database" notice. A live version that the database still held on or after that day is not
      a superseded one coming back: it is the text in force (Education Code 17406).
"""
from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_fixed as B  # noqa: E402  (the published builder, unchanged helpers)

HOST, _MD, _DATE = B.HOST, B._MD, B._DATE
Version, key_of, path_of, cite, law_of, cause_of, body = B.Version, B.key_of, B.path_of, B.cite, B.law_of, B.cause_of, B.body
sec_no, date_in = B.sec_no, B.date_in


def dates_of(row: dict, fixes: bool) -> tuple:
    if not fixes:
        return B.dates_of(row)
    hist = row["hist"]
    ops = date_in(r"(?<![Ii]n)(?<![Ii]n-)[Oo]perative (?:on )?" + _DATE, hist)
    eff = row["eff"] or next(iter(date_in(r"Effective " + _DATE, hist)), None)
    start = max(ops + ([eff] if eff else [])) if ops or eff else None
    if start is None and row["code"] == "CONS" or start is None and re.search(r"\bProp(?:osition|\.)", hist):
        start = next(iter(date_in(_DATE, hist)), None)
    # F2: a conditional inoperation is not an end.
    plain = re.sub(r"[Cc]onditionally [Ii]noperative[^.;]*", "", hist)
    ends = date_in(r"Repealed (?:as of|on) " + _DATE, plain) + date_in(r"[Ii]noperative (?:on |as of )?" + _DATE, plain)
    clauses = [m[0] for m in re.findall(r"((?:Repealed (?:as of|on)|[Ii]noperative(?: on| as of)?) " + _DATE + r"(?:,? by its own provisions)?)", plain)]
    return start, (min(ends) if ends else None), "; ".join(clauses)


def plan(snaps: list[dict], fixes: bool) -> dict:
    D = [s["snapshot"] for s in snaps]
    base, record_to = D[0], D[-1]
    codes = snaps[-1]["codes"]
    texts: dict[str, list[str]] = {}
    for s in snaps:
        texts.update(s["texts"])
    stats = Counter()

    versions: dict[tuple, dict[str, Version]] = defaultdict(dict)
    in_snap: list[dict[tuple, list[Version]]] = []
    chapter_eff: dict[tuple, Counter] = defaultdict(Counter)
    for k, s in enumerate(snaps):
        url = HOST + s["zip"]
        present: dict[tuple, list[Version]] = defaultdict(list)
        ids_now = {r["id"] for r in s["laws"]}
        for row in s["laws"]:
            key = key_of(row)
            v = versions[key].get(row["id"])
            if v is None and fixes and k > 0:
                # F3: a renumbered row id of a version the previous archive held.
                start, _, _ = dates_of(row, fixes)
                cause = cause_of(row)
                cands = [w for vid, w in versions[key].items() if w.db and w.seen and w.seen[-1] == k - 1
                         and vid not in ids_now and w.start == start
                         and (w.cause["id"] == cause["id"] or w.hist == row["hist"] or w.texts[-1][1] == row["h"])]
                if len(cands) == 1:
                    v = cands[0]
                    versions[key][row["id"]] = v
                    stats["renumbered_ids"] += 1
            if v is None:
                start, end, note = dates_of(row, fixes)
                cause = cause_of(row)
                v = Version(row["id"], key, start, cause, True, (row["opsec"] or "", row["id"]))
                v.end, v.end_note = end, note
                versions[key][row["id"]] = v
            elif fixes:
                # F1: the newest archive's history line says when the version ends.
                _, end, note = dates_of(row, fixes)
                if (end, note) != (v.end, v.end_note):
                    stats["ends_updated"] += 1
                v.end, v.end_note = end, note
            if row["eff"] and v.cause["id"][0] == "stats":
                chapter_eff[v.cause["id"]][row["eff"]] += 1
            if not v.texts or v.texts[-1][1] != row["h"]:
                v.texts.append((D[k], row["h"], url))
            v.hist = row["hist"]
            if not v.seen or v.seen[-1] != k:
                v.seen.append(k)
            if v not in present[key]:
                present[key].append(v)
        in_snap.append(present)
        stats[f"rows_{s['zip']}_{s['snapshot']}"] = len(s["laws"])
    # F3 aliases: one Version object under two ids; the rest of phase 1 sees each object once.
    for key in list(versions):
        uniq: dict[str, Version] = {}
        seen_ids = set()
        for vid, v in versions[key].items():
            if id(v) not in seen_ids:
                seen_ids.add(id(v))
                uniq[vid] = v
        versions[key] = uniq

    def heads_match(row_heads: dict, path: list) -> bool:
        return all((row_heads.get(t) or "").rstrip(".") == n.rstrip(".") for t, n in path)

    rows_by_code: list[dict[str, list[dict]]] = []
    for s in snaps:
        by = defaultdict(list)
        for row in s["laws"]:
            by[row["code"]].append(row)
        rows_by_code.append(by)

    repeals: dict[tuple, list] = defaultdict(list)
    bill_info: dict[tuple, dict] = {}
    ambiguous: list[str] = []
    for k, s in enumerate(snaps):
        url = HOST + s["zip"]
        for c in s["chaptered"]:
            law = law_of(c["year"], c["ex"], c["chapter"], c["bill"])
            cid = law["id"]
            if cid in bill_info:
                continue
            if chapter_eff.get(cid):
                day, basis = sorted(chapter_eff[cid].items(), key=lambda x: (-x[1], x[0]))[0][0], "the effective date the Legislative Counsel's database gives the sections this statute made"
            elif c["urgency"] == "Yes" or c["immediate"]:
                day = c["filed"] or c["chaptered"]
                basis = "Gov. Code sec. 9600(b): an urgency statute, or one the bill says takes effect immediately, goes into effect on enactment; dated by its filing with the Secretary of State"
            elif not c["ex"]:
                day = f"{int(c['chaptered'][:4]) + 1}-01-01"
                basis = "Gov. Code sec. 9600(a) (Cal. Const., art. IV, sec. 8(c)): a regular-session statute goes into effect on January 1 next following a 90-day period from its enactment"
            else:
                day, basis = None, ""
            bill_info[cid] = {"law": law, "day": day, "basis": basis, "title": c["title"], "zip": s["zip"],
                              "member": c["member"], "chaptered": c["chaptered"]}
            if not day or day <= base or day > record_to:
                continue
            frag_count = Counter()
            for a in c["acts"]:
                for n, h in a["frags"]:
                    frag_count[(a["code"], sec_no(n))] += 1
            for a in c["acts"]:
                code = a["code"]
                if a["action"] == "IS_REPEALED":
                    if a["target"]:
                        targets = [(code, sec_no(a["target"]))]
                    elif a["heads"]:
                        prior = max([i for i in range(len(D)) if D[i] < day] or [0])
                        targets = sorted({(code, sec_no(r["sec"])) for r in rows_by_code[prior].get(code, []) if heads_match(r["heads"], a["heads"])})
                    else:
                        targets = []
                    for t in targets:
                        repeals[t].append((day, {**law, "act": a["sec_no"], "how": "repealed"}))
                    continue
                if a["action"] == "IS_AMENDED_AND_RENUMBERED" and a["target"]:
                    old = (code, sec_no(a["target"]))
                    if all(sec_no(n) != old[1] for n, _ in a["frags"]):
                        repeals[old].append((day, {**law, "act": a["sec_no"], "how": "renumbered"}))
                for n, h in a["frags"]:
                    key = (code, sec_no(n))
                    if any(v.cause["id"] == cid for v in versions.get(key, {}).values()):
                        continue
                    if frag_count[key] > 1:
                        ambiguous.append(f"{law['law']} ({c['bill']}): {cite(key, codes)} appears {frag_count[key]} times (alternative versions); not used")
                        frag_count[key] = -100
                        continue
                    if frag_count[key] < 0:
                        continue
                    vid = f"bill:{c['bill_id']}:{a['sec_no']}:{n}"
                    v = Version(vid, key, day, {**law, "act": a["sec_no"]}, False, (a["sec_no"], vid))
                    v.texts.append((day, h, f"{url}#{c['member']}"))
                    v.hist = f"{a['sec_no'].title()} of {law['law']} ({c['bill']}), as chaptered"
                    v.source = basis
                    versions[key][vid] = v
                    stats["bill_versions"] += 1

    def first_after(day: str):
        return next((i for i in range(len(D)) if D[i] >= day), None)

    for key, vs in versions.items():
        for vid in [v for v in vs if not vs[v].db]:
            v = vs[vid]
            k = first_after(v.start)
            there = in_snap[k].get(key, []) if k is not None else None
            if k is None or there and not any((w.start or "") >= v.start for w in there):
                del vs[vid]
                stats["bill_versions_not_borne_out"] += 1
    for key, reps in repeals.items():
        for day, law in sorted(reps, key=lambda x: x[0]):
            k = first_after(day)
            if k is None:
                continue
            stale = [w for w in in_snap[k].get(key, []) if (w.start or "") < day and not (w.end and w.end <= day)]
            if stale:
                stats["repeals_not_borne_out"] += 1
                continue
            for v in versions.get(key, {}).values():
                if (v.start or "") < day and (v.removed is None or day < v.removed):
                    v.removed, v.removed_by = day, law
            stats["repeals"] += 1
    for key, vs in versions.items():
        for v in vs.values():
            if v.db and v.seen[-1] < len(D) - 1 and v.removed is None:
                v.removed = D[v.seen[-1] + 1]
                v.removed_by = None

    groups: dict[tuple, dict] = {}

    def add(day, kind, cause_id, cause, path, text, note, source):
        g = groups.setdefault((day, kind, cause_id), {"cause": cause, "files": {}, "notes": [], "sources": set()})
        g["files"][path] = text
        if note:
            g["notes"].append(note)
        if source:
            g["sources"].add(source)

    def rank(v):
        return (v.start or "", v.db, v.order)

    def chosen(vs, t, floor):
        live = [v for v in vs if (v.start is None or v.start <= t) and not (v.end and v.end <= t)
                and not (v.removed and v.removed <= t) and (floor is None or rank(v) >= floor)]
        if not live and fixes and floor is not None:
            # F4: below the floor, a version the database still held on or after t is not a superseded one coming
            # back (the database drops superseded versions): it is the text in force.
            live = [v for v in vs if (v.start is None or v.start <= t) and not (v.end and v.end <= t)
                    and not (v.removed and v.removed <= t) and v.db and any(D[i] >= t for i in v.seen)]
        return max(live, key=rank) if live else None

    def render(key, v, t):
        h, src = v.text_at(t)
        lines = [f"# {cite(key, codes)}", ""]
        for line in body(texts[h]):
            lines += [line, ""]
        lines.append(f"Source: {_MD.sub(r'\\\1', v.hist)}")
        return "\n".join(lines) + "\n", src

    def gone(key, note):
        return f"# {cite(key, codes)}\n\n{_MD.sub(r'\\\1', note)}\n"

    for key in sorted(versions, key=lambda k: path_of(k)):
        vs = list(versions[key].values())
        if not vs:
            continue
        path = path_of(key)
        points = {p for v in vs for p in (v.start, v.end, v.removed) if p} | {d for v in vs for d, _, _ in v.texts[1:]}
        points = sorted(p for p in points if base < p <= record_to)
        prev = chosen(vs, base, None)
        prev_text = None
        floor = None
        if prev is not None:
            prev_text, src = render(key, prev, base)
            add(base, 0, ("baseline",), {}, path, prev_text, "", src)
            floor = rank(prev)
        for t in points:
            cur = chosen(vs, t, floor)
            if cur is not None:
                text, src = render(key, cur, t)
                floor = max(floor, rank(cur)) if floor else rank(cur)
            else:
                text, src = None, (prev.text_at(t)[1] if prev is not None else "")
            if text == prev_text or (cur is None and prev is None):
                prev = cur
                continue
            name = cite(key, codes)
            if cur is not None and cur is not prev and cur.start == t:
                verb = "amended" if prev is not None else "added"
                add(t, 1, cur.cause["id"], cur.cause, path, text, f"{name} {verb}" + (f" ({cur.cause['act']})" if cur.cause.get("act") else ""), src)
                if not cur.db and cur.source:
                    groups[(t, 1, cur.cause["id"])]["basis"] = cur.source
            elif cur is not None and cur is prev:
                add(t, 3, ("database",), {}, path, text, f"{name}: text differs in the database of {t}", src)
            else:
                if prev is not None and prev.end == t:
                    clause = prev.end_note or f"Not in force from {t} by its own provisions"
                    note = text if cur is not None else gone(key, f"{clause}. Last version: {prev.hist}")
                    add(t, 2, prev.cause["id"], prev.cause, path, note, f"{name}: {clause}", src)
                elif prev is not None and prev.removed == t and prev.removed_by:
                    law = prev.removed_by
                    how = law["how"]
                    note = text if cur is not None else gone(key, f"{how.capitalize()} by {law['law']}" + (f" ({law['bill']})" if law['bill'] else "") + f", in force {t}. Last version: {prev.hist}")
                    add(t, 1, law["id"], law, path, note, f"{name} {how} ({law['act']})", src)
                    info = bill_info.get(law["id"])
                    if info:
                        groups[(t, 1, law["id"])].setdefault("basis", info["basis"])
                        groups[(t, 1, law["id"])]["sources"].add(f"{HOST}{info['zip']}#{info['member']}")
                else:
                    last = D[max(i for i in range(len(D)) if D[i] < t)]
                    note = text if cur is not None else gone(key, f"Not in the Legislative Counsel's database of {t} (it was in the database of {last}); the database and the chaptered statutes name no repealing law. Last version: {prev.hist if prev else ''}")
                    add(t, 3, ("database",), {}, path, note, f"{name}: no longer in the database of {t}" if cur is None else f"{name}: changed in the database of {t}", src)
            prev, prev_text = cur, text
    return {"groups": groups, "D": D, "codes": codes, "bill_info": bill_info, "ambiguous": ambiguous, "stats": stats,
            "versions": versions}


def sort_key(k: tuple) -> tuple:
    return (k[0], k[1], tuple(str(x).zfill(6) if isinstance(x, int) else str(x) for x in k[2]))


def timeline(groups: dict) -> dict[str, list[tuple[str, str | None]]]:
    """path -> [(day, text or None)] in commit order, exactly as the builder writes them."""
    out: dict[str, list] = defaultdict(list)
    for gk in sorted(groups, key=sort_key):
        for p, t in groups[gk]["files"].items():
            out[p].append((gk[0], t))
    return out


def text_on(tl: list, day: str):
    cur = None
    for d, t in tl:
        if d <= day:
            cur = t
    return cur
