"""Reads one of the Legislative Counsel's session archives (pubinfo_YYYY.zip) into the parts the history needs:

- every row of LAW_SECTION_TBL (one row per section version in the database when the archive was made), with the
  section text rendered from its .lob file (caml:Content XML);
- every chaptered statute in BILL_TBL / BILL_VERSION_TBL, with what each of its bill sections does to which code
  section (from the ActionLine's action and xlink:href) and the text of each section it adds or amends.

Each rendered text is checked character for character against every text node of its source (letters and digits, in
order); a mismatch is recorded, not hidden. Standard library only.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

CAML = "{http://lc.ca.gov/legalservices/schemas/caml.1#}"
XLINK = "{http://www.w3.org/1999/xlink}"
XHTML = "{http://www.w3.org/1999/xhtml}"
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_DATE = r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d{1,2}, \d{4})"


def iso(text: str) -> str:
    m = re.match(r"([A-Za-z]+)\.? (\d{1,2}), (\d{4})", text)
    return f"{int(m.group(3)):04d}-{MONTHS[m.group(1)[:3].lower()]:02d}-{int(m.group(2)):02d}"


# ---- The .dat tables ----

def rows(z: zipfile.ZipFile, table: str, width: int) -> list[list[str | None]]:
    """A table's rows: tab-separated, text fields in backticks, NULL for no value. A field may hold a newline, so
    records are joined until they have the table's width."""
    out, pending = [], ""
    for line in z.read(f"{table}.dat").decode("utf-8").split("\n"):
        pending = pending + "\n" + line if pending else line
        if pending.count("\t") + 1 < width:
            continue
        cells = pending.split("\t")
        pending = ""
        out.append([None if c == "NULL" else c[1:-1] if len(c) >= 2 and c[0] == c[-1] == "`" else c for c in cells])
    return out


# ---- Text ----

_WS = re.compile(r"\s+")
_SPACES = {"EnSpace", "EmSpace", "ThinSpace"}
BLOCK = {"p", "table", "div", "blockquote", "h1", "h2", "h3", "ul", "ol", "li"}


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _inline(e: ET.Element, out: list[str]) -> None:
    """Appends the text of an inline element and its children (not its tail)."""
    tag = local(e.tag)
    if tag == "span" and e.get("class") in _SPACES:
        out.append(" ")
    elif tag == "span" and e.get("class") == "SpacedLeaders":
        out.append(" ____ ")
    elif tag == "br":
        out.append(" ")
    elif tag == "TipIn":
        return
    elif tag == "Fraction":
        num = "".join(e.find(CAML + "Numerator").itertext()) if e.find(CAML + "Numerator") is not None else ""
        den = "".join(e.find(CAML + "Denominator").itertext()) if e.find(CAML + "Denominator") is not None else ""
        before = "".join(out).rstrip()
        out.append((" " if before[-1:].isdigit() else "") + f"{num}/{den}")
        return
    if e.text:
        out.append(e.text)
    for c in e:
        _inline(c, out)
        if c.tail:
            out.append(c.tail)


def _clean(text: str) -> str:
    return _WS.sub(" ", text.replace(" ", " ")).strip()


def _table(t: ET.Element) -> list[str]:
    """A table, one line per row and cells joined by " | ", walking only the table's own rows (a nested table is not
    walked twice) and its caption wherever it stands."""
    lines: list[str] = []
    for part in t:
        tag = local(part.tag)
        if tag == "caption":
            lines.append(_clean(" ".join(blocks(part))))
        elif tag in ("thead", "tbody", "tfoot"):
            for tr in part:
                if local(tr.tag) == "tr":
                    lines += _row(tr)
        elif tag == "tr":
            lines += _row(part)
    return [x for x in lines if x]


def _row(tr: ET.Element) -> list[str]:
    cells = [_clean(" ".join(blocks(c))) for c in tr if local(c.tag) in ("td", "th")]
    return [" | ".join(cells)] if any(cells) else []


