"""Builds a git history of the California Codes (and the California Constitution) from the Legislative Counsel's
bulk data: each commit is one statute (or ballot measure) taking effect, authored by it, dated the day it took effect.

    python -I scripts/state-law/ca/fetch.py                       # download the session archives (resumable)
    python -I scripts/state-law/ca/build.py parse                 # read each archive into cache/parsed/
    python -I scripts/state-law/ca/build.py repo <out>            # write the history with git fast-import

Source: https://downloads.leginfo.legislature.ca.gov/ (no robots.txt: 404 on 2026-10-28). The browsing site
leginfo.legislature.ca.gov disallows every agent and is never contacted.

Each archive pubinfo_YYYY.zip holds LAW_SECTION_TBL as it stood when the archive was made: every section version
then in the database (in force, or enacted to become operative later), each with its effective date, the statute and
chapter that made it, and its history line. Superseded versions are not kept, so the history is assembled from six
archives (the 2015-16 session's, made 2016-11-27, as the starting point, then 2017, 2019, 2021, 2023 and 2025) and
from the chaptered statutes in the same archives, which give the text of every section a statute added or amended,
every section it repealed, and so the versions that came and went between two archives.

The repo step makes no network requests and is deterministic.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pubinfo  # noqa: E402

HOST = "https://downloads.leginfo.legislature.ca.gov/"
HERE = Path(__file__).resolve().parent
RAW = HERE / "cache" / "raw"
PARSED = HERE / "cache" / "parsed"
ARCHIVES = ["pubinfo_2015.zip", "pubinfo_2017.zip", "pubinfo_2019.zip", "pubinfo_2021.zip", "pubinfo_2023.zip",
            "pubinfo_2025.zip"]
COMMITTER = ("Lexifina", "state-law-history@lexifina.com")
_DATE = pubinfo._DATE
_MD = re.compile(r"([\\*_`<])")

# ---- Reading the archives ----


def cmd_parse(args: argparse.Namespace) -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    for name in args.archives or ARCHIVES:
        out = PARSED / name.replace(".zip", ".json.gz")
        if out.exists() and not args.force:
            print(f"{name}: parsed", flush=True)
            continue
        d = pubinfo.parse(RAW / name, out)
        print(f"{name}: snapshot {d['snapshot']}, {len(d['laws'])} section versions, {len(d['chaptered'])} chaptered "
              f"statutes, {len(d['mismatches'])} text mismatches", flush=True)


# ---- Sections, versions and their dates ----


def sec_no(s: str) -> str:
    s = re.sub(r"^(SECTION|SEC\.)\s*", "", s.strip(), flags=re.I)
    return s.rstrip(".").strip()


def key_of(row: dict) -> tuple:
    if row["code"] == "CONS":
        return ("CONS", row["id"].rsplit(".", 1)[1].strip(), sec_no(row["sec"]))
    return (row["code"], sec_no(row["sec"]))


def path_of(key: tuple) -> str:
    safe = lambda s: re.sub(r"[^A-Za-z0-9.-]+", "-", s).strip("-") or "x"
    if key[0] == "CONS":
        return f"cons/art-{safe(key[1].lower())}/{safe(key[2])}.md"
    return f"{key[0].lower()}/{safe(key[1])}.md"


def roman(s: str) -> int:
    vals = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}
    s = s.lower()
    total = 0
    for i, ch in enumerate(s):
        v = vals[ch]
        total += -v if i + 1 < len(s) and vals[s[i + 1]] > v else v
    return total


class Depth:
    """The designation depth of California's (a)(1)(A)(i)(I) scheme, deciding "(i)" and "(I)" by context."""

    def __init__(self) -> None:
        self.last: dict[int, str] = {}
        self.cur = 0

    def of(self, label: str) -> int:
        if label.isdigit():
            d = 2
        elif label.islower() or label.isupper():
            lower = label.islower()
            letter, rom = (1, 4) if lower else (3, 5)
            is_rom = re.fullmatch(r"[ivxlc]+" if lower else r"[IVXLC]+", label) is not None
            if not is_rom:
                d = letter
            elif len(label) > 1 and len(set(label)) > 1 or len(label) > 1 and label[0] not in "aA":
                d = rom
            else:
                prev_letter = self.last.get(letter)
                prev_rom = self.last.get(rom)
                follows_letter = prev_letter is not None and len(prev_letter) == 1 and ord(label[0]) == ord(prev_letter) + 1
                follows_rom = (prev_rom is not None and roman(label) == roman(prev_rom) + 1) or (roman(label) == 1 and self.cur >= rom - 1)
                if follows_rom and (self.cur >= rom - 1 or not follows_letter):
                    d = rom
                elif follows_letter:
                    d = letter
                else:
                    d = rom if self.cur >= rom - 1 else letter
        else:
            d = 1
        self.last[d] = label
        for k in list(self.last):
            if k > d:
                del self.last[k]
        self.cur = d
        return d


