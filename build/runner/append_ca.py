"""Append, by fast-forward, to a scratch clone of the published California history (pubinfo-runner, lexifina-75):

1. labelled corrections (Kind: correction, Corrects: <from>..<to>, Files:) wherever the fixed renderer (plan.py,
   fixes F1-F3) gives a section a different text than the published build did, on any day up to the published
   record_to; one commit per corrected day range;
2. ordinary commits for every change after record_to found in the newer archive(s), written exactly as the published
   builder writes them (statute, ending, database-difference commits, dated by In-force);
3. one documentation commit: corrections-aware tools/ca.py, corpus.json record_to/checked_on, CHANGES.md, the
   runner's code under build/runner/.

    python -X utf8 append_ca.py <analysis.pkl> <published clone> <scratch dir> <retrieved YYYY-MM-DD>

Never pushes. Deterministic for the same inputs. Prints the new head.
"""
from __future__ import annotations

import datetime as dt
import json
import pickle
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import plan as PL  # noqa: E402
import build_fixed as B  # noqa: E402

COMMITTER = ("Lexifina", "state-law-history@lexifina.com")
CORR_AUTHOR = ("California Codes, corrections", "corrections@ca-codes.invalid")
HOST = B.HOST
REASON = ("the published build chose the wrong version of these sections on these days; the fixed version selection "
          "(build/runner/RUNNER-FIXES.md: F1 a version ends on the day the newest archive gives, F2 a conditional "
          "inoperation is not an end, F3 a database row renumbered between archives is the same version) gives the "
          "text the Legislative Counsel's database holds")


def run(*args, cwd, inp: bytes | None = None) -> str:
    p = subprocess.run(list(args), cwd=cwd, input=inp, capture_output=True)
    if p.returncode:
        raise RuntimeError(f"{args[:3]}: {p.stderr.decode(errors='replace')[-500:]}")
    return p.stdout.decode("utf-8", errors="replace")


def day_before(d: str) -> str:
    return (dt.date.fromisoformat(d) - dt.timedelta(days=1)).isoformat()


def correction_blocks(tl0: list, tl2: list, R0: str) -> list[tuple[str, str | None, str | None]]:
    """[(from, to or None (open), corrected text or None (no file))] for the days up to R0 where the two differ."""
    days = sorted({d for d, _ in tl0 if d <= R0} | {d for d, _ in tl2 if d <= R0})
    out = []
    for i, d in enumerate(days):
        a, b = PL.text_on(tl0, d), PL.text_on(tl2, d)
        if a == b:
            continue
        to = day_before(days[i + 1]) if i + 1 < len(days) else None
        if out and out[-1][2] == b and out[-1][1] is not None and out[-1][1] == day_before(d):
            out[-1] = (out[-1][0], to, b)   # contiguous, same corrected text
        else:
            out.append((d, to, b))
    return out


