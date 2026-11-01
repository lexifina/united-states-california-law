# Renderer fixes (pubinfo-runner, 2026-11-05)

Queue verify job 458 compared every current section of the Legislative Counsel's archive of 2026-11-01 with this
history's head and found 943 sections whose words differ and 112 absent. The source was right; the build's version
selection was wrong. `plan.py` is the build's phase 1 with three fixes; with the fixes off it reproduces the published
build exactly (every file at 2026-10-25 equal).

- **F1. A version ends on the day the newest archive gives.** The build read a version's "Repealed as of" or
  "Inoperative on" day from the first archive that held it. When the Legislature later moved the sunset (Probate
  Code § 5606: 2021 to 2032), the section still ended on the old day.
- **F2. "Conditionally inoperative on <day>" is not an end.** The condition is set elsewhere; the database keeps
  the text in force, and so does this history (Food and Agricultural Code § 6047.10).
- **F3. A renumbered database row is the same version.** The database sometimes changes a row id between archives
  (Constitution art. I § 2: `CONSSEC. 2.1980772.I` to `CONSSEC. 2.1978772.I`). A new id with the same start day and
  statute (or history line, or text) as a version the previous archive held, and this one does not, is that version.
- **F4. A version the database still holds is not a superseded one.** When the version in force ended or left the
  database and every other live version ranked below it, the build wrote a "not in the database" notice. A live
  version that the database held on or after that day is the text in force (Education Code § 17406).

A correction for days that end before the record's last day leaves its text at the tip of the branch, so each
such file is then restored by a re-apply commit (`Kind: law`, `Re-applies: <commit>`, credited to the statute of
the commit that made the current text, with its `In-force`), as CORPUS-STANDARD 3.2 rule 7 requires: the head
always shows the text in force today.

The corrections are appended as `Kind: correction` commits with `Corrects: <from>..<to>` and one `Files:` line per
section; nothing published is rewritten. `verify_gate.py` is the independent check (job 458's XML walker, not this
renderer) run on every runner head before review.
