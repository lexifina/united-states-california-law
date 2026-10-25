# How this history was made, and how to make it again

## Short version

```sh
git clone <this repository> && cd <it>/build
python fetch.py                      # download the six session archives (about 6.6 GB; resumable)
sha256sum -c DATA-SHA256SUMS         # (from cache/raw) the files are the ones this history was built from
python build.py parse                # read each archive (about 3 minutes each)
python build.py repo ../../rebuilt
git -C ../../rebuilt log --format=%H    # the same commits, with the same hashes
```

`build.py` expects `fetch.py`, `pubinfo.py`, `docs.py` and `tool.py` beside it and the archives in `cache/raw/`. The last
statute commit is `b924b631c0c45849bda473b4ff23c7d488919df7`. The current session's archive, `pubinfo_2025.zip`, is rebuilt by the Legislative
Counsel every week, so a fetch made later reads a newer file; the checksums say whether it differs.

## Sources

| What | Where | Size |
| --- | --- | --- |
| `pubinfo_2015.zip`: the 2015–16 session archive (LAW_SECTION_TBL as of 2016-11-27, and the session's chaptered statutes) | `https://downloads.leginfo.legislature.ca.gov/pubinfo_2015.zip` | 903,670,010 bytes, Last-Modified Mon, 28 Nov 2016 05:51:36 GMT |
| `pubinfo_2017.zip`: the 2017–18 session archive (LAW_SECTION_TBL as of 2018-11-25, and the session's chaptered statutes) | `https://downloads.leginfo.legislature.ca.gov/pubinfo_2017.zip` | 1,222,693,499 bytes, Last-Modified Mon, 26 Nov 2018 05:27:05 GMT |
| `pubinfo_2025.zip`: the 2025–26 session archive (LAW_SECTION_TBL as of 2026-10-25, and the session's chaptered statutes) | `https://downloads.leginfo.legislature.ca.gov/pubinfo_2025.zip` | 1,292,052,065 bytes, Last-Modified Mon, 26 Oct 2026 04:26:08 GMT |
| The load scripts, which name every column of every table | `https://downloads.leginfo.legislature.ca.gov/pubinfo_load.zip` | 15,662 bytes |
| The Readme (05/21/2021) | `https://downloads.leginfo.legislature.ca.gov/pubinfo_Readme.pdf` | 370,394 bytes |

**Requests.** About 40 in all, one at a time and at least a second apart, with the user agent
`Mozilla/5.0 (compatible; Lexifina state law history; +https://lexifina.com/watch)`: the robots.txt check, the directory
listing, `pubinfo_News.txt`, `pubinfo_Readme.txt`, the Readme and the load scripts; a HEAD request for each session
archive to read its size; about 12 HTTP Range requests reading the zip directories of `pubinfo_2017.zip` and
`pubinfo_2025.zip` and one table from the latter, to check the table list before downloading; and one GET per archive
(a dropped connection resumes with a Range request from the byte reached). Every file is kept in `cache/raw/` and listed
with its sha256 in `build/DATA-SHA256SUMS` (sha256 of that list: `8c153b83b6aa93f69ad2150e61c517d4331432742fef66b844f3407d500b4aac`).

**robots.txt, checked 28 October 2026.**

- `https://downloads.leginfo.legislature.ca.gov/robots.txt` answers `404 Not Found`: the host sets no rules.
- `https://leginfo.legislature.ca.gov/robots.txt` reads, in part:

  ```
  User-agent: *
  Disallow: /
  ...
  Crawl-Delay: 10
  ```

  That site disallows every agent. It was not fetched, not even its robots.txt, during this build (the lines above
  are from Lexifina's survey of state sources the same day), and no page of it is used.

## What the tables contain, and what they do not

Each archive is a MySQL dump of the Legislative Counsel's `capublic` database: one tab-delimited `.dat` file per table
(text fields in backticks, `NULL` for none) and one `.lob` file per large field.

- **LAW_SECTION_TBL**: one row per section version in the database when the archive was made. Columns: `ID` (code,
  section, statute year, chapter and bill section, run together), `LAW_CODE`, `SECTION_NUM`, `OP_STATUES` (statute
  year), `OP_CHAPTER`, `OP_SECTION` (the bill section), `EFFECTIVE_DATE`, `LAW_SECTION_VERSION_ID`, `DIVISION`, `TITLE`,
  `PART`, `CHAPTER`, `ARTICLE`, `HISTORY` (the history line, e.g. "Amended by Stats. 2019, Ch. 115, Sec. 1. (AB 1817)
  Effective January 1, 2020."), the content (`caml:Content` XML in a `.lob`), `ACTIVE_FLG`, `TRANS_UID`, `TRANS_UPDATE`.
  Every row in every archive read has `ACTIVE_FLG = Y`. **A section has more than one row only while a later version is
  waiting to become operative** (or, for the Constitution, the same section number in different articles). Superseded
  versions are dropped: the table is the law as it stood when the archive was made, plus pending versions. It has no
  history of earlier text. `EFFECTIVE_DATE` is empty for about 68,000 old rows; operative dates, sunset dates and
  "Inoperative" dates are only in the history line, as words.
- **LAW_TOC_TBL, LAW_TOC_SECTIONS_TBL, CODES_TBL**: the codes' headings and the order of sections; not used except
  CODES_TBL for the codes' names (the headings of divisions, parts and chapters are not in the section files).
- **BILL_TBL, BILL_VERSION_TBL** with the bill XML in `.lob` files: every bill of the session and every printed version.
  The chaptered version of each statute (`CHAPTER_TYPE = CHP`) gives, for each bill section, an `ActionLine` with an
  `action` (`IS_AMENDED`, `IS_ADDED`, `IS_REPEALED`, `IS_AMENDED_AND_RENUMBERED`) and an `xlink:href` naming the code and
  the section or heading, and a `Fragment` with the full text of each section it adds or amends, in the same
  `caml:Content` form as LAW_SECTION_TBL. It does not give the statute's effective date; `URGENCY` is a column of
  BILL_VERSION_TBL and the filing date is in the bill's `caml:History`.
- The daily files `pubinfo_daily_*.zip` hold "all current session data except Code tables" (Readme) and were not used.

## The build, step by step

1. `python fetch.py` downloaded the six archives into `cache/raw/` (Python 3.12.10).
2. `python build.py parse` read each archive into `cache/parsed/`: every LAW_SECTION_TBL row with its text, and every
   chaptered statute with its actions and fragments. Each text was rendered one paragraph per line and **checked
   character for character against every text node of its source** (letters and digits, in document order, including
   text standing directly in a container beside its paragraphs, table cells walked by their own rows only, and table
   captions). Mismatches, all reported:

- none

3. `python build.py repo <out>` worked out, for each section, the version in force on every date where anything could
   change, and wrote the history with `git fast-import` (git 2.46.0.windows.1): the database of 2016-11-27 as the starting point,
   then each statute's changes in date order, then the documentation commit, dated 2026-10-25.

`build/build.py` has git blob `c4d7c42e8777430cadd4c6a4fb4ac669c92c46fb`, the same file as `scripts/state-law/ca/build.py` in Lexifina's repository.
Figures from this build: bill_versions 3,022, bill_versions_not_borne_out 54, repeals 1,412, repeals_not_borne_out 159, rows_pubinfo_2015.zip 151,806, rows_pubinfo_2017.zip 153,879, rows_pubinfo_2025.zip 162,526.

## Decisions made by hand

1. **The starting point is the 2015–16 session archive**, made 2016-11-27: the law then in force. Every change in force
   from that day is a commit, so every version in force from 1 January 2017 onwards is here.
2. **A version's date** is its operative date if the history line gives one, otherwise `EFFECTIVE_DATE`, otherwise the
   "Effective" date in the history line. For the Constitution and for ballot measures without a date column, the first
   date in the history line (the election day) is used. A version stops on the earliest "Repealed as of" or
   "Inoperative" date in its history line.
3. **When two versions could be in force**, the one with the later start wins; on the same day, a version the database
   keeps beats one taken from a bill.
4. **A version taken from a chaptered bill** is used only if the next archive bears it out (it holds a version of that
   section at least as new, or none at all). A repeal found in a bill is used only if the next archive no longer holds
   an older version of the section.
5. **A statute's effective date** is the date the database gives the sections it made. If it made none that the
   database keeps: an urgency statute, or one whose text says it takes effect immediately, is dated by its filing
   with the Secretary of State; any other regular-session statute by January 1 after the year it was chaptered
   (Gov. Code § 9600). The commit says which in `Date-basis:`.
6. **A section missing from the next archive with no repeal found** gets a one-line note, dated by that archive.
7. **Text**: one line per `<p>`; table rows as cells joined by " | "; en and em spaces as spaces; a fraction as
   "1/4"; fill-in leaders as "____"; a `caml:TipIn` (pages of forms printed with the statute and not in the data, whose
   only text is a printer's instruction) as one bracketed line saying so. Designations (a), (1), (A), (i), (I) are
   indented two spaces per level; "(i)" and "(I)" are read as roman numerals or letters by what precedes them.
8. **The Constitution** is included (`cons/art-<article>/<section>.md`), credited to the ballot measure the history line
   names.

## What AI models did

This repository was built on 2026-10-28 in **Claude Code**, Anthropic's coding tool, by the model **Claude Opus 5.5**
(model id `claude-opus-5-5`), from a goal set by the user at Lexifina: to bring all US law into git, in the same form as
the US Code history. The model wrote `build/`, `tools/ca.py` and these documents, and ran the commands above. **No model
wrote or edited any legal text, and no model is called when the history is built.** Every word comes from the
Legislative Counsel's data; every date from its tables, history lines and chaptered bills, by the rules above. The work
has not been reviewed line by line by a lawyer.
