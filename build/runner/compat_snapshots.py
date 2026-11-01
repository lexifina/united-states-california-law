"""Append correction snapshots compatible with the original California date reader.

Every snapshot starts from an unchanged published tree on its source date and
overlays source-version corrections. Law blobs are reused without alteration.
"""
from __future__ import annotations
import collections, datetime as dt, json, re, subprocess

LAW = re.compile(r'^[a-z]+/(?:art-[^/]+/)?[0-9][A-Za-z0-9.-]*\.md$')
DOC = re.compile(r'^(README[^/]*|REPRODUCE[^/]*|LICENSE[^/]*|NOTICE[^/]*|SOURCES[^/]*|CHANGES\.md|CHANGELOG[^/]*|corpus\.json|jurisdiction\.json|\.gitattributes|\.gitignore|data/.*|tools/.*|docs/.*|build/.*)$')
IDENTITY = 'Lexifina <state-law-history@lexifina.com>'
# The official AB 141 row resumes only after the conditions in Stats. 2022,
# Ch. 56, Sec. 41. No trigger/date evidence establishes that reversion here.
# Keep the unchanged published dated snapshot for this path at every boundary.
HELD_CORRECTION_PATHS = frozenset({'rtc/34011.md'})

def git(repo, *args, data=None, timeout=120):
    p=subprocess.run(['git',*args],cwd=repo,input=data,capture_output=True,timeout=timeout)
    if p.returncode: raise RuntimeError(p.stderr.decode('utf-8','replace'))
    return p.stdout

def entries(repo, rev):
    out={}
    for row in git(repo,'ls-tree','-r','-z',rev).split(b'\0'):
        if not row: continue
        header,path=row.split(b'\t',1);mode,kind,oid=header.split()
        out[path.decode()] = (mode.decode(),oid.decode())
    return out

def epoch(day):
    return int((dt.datetime.fromisoformat(day)-dt.datetime(1970,1,1)).total_seconds())+43200

def history(repo, rev):
    rows=[]
    for block in git(repo,'log','--reverse','--date=short','--format=%x1e%H%x1f%ad%x1f%B',rev).decode().split('\x1e')[1:]:
        sha,date,msg=block.strip('\r\n ').split('\x1f',2)
        ts=collections.defaultdict(list)
        for k,v in re.findall(r'^([A-Za-z-]+): (.*)$',msg,re.M): ts[k].append(v)
        rows.append({'sha':sha,'day':(ts['In-force'][0] if ts['In-force'] else date),'message':msg,'trailers':dict(ts)})
    return rows