def body(lines: list[str]) -> list[str]:
    out = []
    depth = Depth()
    for line in lines:
        text = _MD.sub(r"\\\1", line)
        m = re.match(r"\(([a-z]{1,4}|[A-Z]{1,4}|\d{1,3})\)", line)
        if m:
            out.append("  " * (depth.of(m.group(1)) - 1) + "- " + text)
        else:
            text = re.sub(r"^([#>+-]|\d+\.)", r"\\\1", text)
            out.append(text)
    return out


def cite(key: tuple, codes: dict) -> str:
    if key[0] == "CONS":
        return f"California Constitution, article {key[1]}, § {key[2]}"
    name = re.sub(r"\s*-\s*[A-Z]+$", "", codes.get(key[0], key[0])).strip()
    return f"California {name} § {key[1]}"


def date_in(pattern: str, text: str) -> list[str]:
    return [pubinfo.iso(m) for m in re.findall(pattern, text)]


class Version:
    __slots__ = ("vid", "key", "start", "end", "end_note", "cause", "texts", "source", "seen", "db", "removed",
                 "removed_by", "order", "hist")

    def __init__(self, vid, key, start, cause, db, order):
        self.vid, self.key, self.start, self.cause, self.db, self.order = vid, key, start, cause, db, order
        self.end = None
        self.end_note = ""
        self.texts: list[tuple[str, str, str]] = []   # (snapshot date, text hash, source URL)
        self.seen: list[int] = []
        self.removed = None
        self.removed_by = None
        self.source = ""
        self.hist = ""

    def text_at(self, t: str) -> tuple[str, str]:
        chosen = self.texts[0]
        for item in self.texts:
            if item[0] <= t:
                chosen = item
        return chosen[1], chosen[2]


def law_of(year: str, ex: str | None, chapter: str, bill: str = "") -> dict:
    ex_part = f", {ex}{ {'1': 'st', '2': 'nd', '3': 'rd'}.get(ex, 'th') } Ex. Sess." if ex else ""
    name = f"Stats. {year}{ex_part}, ch. {int(chapter)}"
    email = f"ca-{year}-{'x' + ex + '-' if ex else ''}{int(chapter)}@statutes.invalid"
    return {"id": ("stats", year, ex or "", int(chapter)), "law": name, "bill": bill, "email": email}


def cause_of(row: dict) -> dict:
    hist = row["hist"]
    bill = re.search(r"\(([A-Z]{2,3}X?\d? \d+)\)", hist)
    if row["statute"] and row["chapter"] and row["chapter"].isdigit():
        return law_of(row["statute"], row["ex"], row["chapter"], bill.group(1) if bill else "")
    prop = re.search(r"\bProp(?:osition|\.)\s*(\d+[A-Z]?(?:-[a-z])?)", hist)
    if prop:
        year = row["statute"] or (re.search(r"\d{4}", hist).group(0) if re.search(r"\d{4}", hist) else "")
        return {"id": ("prop", year, prop.group(1)), "law": f"Prop. {prop.group(1)} ({year})", "bill": "",
                "email": f"ca-prop-{year}-{prop.group(1).lower()}@ballot.invalid"}
    return {"id": ("unnamed",), "law": "", "bill": "", "email": "unnamed@ca-statutes.invalid"}


