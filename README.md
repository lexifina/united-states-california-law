# Git for California law

The text of the 29 California Codes and the California Constitution, section by section, with a commit for each
statute on each day its changes took effect, from 2016-11-27 to 2026-10-25. **Each commit is a statute taking
effect.** Its author is the statute that made the change (for example `Stats. 2019, ch. 115 (AB 1817)`), and its date is
the day the change came into force.

So the questions a lawyer asks of a code section become commands:

| The question | The command |
| --- | --- |
| What did Civil Code § 1714 say on 1 June 2020? | `python tools/ca.py on 2020-06-01 civ/1714.md` |
| Which statute wrote each subdivision? | `git blame civ/1714.md` |
| What changed between two dates? | `python tools/ca.py diff 2018-12-31 2024-12-31 civ/1714.md --words` |
| Everything one statute changed | `python tools/ca.py law "Stats. 2019, ch. 115"` |
| Every change to a section, with its dates | `python tools/ca.py log civ/1714.md` |
| The most amended sections of a code | `git log --format= --name-only -- veh \| sort \| uniq -c \| sort -rn \| head` |

`tools/ca.py` needs only Python 3 and git, and picks the text by each commit's `In-force:` date, so the answer does not
depend on your time zone or the time of day.

## What is here

165,451 section files in 30 folders (one per code, `cons/` for the Constitution), to 2026-10-25.
There are 8,127 commits: 8,102 credited to a chaptered statute and 21 to a
ballot measure; the rest are the starting point, sections that ended by their own terms ("Repealed as of", "Inoperative"),
and differences between two of the Legislative Counsel's databases that no statute found explains.

Each section is one Markdown file, `<code>/<section>.md` (for example `civ/1714.md`, `pen/187.md`,
`cons/art-i/1.md`), with one paragraph or designated provision per line, indented by designation, so `git blame` names a
statute for every subdivision. Its last line is the section's history line from the Legislative Counsel's database, or,
for a version that only the chaptered bill preserves, the bill section it came from.

## How a change is dated and credited

The Legislative Counsel publishes its database as one archive per two-year session. Each archive has every code
section version in the database when it was made, with its effective date, the statute and chapter that made it, and
its history line. It does not keep superseded versions. This history combines six archives
(`pubinfo_2015.zip` of 2016-11-27, `pubinfo_2017.zip` of 2018-11-25, `pubinfo_2025.zip` of 2026-10-25) with the chaptered statutes in the same archives:

- **A version the database keeps** is dated by its operative date where the history line gives one ("Operative
  January 1, 2029, by its own provisions"), otherwise by its effective date, and credited to its statute.
- **A version that came and went between two archives** is taken from the chaptered statute that enacted it, which
  holds the full text of every section it adds or amends, dated by that statute's effective date (2,968 versions).
  The commit's `Date-basis:` trailer says how that date was found.
- **A repeal** is credited to the statute that repealed the section, by name or as part of a repealed chapter or article
  (1,412 found). A section that ends by its own terms is committed on that day, credited to the statute
  that set the term.
- **A section that is simply gone** from the next archive, with no repealing statute found, is a commit of its own,
  "Legislative Counsel database differs", dated by that archive.

Commits are dated at noon UTC.

## What it does not show

- **Before 2016-11-27.** The history starts from the database of that day; earlier versions are not here.
- **Future versions.** A version enacted to become operative after 2026-10-25 is not on `main`.
- **Uncodified law.** Statutes that do not amend a code (budget acts, uncodified sections) are not here.
- **Operative dates written only in the text.** A version taken from a chaptered bill is dated by the statute's
  effective date; if its own text defers its operation, the history does not know.
- **Alternative versions.** Where a statute holds two versions of one section (SEC. 1 and SEC. 1.5, one operative only if
  another bill is enacted) and the database keeps neither, neither is used (421 cases, listed in
  `build/NOT-USED.md`).
- **Annotations.** No notes of decisions, cross-references or editorial notes: statute text and the history line only.
- **Official status.** The Legislative Counsel's database is not the official publication of the statutes. For anything
  that matters, read the chaptered statute.

## How it is built

From the Legislative Counsel's bulk data at https://downloads.leginfo.legislature.ca.gov/, which has no robots.txt. The browsing site
leginfo.legislature.ca.gov disallows every agent and was not used. The build script is in [`build/`](build/), with the
checksums of every file it read; it makes no network requests when it writes the history, and the same files give the
same commits. [REPRODUCE.md](REPRODUCE.md) records the sources, the commands, the software and what AI models did and did
not do; [CHANGES.md](CHANGES.md) records how the history changes.

Lexifina keeps a history of the US Code in the same form, [lexifina/united-states-law](https://github.com/lexifina/united-states-law),
and both are part of [lexifina/global-law](https://github.com/lexifina/global-law).

Built by [Lexifina](https://lexifina.com). For corrections, open an issue.

## License

The California Codes and Constitution are in the public domain: Government Code § 10248.5 says the information the
Legislative Counsel makes available under § 10248 "is within the public domain and the State of California retains no
copyright or other proprietary interest in that information." The history, commit messages, tools and documentation are
released under [CC BY 4.0](LICENSE.md).