def blocks(e: ET.Element) -> list[str]:
    """The paragraphs of a block container, in order: its own text and the tails between children are paragraphs
    too, so text standing directly inside a div or beside <p> children is kept."""
    lines: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        t = _clean("".join(buf))
        if t:
            lines.append(t)
        buf.clear()

    if e.text:
        buf.append(e.text)
    for c in e:
        tag = local(c.tag)
        if tag == "table":
            flush()
            lines += _table(c)
        elif tag == "TipIn":
            flush()
            lines.append(f"[Tip-in material, {c.get('numPages') or 'some'} page(s), printed with the statute and not in the source text]")
        elif tag in BLOCK and any(local(d.tag) in BLOCK for d in c.iter() if d is not c):
            flush()
            lines += blocks(c)
        elif tag in BLOCK:
            flush()
            part: list[str] = []
            _inline(c, part)
            t = _clean("".join(part))
            if t:
                lines.append(t)
        else:
            _inline(c, buf)
        if c.tail:
            buf.append(c.tail)
    flush()
    return lines


def _nodes(e: ET.Element, out: list[str]) -> None:
    if local(e.tag) == "TipIn":   # a printer's instruction ("PRINTER PLEASE NOTE: TIP-IN MATERIAL TO BE INSERTED")
        return
    if e.text:
        out.append(e.text)
    for c in e:
        _nodes(c, out)
        if c.tail:
            out.append(c.tail)


def source_chars(e: ET.Element) -> str:
    """Every letter and digit of every text node under e, in document order, except a TipIn's printer instruction."""
    out: list[str] = []
    _nodes(e, out)
    return "".join(ch for ch in "".join(out) if ch.isalnum())


def rendered_chars(lines: list[str]) -> str:
    return "".join(ch for ch in "".join(x for x in lines if not x.startswith("[Tip-in material")) if ch.isalnum())


def content_lines(xml: bytes | ET.Element) -> tuple[list[str], bool]:
    root = ET.fromstring(xml) if isinstance(xml, bytes) else xml
    lines = blocks(root)
    return lines, rendered_chars(lines) == source_chars(root)


def text_hash(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:20]


# ---- Law sections ----

_STATS = re.compile(r"Stats\. (\d{4}),(?: (\d)(?:st|nd|rd|th) Ex\. Sess\.,)? Ch\. (\d+)", re.I)


def law_sections(z: zipfile.ZipFile, texts: dict, bad: list) -> list[dict]:
    out = []
    for r in rows(z, "LAW_SECTION_TBL", 18):
        (rid, code, sec, statute, chapter, opsec, eff, vid, div, title, part, chap, art, hist, lob, active, _, upd) = r
        lines, ok = content_lines(z.read(lob)) if lob else ([], True)
        h = text_hash(lines)
        texts[h] = lines
        if not ok:
            bad.append(f"LAW_SECTION_TBL {rid} ({lob})")
        hist = _clean(hist or "")
        m = _STATS.search(hist)
        out.append({
            "id": rid, "code": code, "sec": sec, "statute": statute, "chapter": chapter, "opsec": opsec,
            "ex": (m.group(2) if m and m.group(1) == statute and m.group(3) == chapter else None),
            "eff": (eff or "")[:10] or None, "vid": vid, "hist": hist, "lob": lob, "active": active, "h": h,
            "heads": {"DIVISION": div, "TITLE": title, "PART": part, "CHAPTER": chap, "ARTICLE": art},
        })
    return out


# ---- Chaptered statutes ----

_HREF_SEC = re.compile(r"LawSection\[caml:Num='([^']+)'\]")
_HREF_HEAD = re.compile(r"LawHeading\[@type='(\w+)' and caml:Num='([^']+)'\]")