def dates_of(row: dict) -> tuple[str | None, str | None, str]:
    """(in force from, in force until, the clauses that end it) for a database row."""
    hist = row["hist"]
    ops = date_in(r"(?<![Ii]n)(?<![Ii]n-)[Oo]perative (?:on )?" + _DATE, hist)
    eff = row["eff"] or next(iter(date_in(r"Effective " + _DATE, hist)), None)
    start = max(ops + ([eff] if eff else [])) if ops or eff else None
    if start is None and row["code"] == "CONS" or start is None and re.search(r"\bProp(?:osition|\.)", hist):
        start = next(iter(date_in(_DATE, hist)), None)
    ends = date_in(r"Repealed (?:as of|on) " + _DATE, hist) + date_in(r"[Ii]noperative (?:on |as of )?" + _DATE, hist)
    clauses = [m[0] for m in re.findall(r"((?:Repealed (?:as of|on)|[Ii]noperative(?: on| as of)?) " + _DATE + r"(?:,? by its own provisions)?)", hist)]
    return start, (min(ends) if ends else None), "; ".join(clauses)


# ---- Writing ----


def epoch(day: str) -> int:
    return int((datetime.strptime(day, "%Y-%m-%d") - datetime(1970, 1, 1)).total_seconds()) + 12 * 3600


