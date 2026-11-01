#!/usr/bin/env python3
"""Read the California codes history by date. Needs Python 3.8 or later and git; run it inside the repository.

  python tools/ca.py on 2020-06-01 civ/1714.md                 the text in force on a date
  python tools/ca.py diff 2018-12-31 2024-12-31 civ/1714.md [--words]
  python tools/ca.py log civ/1714.md                           every change to a section, with its dates
  python tools/ca.py law "Stats. 2019, ch. 115"                everything one statute changed

A commit's date is its In-force: trailer, the day its change took effect. The first commit (the codes as in the
Legislative Counsel's database of 2016-11-27) and the documentation use their author date. The text of a section on
a date is the file in the last ordinary commit touching it dated on or before that date, unless a later correction
(Kind: correction, Corrects: <from>..<to>, Files: <path>) covers that date for that file: then the correction's text.
So the answer does not depend on your time zone or the time of day.
"""
import re
import subprocess
import sys


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", check=True).stdout


def history(path=None):
    fmt = ("%H%x1f%ad%x1f%an%x1f%s%x1f%(trailers:key=In-force,valueonly,separator=%x2c)%x1f"
           "%(trailers:key=Kind,valueonly,separator=%x2c)%x1f%(trailers:key=Corrects,valueonly,separator=%x2c)%x1e")
    out = []
    args = ["log", "--reverse", "--date=short", "--format=" + fmt, "HEAD"] + (["--", path] if path else [])
    for rec in git(*args).split("\x1e"):
        rec = rec.strip("\n")
        if rec:
            commit, authored, author, subject, in_force, kind, corrects = rec.split("\x1f")
            out.append({"day": (in_force.strip().split(",")[0] or authored), "commit": commit, "author": author,
                        "subject": subject, "kind": kind.strip(), "corrects": corrects.strip()})
    return out


def day(text):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        sys.exit("Dates are written YYYY-MM-DD, not %r." % text)
    return text


def covers(span, date):
    m = re.match(r"(\d{4}-\d{2}-\d{2})\s*\.\.\s*(\d{4}-\d{2}-\d{2})?", span)
    return bool(m) and m.group(1) <= date and (not m.group(2) or date <= m.group(2))


def text_on(date, path):
    hist = history(path)
    ordinary = [h for h in hist if h["kind"] != "correction" and h["day"] <= date]
    fixes = [h for h in hist if h["kind"] == "correction" and covers(h["corrects"], date)]
    pick = fixes[-1] if fixes else (ordinary[-1] if ordinary else None)
    if pick is None:
        return None
    try:
        return git("show", "%s:%s" % (pick["commit"], path))
    except subprocess.CalledProcessError:
        return None


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = argv[1]
    if cmd == "on" and len(argv) == 4:
        text = text_on(day(argv[2]), argv[3])
        if text is None:
            print("%s was not in the California codes on %s." % (argv[3], argv[2]))
            return 1
        sys.stdout.write(text)
        return 0
    if cmd == "log" and len(argv) == 3:
        for h in history(argv[2]):
            print("%s  %s  %s\n            %s" % (h["day"], h["commit"][:10], h["author"], h["subject"]))
        return 0
    if cmd == "diff" and len(argv) in (5, 6):
        import difflib
        a = (text_on(day(argv[2]), argv[4]) or "").splitlines(keepends=True)
        b = (text_on(day(argv[3]), argv[4]) or "").splitlines(keepends=True)
        sys.stdout.writelines(difflib.unified_diff(a, b, argv[2], argv[3]))
        return 0
    if cmd == "law" and len(argv) == 3:
        sys.stdout.write(git("log", "--stat", "--format=%n%ad  %s", "--date=short", "--author=^" + re.escape(argv[2]) + r"[ (]"))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
