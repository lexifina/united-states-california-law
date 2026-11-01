"""Independent gate (the reviewer's): every current section of the newest official archive, read straight from its
XML with the walker of queue verify job 458 (gpt-workers/jobs/q-458/verify_job.py, copied unchanged in substance),
compared word for word with a git revision of the history. Never uses the builder's renderer.

    python -X utf8 verify_gate.py <repo> <rev> <pubinfo zip> <out.json>

'conditional' marks a section whose choice the source leaves open: a conditional inoperation whose trigger is not
verified, or two or more versions in force in the database at once (alternative/prevailing versions, owner review).
"""
from __future__ import annotations

import collections
import datetime
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

DATEPAT = r'((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d{1,2}, \d{4})'


def day(s):
    s = re.sub(r'\b([A-Za-z]{3})[A-Za-z]*\.?', r'\1', s)
    return datetime.datetime.strptime(s, '%b %d, %Y').strftime('%Y-%m-%d')


def dates(hist, eff, code):
    starts = []
    if eff and re.match(r'\d{4}-\d{2}-\d{2}', eff):
        starts.append(eff[:10])
    for x in re.findall(r'(?<![Ii]n)(?<![Ii]n-)[Oo]perative (?:on )?' + DATEPAT, hist):
        starts.append(day(x))
    if not starts:
        starts = [day(x) for x in re.findall(r'Effective ' + DATEPAT, hist)]
    if not starts and code == 'CONS':
        starts = [day(x) for x in re.findall(DATEPAT, hist)]
    ends = [day(x) for x in re.findall(r'(?:Repealed (?:as of|on) |[Ii]noperative (?:(?:on|as of) )?)' + DATEPAT, hist)]
    return max(starts) if starts else None, min(ends) if ends else None


def secpath(code, sec, rid):
    sec = re.sub(r'^(SECTION|SEC\.)\s*', '', sec.strip(), flags=re.I).rstrip('.').strip()
    safe = lambda s: re.sub(r'[^A-Za-z0-9.-]+', '-', s).strip('-') or 'x'  # noqa: E731
    if code == 'CONS':
        return 'cons/art-' + safe(rid.rsplit('.', 1)[1].strip().lower()) + '/' + safe(sec) + '.md'
    return code.lower() + '/' + safe(sec) + '.md'


def local(tag):
    return tag.rsplit('}', 1)[-1]


BLOCKS = {'p', 'div', 'table', 'tr', 'td', 'th', 'li', 'h1', 'h2', 'h3', 'blockquote'}


def source_text(e):
    tag = local(e.tag)
    if tag == 'TipIn':
        return ''
    if tag == 'br' or (tag == 'span' and e.get('class') in {'EnSpace', 'EmSpace', 'ThinSpace'}):
        return ' '
    if tag == 'span' and e.get('class') == 'SpacedLeaders':
        return ' '
    if tag == 'Fraction':
        parts = {local(c.tag): ''.join(c.itertext()) for c in e}
        return parts.get('Numerator', '') + '/' + parts.get('Denominator', '')
    t = e.text or ''
    for c in e:
        if local(c.tag) in BLOCKS:
            t += ' '
        if local(c.tag) == 'Fraction' and t.rstrip()[-1:].isdigit():
            t += ' '
        t += source_text(c)
        if local(c.tag) in BLOCKS:
            t += ' '
        t += c.tail or ''
    return t


def words(t):
    return [x for x in re.findall(r'\w+', t) if x.strip('_')]


def published_words(md: str):
    text = md.split('\n', 2)[2].rsplit('\nSource:', 1)[0] if md.count('\n') >= 2 else ''
    text = re.sub(r'\[Tip-in material[^\]]*\]', '', text)
    text = re.sub(r'\\([\\`*_{}\[\]()#+.!>~-])', r'\1', text)
    return words(text)


def selected_sections(z: zipfile.ZipFile):
    snapshot = '%04d-%02d-%02d' % z.getinfo('LAW_SECTION_TBL.dat').date_time[:3]
    table = z.read('LAW_SECTION_TBL.dat').decode('utf-8')
    rows, pending = [], ''
    for line in table.split('\n'):
        pending = pending + '\n' + line if pending else line
        if pending.count('\t') + 1 < 18:
            continue
        cells = pending.split('\t')
        pending = ''
        rows.append([None if x == 'NULL' else x[1:-1] if x.startswith('`') and x.endswith('`') else x for x in cells])
    selected, uncertain, excl, eligible = {}, set(), collections.Counter(), collections.Counter()
    for r in rows:
        rid, code, sec, stat, ch, op, eff, vid, div, title, part, chap, art, hist, lob, active, uid, upd = r
        hist = hist or ''
        start, end = dates(hist, eff, code)
        if end and end <= snapshot and re.search(r'conditionally inoperative', hist, re.I):
            uncertain.add(secpath(code, sec, rid))
            end = None
        if start and start > snapshot:
            excl['future'] += 1
            continue
        if end and end <= snapshot:
            excl['ended'] += 1
            continue
        if not lob:
            excl['no text'] += 1
            continue
        path = secpath(code, sec, rid)
        eligible[path] += 1
        cand = {'path': path, 'hist': hist, 'lob': lob, 'start': start}
        if path not in selected or (start or '') > (selected[path]['start'] or ''):
            selected[path] = cand
    return snapshot, selected, uncertain | {p for p, n in eligible.items() if n > 1}, dict(excl)


def blobs(repo: Path, rev: str, paths: list[str]) -> dict[str, str]:
    tree = set(subprocess.run(['git', 'ls-tree', '-r', '--name-only', rev], cwd=repo, capture_output=True, text=True,
                              encoding='utf-8', check=True).stdout.split('\n'))
    want = [p for p in paths if p in tree]
    p = subprocess.run(['git', 'cat-file', '--batch'], cwd=repo, input=''.join(f'{rev}:{n}\n' for n in want).encode(),
                       capture_output=True, check=True)
    out, buf, i = {}, p.stdout, 0
    for n in want:
        nl = buf.index(b'\n', i)
        size = int(buf[i:nl].split()[2])
        out[n] = buf[nl + 1:nl + 1 + size].decode('utf-8')
        i = nl + 1 + size + 1
    return out


def gate(repo: Path, rev: str, zpath: Path) -> dict:
    with zipfile.ZipFile(zpath) as z:
        snapshot, sel, uncertain, excl = selected_sections(z)
        pub = blobs(repo, rev, sorted(sel))
        diff, missing = [], []
        for path, r in sorted(sel.items()):
            if path not in pub:
                missing.append(path)
                continue
            a = words(source_text(ET.fromstring(z.read(r['lob']))))
            b = published_words(pub[path])
            if a != b:
                i = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
                diff.append({'path': path, 'conditional': path in uncertain, 'start': r['start'], 'hist': r['hist'],
                             'source': ' '.join(a[max(0, i - 8):i + 16]), 'published': ' '.join(b[max(0, i - 8):i + 16])})
    return {'snapshot': snapshot, 'rev': rev, 'selected': len(sel), 'present': len(pub), 'missing': missing,
            'differ': diff, 'differ_unconditional': [d for d in diff if not d['conditional']], 'excluded': excl}


if __name__ == '__main__':
    repo, rev, zp, outp = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
    res = gate(repo, rev, zp)
    outp.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in res.items()}), flush=True)