def cmd_repo(args: argparse.Namespace) -> None:
    out = Path(args.out).resolve()
    if out.exists() and any(out.iterdir()) and not (out / ".git").exists():
        sys.exit(f"{out} is not empty and is not a git repository; refusing to overwrite it")
    archives = args.archives.split(",") if args.archives else ARCHIVES
    snaps = [pubinfo.load(PARSED / n.replace(".zip", ".json.gz")) for n in archives]
    D = [s["snapshot"] for s in snaps]
    base, record_to = D[0], D[-1]
    codes = snaps[-1]["codes"]
    texts: dict[str, list[str]] = {}
    for s in snaps:
        texts.update(s["texts"])
    stats = Counter()

    # Database versions, across the archives.
    versions: dict[tuple, dict[str, Version]] = defaultdict(dict)
    in_snap: list[dict[tuple, list[Version]]] = []
    chapter_eff: dict[tuple, Counter] = defaultdict(Counter)
    for k, s in enumerate(snaps):
        url = HOST + s["zip"]
        present: dict[tuple, list[Version]] = defaultdict(list)
        for row in s["laws"]:
            key = key_of(row)
            v = versions[key].get(row["id"])
            if v is None:
                start, end, note = dates_of(row)
                cause = cause_of(row)
                v = Version(row["id"], key, start, cause, True, (row["opsec"] or "", row["id"]))
                v.end, v.end_note = end, note
                versions[key][row["id"]] = v
            if row["eff"] and v.cause["id"][0] == "stats":
                chapter_eff[v.cause["id"]][row["eff"]] += 1
            if not v.texts or v.texts[-1][1] != row["h"]:
                v.texts.append((D[k], row["h"], url))
            v.hist = row["hist"]
            v.seen.append(k)
            present[key].append(v)
        in_snap.append(present)
        stats[f"rows_{s['zip']}"] = len(s["laws"])

    def heads_match(row_heads: dict, path: list) -> bool:
        return all((row_heads.get(t) or "").rstrip(".") == n.rstrip(".") for t, n in path)

    rows_by_code: list[dict[str, list[dict]]] = []
    for s in snaps:
        by = defaultdict(list)
        for row in s["laws"]:
            by[row["code"]].append(row)
        rows_by_code.append(by)

    # Chaptered statutes: their dates, the versions they made that no archive kept, and their repeals.
    repeals: dict[tuple, list[tuple[str, dict]]] = defaultdict(list)
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
                        continue   # the database keeps this statute's version: its text and dates are used
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

    # Which bill versions and repeals the next archive bears out.
    def first_after(day: str) -> int | None:
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
    # A database version that the next archive no longer holds, with no repeal found: removed on that archive's date.
    for key, vs in versions.items():
        for v in vs.values():
            if v.db and v.seen[-1] < len(D) - 1 and v.removed is None:
                v.removed = D[v.seen[-1] + 1]
                v.removed_by = None

    # Phase 1: per section, the text in force at each date where something can change.
    groups: dict[tuple, dict] = {}

    def add(day: str, kind: int, cause_id: tuple, cause: dict, path: str, text: str | None, note: str, source: str) -> None:
        g = groups.setdefault((day, kind, cause_id), {"cause": cause, "files": {}, "notes": [], "sources": set()})
        g["files"][path] = text
        if note:
            g["notes"].append(note)
        if source:
            g["sources"].add(source)

    def rank(v: Version) -> tuple:
        return (v.start or "", v.db, v.order)

    def chosen(vs: list[Version], t: str, floor: tuple | None) -> Version | None:
        """The version in force on t: the newest that has started and not ended, never one older than a version
        already in force (a superseded version does not come back when its successor ends)."""
        live = [v for v in vs if (v.start is None or v.start <= t) and not (v.end and v.end <= t)
                and not (v.removed and v.removed <= t) and (floor is None or rank(v) >= floor)]
        return max(live, key=rank) if live else None

    def render(key: tuple, v: Version, t: str) -> tuple[str, str]:
        h, src = v.text_at(t)
        lines = [f"# {cite(key, codes)}", ""]
        for line in body(texts[h]):
            lines += [line, ""]
        lines.append(f"Source: {_MD.sub(r'\\\1', v.hist)}")
        return "\n".join(lines) + "\n", src

    def gone(key: tuple, note: str) -> str:
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
                # The version in force before stops: by its own provisions, by a repeal found in a statute, or
                # unexplained (it is no longer in the next archive).
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

    # Phase 2: fast-import, in date order: the starting point, statutes, provisions ending, database differences.
    out.mkdir(parents=True, exist_ok=True)
    if not (out / ".git").exists():
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=out, check=True)
    fi = subprocess.Popen(["git", "fast-import", "--quiet", "--done", "--force"], cwd=out, stdin=subprocess.PIPE)
    w = fi.stdin
    mark = [0]

    def blob(b: bytes) -> None:
        w.write(b"data %d\n" % len(b)); w.write(b); w.write(b"\n")

    def commit(author: tuple[str, str], day: str, message: str, files: dict[str, str | None]) -> None:
        mark[0] += 1
        w.write(f"commit refs/heads/main\nmark :{mark[0]}\nauthor {author[0]} <{author[1]}> {epoch(day)} +0000\ncommitter {COMMITTER[0]} <{COMMITTER[1]}> {epoch(day)} +0000\n".encode())
        blob(message.encode("utf-8"))
        if mark[0] > 1:
            w.write(f"from :{mark[0] - 1}\n".encode())
        for p in sorted(files):
            if files[p] is None:
                w.write(f"D {p}\n".encode())
            else:
                w.write(f"M 100644 inline {p}\n".encode()); blob(files[p].encode("utf-8"))
        w.write(b"\n")

    def listed(notes: list[str]) -> str:
        names = [re.sub(r" (added|amended|repealed|renumbered).*$|:.*$", "", n) for n in notes]
        uniq = list(dict.fromkeys(n.replace("California ", "", 1) for n in names))
        return f"{', '.join(uniq[:2])}{f' and {len(uniq) - 2} more' if len(uniq) > 2 else ''}"

    def sort_key(k: tuple) -> tuple:
        return (k[0], k[1], tuple(str(x).zfill(6) if isinstance(x, int) else str(x) for x in k[2]))

    kinds = Counter()
    for gk in sorted(groups, key=sort_key):
        day, kind, cid = gk
        g = groups[gk]
        files, notes, sources = g["files"], sorted(g["notes"]), sorted(g["sources"])
        src_lines = "".join(f"Source: {s}\n" for s in sources)
        kinds[kind] += 1
        if kind == 0:
            commit((f"California Codes, Legislative Counsel database of {day}", f"database-{day}@ca-codes.invalid"), day,
                   f"The California Codes and Constitution as in the Legislative Counsel's database of {day}\n\n"
                   f"Every code section and constitutional section in force on {day}, from LAW_SECTION_TBL of {archives[0]}.\n\n"
                   f"Consolidated: {day}\nPrinted-in: {archives[0]} (Legislative Counsel bulk data)\nSource: {HOST}{archives[0]}\n", files)
            continue
        if kind == 3:
            commit(("California Codes, database differences", "database@ca-codes.invalid"), day,
                   f"Legislative Counsel database differs: {listed(notes)}\n\n"
                   f"The Legislative Counsel's database of {day} differs from the one before, and no statute found in the "
                   f"chaptered bills explains it; dated by that database.\n\n" + "\n".join(f"- {n}" for n in notes) +
                   f"\n\nIn-force: {day}\nDate-basis: the date of the database archive, not a date of enactment\n{src_lines}", files)
            continue
        c = g["cause"]
        law = c.get("law") or "Change with no statute named"
        label = law + (f" ({c['bill']})" if c.get("bill") else "")
        title = bill_info.get(cid, {}).get("title", "")
        if kind == 2:
            subject = f"{label}: provisions end by their own terms: {listed(notes)}"
        else:
            verbs = Counter(re.search(r" (added|amended|repealed|renumbered)", n).group(1) for n in notes if re.search(r" (added|amended|repealed|renumbered)", n))
            what = ", ".join(f"{v} {verbs[v]}" for v in ("amended", "added", "repealed", "renumbered") if verbs[v])
            subject = f"{label}: {listed(notes)}" + (f" ({what})" if what and len(notes) > 2 else "")
        trailers = [f"In-force: {day}"]
        if c.get("law"):
            trailers.append(f"Law: {label}")
        if g.get("basis"):
            trailers.append(f"Date-basis: {g['basis']}")
        msg = subject + "\n\n" + (f"{title}\n\n" if title and kind == 1 else "") + "\n".join(f"- {n}" for n in notes) + "\n\n" + "\n".join(trailers) + "\n" + src_lines
        commit((label or "Unnamed change", c.get("email") or "unnamed@ca-statutes.invalid"), day, msg, files)
    w.write(b"done\n"); w.close(); fi.wait()
    last = subprocess.run(["git", "rev-parse", "main"], cwd=out, capture_output=True, text=True).stdout.strip()

    # The documentation commit, dated the day the record runs to.
    from docs import CHANGES, LICENSE, corpus, readme, reproduce

    files_now = subprocess.run(["git", "ls-tree", "-r", "--name-only", "main"], cwd=out, capture_output=True, text=True, encoding="utf-8").stdout.split("\n")
    files_now = [f for f in files_now if f]
    log = subprocess.run(["git", "log", "--format=%ae", "main"], cwd=out, capture_output=True, text=True).stdout.split()
    sums = {}
    for n in archives + ["pubinfo_load.zip", "pubinfo_Readme.pdf"]:
        meta = RAW / (n + ".meta")
        if meta.exists():
            u, size, modified, h = meta.read_text(encoding="utf-8").strip().split("\t")
            sums[u] = (h, size, modified)
        else:
            sums[HOST + n] = (hashlib.sha256((RAW / n).read_bytes()).hexdigest(), str((RAW / n).stat().st_size), "")
    manifest = "".join(f"{h}  {u}\n" for u, (h, _, _) in sorted(sums.items()))
    mismatches = [m for s in snaps for m in s["mismatches"]]
    info = {
        "codes": len({f.split("/")[0] for f in files_now}), "sections": len(files_now), "record_to": record_to,
        "base": base, "snapshots": list(zip(archives, D)), "commits": len(log) + 1,
        "statute_commits": sum(1 for e in log if e.endswith("@statutes.invalid")),
        "ballot_commits": sum(1 for e in log if e.endswith("@ballot.invalid")),
        "database_commits": kinds[3], "ending_commits": kinds[2], "stats": dict(stats), "ambiguous": len(ambiguous),
        "mismatches": mismatches, "sizes": {u: int(s) for u, (_, s, _) in sums.items()},
        "modified": {u: m for u, (_, _, m) in sums.items()},
        "data_sha256": hashlib.sha256(manifest.encode()).hexdigest(), "last": last, "python": sys.version.split()[0],
        "git": subprocess.run(["git", "--version"], capture_output=True, text=True).stdout.split()[-1],
        "blob": subprocess.run(["git", "hash-object", str(Path(__file__).resolve())], capture_output=True, text=True).stdout.strip(),
        "built": "2026-10-28",
    }
    fi = subprocess.Popen(["git", "fast-import", "--quiet", "--done", "--force"], cwd=out, stdin=subprocess.PIPE)
    w = fi.stdin
    docs = {
        "README.md": readme(info), "REPRODUCE.md": reproduce(info), "CHANGES.md": CHANGES, "LICENSE.md": LICENSE,
        "corpus.json": corpus(info), "tools/ca.py": (HERE / "tool.py").read_text(encoding="utf-8"),
        "build/build.py": Path(__file__).read_text(encoding="utf-8"), "build/pubinfo.py": (HERE / "pubinfo.py").read_text(encoding="utf-8"),
        "build/fetch.py": (HERE / "fetch.py").read_text(encoding="utf-8"), "build/docs.py": (HERE / "docs.py").read_text(encoding="utf-8"),
        "build/tool.py": (HERE / "tool.py").read_text(encoding="utf-8"), "build/DATA-SHA256SUMS": manifest,
        "build/NOT-USED.md": "# Alternative versions not used\n\nA statute that holds two or more versions of the same section (for example SEC. 1 and SEC. 1.5, the second operative only if another bill is also enacted) gives no way to tell from the bulk data which took effect, unless the database keeps it. These were left out:\n\n" + "".join(f"- {a}\n" for a in ambiguous),
    }
    w.write(f"commit refs/heads/main\nmark :1\nauthor {COMMITTER[0]} <{COMMITTER[1]}> {epoch(record_to)} +0000\ncommitter {COMMITTER[0]} <{COMMITTER[1]}> {epoch(record_to)} +0000\n".encode())
    blob(b"Documentation, the tool, and the build with the checksums of its data\n")
    w.write(f"from {last}\n".encode())
    for p in sorted(docs):
        w.write(f"M 100644 inline {p}\n".encode()); blob(docs[p].encode("utf-8"))
    w.write(b"\ndone\n"); w.close(); fi.wait()
    subprocess.run(["git", "checkout", "-q", "-f", "main"], cwd=out, check=True)
    count = subprocess.run(["git", "rev-list", "--count", "main"], cwd=out, capture_output=True, text=True).stdout.strip()
    print(f"{count} commits, {len(files_now)} files, {base} to {record_to} -> {out}")
    print(dict(stats), dict(kinds), f"ambiguous {len(ambiguous)}", f"mismatches {len(mismatches)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("parse"); p.add_argument("archives", nargs="*"); p.add_argument("--force", action="store_true")
    r = sub.add_parser("repo"); r.add_argument("out"); r.add_argument("--archives", help="comma-separated, for a trial build")
    a = ap.parse_args()
    {"parse": cmd_parse, "repo": cmd_repo}[a.cmd](a)


if __name__ == "__main__":
    main()