def build(repo, base, input_head, source_day, helper_bytes, progress=print, ref_prefix='compatible'):
    published=history(repo,base)
    appended=history(repo,input_head)
    published_ids={r['sha'] for r in published}
    corrections=[];boundaries={source_day}
    baseline=entries(repo,base);inputs=entries(repo,input_head)
    # Legacy published section names need not start with a digit. Once a
    # namespace is established, every Markdown file in it is law text.
    code_dirs={p.split('/',1)[0] for p in baseline if LAW.match(p)}
    is_law=lambda p:p.split('/',1)[0] in code_dirs and p.endswith('.md')
    corpus=json.loads(git(repo,'show',input_head+':corpus.json'))
    first='2016-11-27'
    for row in published:
        if first<=row['day']<=source_day:boundaries.add(row['day'])
    for row in appended:
        if row['sha'] in published_ids:continue
        ts=row['trailers']
        if ts.get('Kind')!=['correction']:continue
        paths=[p for p in ts.get('Files',[]) if is_law(p) and p not in HELD_CORRECTION_PATHS]
        if not paths:continue
        start,end=ts['Corrects'][0].split('..',1)
        if first<=start<=source_day:boundaries.add(start)
        if end:
            after=(dt.date.fromisoformat(end)+dt.timedelta(days=1)).isoformat()
            if first<=after<=source_day:boundaries.add(after)
        specs=git(repo,'ls-tree','-r','-z',row['sha'],'--',*paths)
        blobs={}
        for line in specs.split(b'\0'):
            if not line:continue
            head,path=line.split(b'\t',1);mode,kind,oid=head.split();blobs[path.decode()]=(mode.decode(),oid.decode())
        corrections.append({'sha':row['sha'],'start':start,'end':end,'files':{p:blobs.get(p) for p in paths}})
    static={p:v for p,v in baseline.items() if not is_law(p)}
    helper_oid=git(repo,'hash-object','-w','--stdin',data=helper_bytes).decode().strip()
    # Every correction snapshot keeps the published non-law tree. Regenerated
    # documentation and shipped tools belong only to the closing docs commit.
    closing={p:v for p,v in inputs.items() if not is_law(p) and baseline.get(p)!=v}
    closing['build/runner/compat_snapshots.py']=('100644',helper_oid)
    closing.pop('build/tool.py',None)
    closing.pop('tools/ca.py',None)
    for p in list(closing):
        if p.startswith(('README','REPRODUCE','LICENSE','SOURCES')) and p in baseline:closing.pop(p)
    dates=sorted(boundaries)
    provisional=bytearray(); plans=[]
    def write_data(stream,value):stream.extend(f'data {len(value)}\n'.encode()+value+b'\n')
    for n,day in enumerate(dates,1):
        chosen=[r for r in published if r['day']<=day][-1]['sha']
        tree=git(repo,'rev-parse',chosen+'^{tree}').decode().strip()
        active={}
        for correction in corrections:
            if correction['start']<=day and (not correction['end'] or day<=correction['end']):active.update(correction['files'])
        when=epoch(source_day)
        provisional.extend(f'commit refs/heads/{ref_prefix}-provisional\nmark :{n}\nauthor {IDENTITY} {when} +0000\ncommitter {IDENTITY} {when} +0000\n'.encode())
        write_data(provisional,f'Snapshot {day}\n\nKind: correction\nIn-force: {day}\n'.encode())
        provisional.extend((f'from {base}\n' if n==1 else f'from :{n-1}\n').encode())
        # Empty path replaces the root tree; all law blobs remain immutable objects.
        provisional.extend(f'M 040000 {tree} ""\n'.encode())
        for path,(mode,oid) in static.items():provisional.extend(f'M {mode} {oid} {path}\n'.encode())
        for path,value in sorted(active.items()):
            if value is None:provisional.extend(f'D {path}\n'.encode())
            else:provisional.extend(f'M {value[0]} {value[1]} {path}\n'.encode())
        provisional.extend(b'\n')
        plans.append({'day':day,'published_snapshot':chosen,'active_correction_paths':len(active)})
        if n%100==0:progress(f'Planned {n}/{len(dates)} source-date snapshots',flush=True)
    provisional.extend(b'done\n')
    progress('Importing complete source-date snapshot trees',flush=True)
    git(repo,'-c','gc.auto=0','fast-import','--quiet','--done',data=bytes(provisional),timeout=600)
    provisional_head=git(repo,'rev-parse',f'refs/heads/{ref_prefix}-provisional').decode().strip()
    revisions=git(repo,'rev-list','--reverse',base+'..'+provisional_head).decode().splitlines()
    if len(revisions)!=len(plans):raise RuntimeError('Provisional snapshot count')
    final=bytearray();manifest=[];mark=0;previous=base
    for sha,plan in zip(revisions,plans):
        paths=git(repo,'diff-tree','--no-renames','--no-commit-id','--name-only','-r',previous,sha).decode().splitlines()
        if not paths:continue
        mark+=1;day=plan['day'];tree=git(repo,'rev-parse',sha+'^{tree}').decode().strip();when=epoch(source_day)
        message=(f'Correction: California source snapshot for {day}\n\n'
                 'Restates the complete dated snapshot using unchanged published law objects and the sourced version corrections. '
                 'The original date reader and the correction-aware reader therefore select the same source-version text.\n\n'
                 f'Kind: correction\nIn-force: {day}\nCorrects: {day}..\n'
                 'In-force-basis: this is the source-date boundary of the corrected snapshot, from the published statute/operative dates '
                 'or the source-dated archive boundary; it is not the retrieval day or a new enactment date\n'
                 'Reason: preserve complete dated snapshots for the original California reader while applying sourced version-selection corrections\n'
                 f'Published-snapshot: {plan["published_snapshot"]}\n'
                 +''.join(f'Files: {p}\n' for p in paths)
                 +'Source: https://downloads.leginfo.legislature.ca.gov/pubinfo_2015.zip\n'
                 +'Source: https://downloads.leginfo.legislature.ca.gov/pubinfo_2017.zip\n'
                 +'Source: https://downloads.leginfo.legislature.ca.gov/pubinfo_2025.zip\n')
        final.extend(f'commit refs/heads/{ref_prefix}\nmark :{mark}\nauthor {IDENTITY} {when} +0000\ncommitter {IDENTITY} {when} +0000\n'.encode())
        write_data(final,message.encode());final.extend((f'from {base}\n' if mark==1 else f'from :{mark-1}\n').encode())
        final.extend(f'M 040000 {tree} ""\n\n'.encode())
        manifest.append({**plan,'tree':tree,'files':paths});previous=sha
        if mark%100==0:progress(f'Checked transition paths for {mark} snapshots',flush=True)
    # One closing documentation commit, and no law change at its date.
    mark+=1;when=epoch(source_day)
    final.extend(f'commit refs/heads/{ref_prefix}\nmark :{mark}\nauthor {IDENTITY} {when} +0000\ncommitter {IDENTITY} {when} +0000\n'.encode())
    write_data(final,b'Documentation: California source record and reader\n\nKind: documentation\n')
    final.extend(f'from :{mark-1}\n'.encode())
    for p,(mode,oid) in closing.items():final.extend(f'M {mode} {oid} {p}\n'.encode())
    final.extend(b'\ndone\n');git(repo,'-c','gc.auto=0','fast-import','--quiet','--done',data=bytes(final),timeout=600)
    head=git(repo,'rev-parse',f'refs/heads/{ref_prefix}').decode().strip()
    git(repo,'symbolic-ref','HEAD',f'refs/heads/{ref_prefix}')
    return {'base':base,'input_head':input_head,'head':head,'snapshots':manifest,'commits':mark,'dates_planned':len(dates),'source_day':source_day,'closing_files':sorted(closing),'held_correction_paths':sorted(HELD_CORRECTION_PATHS)}