def chaptered(z: zipfile.ZipFile, texts: dict, bad: list) -> list[dict]:
    bills = rows(z, "BILL_TBL", 19)
    versions = {r[0]: r for r in rows(z, "BILL_VERSION_TBL", 18)}
    out = []
    for b in bills:
        (bill_id, session, sess_num, mtype, mnum, state, ch_year, ch_type, ch_sess, ch_num, latest, *_rest) = b
        if ch_type != "CHP" or not ch_num or latest not in versions:
            continue
        v = versions[latest]
        if v[4] != "Chaptered" or not v[14]:
            continue
        root = ET.fromstring(z.read(v[14]))
        dates = {}
        for a in root.iter(CAML + "Action"):
            t, d = a.find(CAML + "ActionText"), a.find(CAML + "ActionDate")
            if t is not None and d is not None:
                dates.setdefault(t.text, d.text)
        bill = root.find(CAML + "Bill")
        body = "".join(bill.itertext()) if bill is not None else ""
        acts = []
        for bs in (bill.iter(CAML + "BillSection") if bill is not None else []):
            num = bs.find(CAML + "Num")
            line = bs.find(CAML + "ActionLine")
            if line is None:
                continue
            href = urllib.parse.unquote(line.get(XLINK + "href") or "")
            m = re.match(r"urn:caml:codes:([A-Z]+):", href)
            if not m:
                continue
            target = _HREF_SEC.search(href)
            frags = []
            frag = bs.find(CAML + "Fragment")
            for ls in (frag.iter(CAML + "LawSection") if frag is not None else []):
                n = ls.find(CAML + "Num")
                c = ls.find(f"{CAML}LawSectionVersion/{CAML}Content")
                if n is None or c is None:
                    continue
                lines, ok = content_lines(c)
                if not ok:
                    bad.append(f"chaptered {bill_id} {''.join(num.itertext()) if num is not None else ''} {n.text}")
                h = text_hash(lines)
                texts[h] = lines
                frags.append([_clean(n.text or ""), h])
            acts.append({
                "sec_no": _clean("".join(num.itertext())) if num is not None else "",
                "action": line.get("action") or "", "code": m.group(1),
                "target": target.group(1) if target else None,
                "heads": _HREF_HEAD.findall(href) if not target else [],
                "line": _clean("".join(line.itertext())), "frags": frags,
            })
        out.append({
            "bill_id": bill_id, "bill": f"{mtype} {mnum}" + (f" ({int(sess_num)}st Ex. Sess.)" if sess_num not in (None, "0") else ""),
            "year": ch_year, "ex": ch_sess if ch_sess not in (None, "0") else None, "chapter": ch_num,
            "urgency": v[12], "chaptered": dates.get("CHAPTERED") or (v[3] or "")[:10], "filed": dates.get("FILED"),
            "immediate": bool(re.search(r"take effect immediately", body)), "member": v[14], "acts": acts,
            "title": _clean("".join(root.find(f"{CAML}Description/{CAML}Title").itertext())) if root.find(f"{CAML}Description/{CAML}Title") is not None else "",
        })
    return out


def parse(zpath: Path, out: Path) -> dict:
    """Parses one archive and writes the result, gzipped JSON, to out. Returns the result."""
    texts: dict[str, list[str]] = {}
    bad: list[str] = []
    with zipfile.ZipFile(zpath) as z:
        info = z.getinfo("LAW_SECTION_TBL.dat")
        snapshot = "%04d-%02d-%02d" % info.date_time[:3]
        codes = {r[0]: r[1] for r in rows(z, "CODES_TBL", 2)}
        laws = law_sections(z, texts, bad)
        stats = chaptered(z, texts, bad)
    data = {"zip": zpath.name, "snapshot": snapshot, "codes": codes, "laws": laws, "chaptered": stats, "texts": texts,
            "mismatches": bad}
    tmp = out.with_suffix(".part")
    tmp.write_bytes(gzip.compress(json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8"), compresslevel=5))
    tmp.replace(out)
    return data


def load(path: Path) -> dict:
    return json.loads(gzip.decompress(path.read_bytes()))