def main() -> None:
    pkl, pub, scratch, retrieved = Path(sys.argv[1]), Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve(), sys.argv[4]
    a = pickle.load(pkl.open("rb"))
    R0, R1, tl0, tl2 = a["R0"], a["R1"], a["tl0"], a["tl2"]
    codes, bill_info = a["codes"], a["bill_info"]
    base = run("git", "rev-parse", "HEAD", cwd=pub).strip()
    if not (scratch / ".git").exists():
        run("git", "clone", "-q", "--no-checkout", str(pub), str(scratch), cwd=pub.parent)
    run("git", "checkout", "-q", "-B", "runner", base, cwd=scratch)

    # 1. corrections, grouped by day range
    by_range: dict[tuple, dict[str, str | None]] = defaultdict(dict)
    for p in sorted(set(tl0) | set(tl2)):
        for f, t, text in correction_blocks(tl0.get(p, []), tl2.get(p, []), R0):
            by_range[(f, t or "")][p] = text
    stream = bytearray()
    mark = [0]

    def blob(b: bytes) -> None:
        stream.extend(b"data %d\n" % len(b)); stream.extend(b); stream.extend(b"\n")

    def commit(author, day, message, files, at=None):
        mark[0] += 1
        when = B.epoch(at or day)
        stream.extend(f"commit refs/heads/runner\nmark :{mark[0]}\nauthor {author[0]} <{author[1]}> {when} +0000\n"
                      f"committer {COMMITTER[0]} <{COMMITTER[1]}> {when} +0000\n".encode())
        blob(message.encode("utf-8"))
        stream.extend((f"from {base}\n" if mark[0] == 1 else f"from :{mark[0] - 1}\n").encode())
        for p in sorted(files):
            if files[p] is None:
                stream.extend(f"D {p}\n".encode())
            else:
                stream.extend(f"M 100644 inline {p}\n".encode()); blob(re.sub(r"(?m)^\[Tip-in material,[^\n]*\]\n(?:\n)?", "", files[p]).encode("utf-8"))
        stream.extend(b"\n")

    def name_of(p: str) -> str:
        m = re.match(r"cons/art-([^/]+)/(.+)\.md$", p)
        if m:
            return f"Constitution art. {m.group(1).upper()} § {m.group(2)}"
        code, sec = p.split("/", 1)
        return f"{re.sub(r'\s*-\s*[A-Z]+$', '', codes.get(code.upper(), code.upper())).strip()} § {sec[:-3]}"

    ncorr = 0
    for (f, t) in sorted(by_range):
        files = by_range[(f, t)]
        names = [name_of(p) for p in sorted(files)]
        listed = ", ".join(names[:2]) + (f" and {len(names) - 2} more" if len(names) > 2 else "")
        span = f"{f}..{t}" if t else f"{f}.."
        msg = (f"Correction: {listed}, text in force {('from ' + f + ' to ' + t) if t else 'from ' + f}\n\n"
               f"The published history showed the wrong text for these sections on these days; this commit gives the "
               f"text the fixed version selection reads from the same Legislative Counsel archives. Nothing earlier is "
               f"rewritten.\n\n" + "".join(f"- {n}\n" for n in names) + "\n"
               f"Kind: correction\nIn-force: {f}\nCorrects: {span}\nReason: {REASON}\n"
               + "".join(f"Files: {p}\n" for p in sorted(files))
               + f"Source: {HOST}pubinfo_2015.zip\nSource: {HOST}pubinfo_2017.zip\nSource: {HOST}pubinfo_2025.zip\n")
        commit(CORR_AUTHOR, f, msg, files, at=R1)
        ncorr += 1

    # 1b. re-apply: a closed correction leaves its text at the tip; restore the text in force at R0 (CORPUS-STANDARD
    #     3.2 rule 7), credited to the published commit that last wrote that file.
    tip: dict[str, str | None] = {}
    for (f, t) in sorted(by_range):
        for p_, text in by_range[(f, t)].items():
            tip[p_] = text
    restore = {p_: PL.text_on(tl2.get(p_, []), R0) for p_, text in tip.items()
               if text != PL.text_on(tl2.get(p_, []), R0)}
    writers: dict[str, str] = {}
    if restore:
        out = run("git", "log", "--format=%x1e%H", "--name-only", base, "--", *sorted(restore), cwd=pub)
        for rec in out.split("\x1e"):
            lines = [x for x in rec.strip().split("\n") if x]
            if lines:
                for fpath in lines[1:]:
                    writers.setdefault(fpath, lines[0])
    by_writer: dict[str, dict] = defaultdict(dict)
    for p_, text in restore.items():
        by_writer[writers.get(p_, "")][p_] = text
    meta = {}
    for w in by_writer:
        if w:
            info = run("git", "log", "-1", "--format=%an%x1f%ae%x1f%(trailers:key=In-force,valueonly)%x1f%s%x1f%ad",
                       "--date=short", w, cwd=pub).strip().split("\x1f")
            law = re.search(r"^Law: (.*)$", run("git", "log", "-1", "--format=%B", w, cwd=pub), re.M)
            meta[w] = {"an": info[0], "ae": info[1], "inf": info[2].strip() or info[4], "subj": info[3],
                       "law": law.group(1) if law else None}
    nre = 0
    for w in sorted(by_writer, key=lambda c: (meta[c]["inf"] if c else "", c)):
        files = by_writer[w]
        names = [name_of(p_) for p_ in sorted(files)]
        listed = ", ".join(names[:2]) + (f" and {len(names) - 2} more" if len(names) > 2 else "")
        if w:
            m = meta[w]
            msg = (f"Re-apply: {listed}, as in force from {m['inf']}\n\nThe corrections before this commit restate "
                   f"earlier days; this commit puts back the text in force today, first written by {w[:12]} "
                   f"({m['subj'][:80]}).\n\n" + "".join(f"- {n}\n" for n in names) + f"\nKind: law\nIn-force: {m['inf']}\n"
                   + (f"Law: {m['law']}\n" if m["law"] else "") + f"Re-applies: {w}\n")
            commit((m["an"], m["ae"]), m["inf"], msg, files, at=R1)
        else:
            msg = (f"Re-apply: {listed}\n\nThe corrections before this commit restate earlier days; these sections are "
                   f"not in the law in force today.\n\n" + "".join(f"- {n}\n" for n in names)
                   + f"\nKind: correction\nIn-force: {R0}\nCorrects: {R0}..\nReason: {REASON}\n"
                   + "".join(f"Files: {p_}\n" for p_ in sorted(files)))
            commit(CORR_AUTHOR, R0, msg, files, at=R1)
        nre += 1

    # 2. ordinary commits after R0, as the builder writes them
    groups = a["groups_new"]
    kinds = Counter()

    def listed_notes(notes):
        names = [re.sub(r" (added|amended|repealed|renumbered).*$|:.*$", "", n) for n in notes]
        uniq = list(dict.fromkeys(n.replace("California ", "", 1) for n in names))
        return f"{', '.join(uniq[:2])}{f' and {len(uniq) - 2} more' if len(uniq) > 2 else ''}"

    for gk in sorted(groups, key=PL.sort_key):
        day, kind, cid = gk
        g = groups[gk]
        files, notes, sources = g["files"], sorted(g["notes"]), sorted(g["sources"])
        src_lines = "".join(f"Source: {s}\n" for s in sources)
        kinds[kind] += 1
        if kind == 3:
            commit(("California Codes, database differences", "database@ca-codes.invalid"), day,
                   f"Legislative Counsel database differs: {listed_notes(notes)}\n\n"
                   f"The Legislative Counsel's database of {day} differs from the one before, and no statute found in the "
                   f"chaptered bills explains it; dated by that database.\n\n" + "\n".join(f"- {n}" for n in notes) +
                   f"\n\nIn-force: {day}\nDate-basis: the date of the database archive, not a date of enactment\n{src_lines}", files)
            continue
        c = g["cause"]
        law = c.get("law") or "Change with no statute named"
        label = law + (f" ({c['bill']})" if c.get("bill") else "")
        title = bill_info.get(cid, {}).get("title", "")
        if kind == 2:
            subject = f"{label}: provisions end by their own terms: {listed_notes(notes)}"
        else:
            verbs = Counter(re.search(r" (added|amended|repealed|renumbered)", n).group(1) for n in notes if re.search(r" (added|amended|repealed|renumbered)", n))
            what = ", ".join(f"{v} {verbs[v]}" for v in ("amended", "added", "repealed", "renumbered") if verbs[v])
            subject = f"{label}: {listed_notes(notes)}" + (f" ({what})" if what and len(notes) > 2 else "")
        trailers = [f"In-force: {day}"]
        if c.get("law"):
            trailers.append(f"Law: {label}")
        if g.get("basis"):
            trailers.append(f"Date-basis: {g['basis']}")
        msg = subject + "\n\n" + (f"{title}\n\n" if title and kind == 1 else "") + "\n".join(f"- {n}" for n in notes) + "\n\n" + "\n".join(trailers) + "\n" + src_lines
        commit((label or "Unnamed change", c.get("email") or "unnamed@ca-statutes.invalid"), day, msg, files)

    # 3. documentation
    corpus = json.loads(run("git", "show", f"{base}:corpus.json", cwd=pub))
    corpus["record_to"] = R1
    corpus["checked_on"] = retrieved
    corpus["record_note"] = (f"The 29 California Codes and the Constitution from the Legislative Counsel's database of 2016-11-27 "
                             f"to that of {R1}, via its session archives; built 2026-10-28, extended and corrected by "
                             f"pubinfo-runner on {retrieved}.")
    changes = run("git", "show", f"{base}:CHANGES.md", cwd=pub)
    entry = (f"## {dt.date.fromisoformat(retrieved).strftime('%-d %B %Y') if sys.platform != 'win32' else dt.date.fromisoformat(retrieved).strftime('%#d %B %Y')}\n\n"
             f"Extended to the Legislative Counsel's database of {R1} ({sum(kinds.values())} commits) and corrected: "
             f"{ncorr} correction commits (Kind: correction, with Corrects: and Files:) for sections whose published "
             f"text was wrong on some days because the build kept a version's first-seen end date, read a conditional "
             f"inoperation as an end, or lost a version whose database row was renumbered. Nothing published was "
             f"rewritten. See build/runner/RUNNER-FIXES.md.\n\n")
    changes = changes.replace("## ", entry + "## ", 1)
    docs = {
        "corpus.json": json.dumps(corpus, indent=2, ensure_ascii=True) + "\n",
        "CHANGES.md": changes,
        "tools/ca.py": (HERE / "tool_ca.py").read_text(encoding="utf-8"),
        "build/tool.py": (HERE / "tool_ca.py").read_text(encoding="utf-8"),
        "build/runner/plan.py": (HERE / "plan.py").read_text(encoding="utf-8"),
        "build/runner/append_ca.py": (HERE / "append_ca.py").read_text(encoding="utf-8"),
        "build/runner/verify_gate.py": (HERE / "verify_gate.py").read_text(encoding="utf-8"),
        "build/runner/RUNNER-FIXES.md": (HERE / "RUNNER-FIXES.md").read_text(encoding="utf-8"),
    }
    commit(COMMITTER, R1, "Documentation, the tool, and the runner's fixed build\n\nKind: documentation\n", docs)
    stream.extend(b"done\n")
    run("git", "fast-import", "--quiet", "--done", cwd=scratch, inp=bytes(stream))
    head = run("git", "rev-parse", "refs/heads/runner", cwd=scratch).strip()
    run("git", "checkout", "-q", "-f", "runner", cwd=scratch)
    print(json.dumps({"base": base, "head": head, "corrections": ncorr, "reapplied": nre, "ordinary": dict(kinds),
                      "corrected_files": sum(len(v) for v in by_range.values()), "record_to": R1}))


if __name__ == "__main__":
    main()
