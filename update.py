#!/usr/bin/env python3
"""AlbumWall v3: editable CSV library, on-demand sync, portable local artwork."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import hashlib
import io
import json
import math
import msvcrt
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import uuid
from urllib.parse import urlparse

import requests
from PIL import Image, ImageChops, ImageOps, ImageStat

ROOT = Path(__file__).resolve().parent
LOCAL = ROOT / '.local'
COVERS = ROOT / 'Covers'
FIELDS = ['id','album','artist','edition','year','sources','platform_ids','cover','cover_url',
          'excluded','notes','width','height','sha256','phash','download_url']
IMPORT_FIELDS = ['album','artist','edition','year','platform_id','cover_url','source_groups']
COS = [[math.cos((2*x+1)*u*math.pi/64) for x in range(32)] for u in range(8)]

def canon(s):
    return ''.join(c for c in unicodedata.normalize('NFKC',str(s or '')).casefold() if c.isalnum())

def json_value(s, default):
    try: return json.loads(s) if s else default
    except (TypeError,ValueError): return default

def truth(s): return str(s).strip().casefold() in {'1','true','yes','y','是','排除'}

def read_csv(path):
    if not path.exists(): return []
    data=path.read_bytes()
    for enc in ('utf-8-sig','utf-16','gb18030'):
        try: text=data.decode(enc); break
        except UnicodeError: continue
    else: raise ValueError('Unsupported CSV encoding')
    try: dialect=csv.Sniffer().sniff(text[:8192],delimiters=',;\t')
    except csv.Error: dialect=csv.excel
    return list(csv.DictReader(io.StringIO(text),dialect=dialect))

def write_csv(path,rows,fields):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp')
    with temp.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore')
        writer.writeheader(); writer.writerows(rows)
    temp.replace(path)

def atomic_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(path)

def load_json(path,default):
    try: return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:return default
    except ValueError:raise ValueError('Invalid local JSON: '+path.name)

def safe_path(relative):
    p=(ROOT/relative).resolve()
    if not p.is_relative_to(ROOT):raise ValueError('Cover path must stay inside project')
    return p

def phash(im):
    data=list(im.convert('L').resize((32,32),Image.Resampling.LANCZOS).get_flattened_data())
    horizontal=[[sum(data[y*32+x]*COS[u][x] for x in range(32)) for u in range(8)] for y in range(32)]
    coeff=[sum(horizontal[y][u]*COS[v][y] for y in range(32)) for v in range(8) for u in range(8)]
    median=sorted(coeff[1:])[31]
    bits=0
    for v in coeff:bits=(bits<<1)|int(v>median)
    return f'{bits:016x}'

def inspect_image(path):
    raw=path.read_bytes()
    with Image.open(io.BytesIO(raw)) as im:
        im.verify()
    with Image.open(io.BytesIO(raw)) as im:
        if im.width*im.height>40_000_000:raise ValueError('Image dimensions too large')
        w,h=im.size
        perceptual=phash(im)
        fmt=im.format
    ext={'JPEG':'jpg','PNG':'png','WEBP':'webp'}.get(fmt)
    if not ext:raise ValueError('Unsupported image type')
    return {'width':w,'height':h,'sha256':hashlib.sha256(raw).hexdigest(),'phash':perceptual,'ext':ext}

def metadata_key(row):
    return (canon(row.get('album')),canon(row.get('artist')),canon(row.get('edition')))

def can_merge(a,b):
    if not all(metadata_key(a)[:2]) or metadata_key(a)!=metadata_key(b):return False
    if a.get('year') and b.get('year') and a['year']!=b['year']:return False
    ids_a=json_value(a.get('platform_ids'),{});ids_b=json_value(b.get('platform_ids'),{})
    # Two distinct release IDs on the same platform are not presumed identical.
    for platform in ids_a.keys() & ids_b.keys():
        if ids_a[platform] and ids_b[platform] and not set(ids_a[platform])&set(ids_b[platform]):return False
    return True

def field(row,*names):
    lookup={re.sub(r'[\s_-]','',str(k)).casefold():str(v or '').strip() for k,v in row.items() if k}
    for name in names:
        value=lookup.get(re.sub(r'[\s_-]','',name).casefold(),'')
        if value:return value
    return ''

def merge_imports(rows,report):
    sources={};added=matched=skipped=0
    for name,platform in [('YouTubeMusic.csv','YTM'),('QQMusic.csv','QQ'),('AppleMusic.csv','Apple')]:
        path=ROOT/'Imports'/name
        try:imports=read_csv(path)
        except Exception as e:
            report['warnings'].append(name+': '+type(e).__name__);continue
        for raw in imports:
            album=field(raw,'album','专辑','专辑名','albumname')
            artist=field(raw,'album_artist','albumartist','artist','artists','歌手','艺人')
            if not album or not artist:skipped+=1;continue
            pid=field(raw,'platform_id','album_id','albummid','browseid')
            incoming={'album':album,'artist':artist,'edition':field(raw,'edition','发行版本'),
                      'year':field(raw,'year','发行年份'),'platform_ids':json.dumps({platform:[pid]} if pid else {})}
            target=None
            if pid:
                targets=[r for r in rows if pid in json_value(r.get('platform_ids'),{}).get(platform,[])]
                if len(targets)>1:
                    report['warnings'].append('Duplicate platform ID in editable library: '+platform+':'+pid)
                    skipped+=1;continue
                if targets:target=targets[0]
            if target is None:
                targets=[r for r in rows if can_merge(r,incoming)]
                if len(targets)==1:target=targets[0]
            if target is None:
                target={f:'' for f in FIELDS};target.update(incoming)
                target['id']=uuid.uuid4().hex[:16];target['excluded']='0';rows.append(target);added+=1
            else:matched+=1
            # Existing editorial text, cover override, exclusion, and notes always win.
            for key in ('album','artist','edition','year'):
                if not target.get(key):target[key]=incoming[key]
            ids=json_value(target.get('platform_ids'),{})
            if pid:ids[platform]=list(dict.fromkeys(ids.get(platform,[])+[pid]))
            target['platform_ids']=json.dumps(ids,ensure_ascii=False,separators=(',',':'))
            target['sources']=';'.join(sorted(set(filter(None,re.split(r'[;,]',target.get('sources',''))))|{platform}))
            url=field(raw,'cover_url','artworkurl','封面地址')
            if url.startswith('https://'):sources.setdefault(target['id'],[]).append(url)
    report['imports']={'new_albums':added,'matched_rows':matched,'skipped_no_album_or_conflict':skipped}
    return sources

def best_thumb(item):
    ts=[x for x in item.get('thumbnails',[]) if x.get('url','').startswith('https://')]
    return max(ts,key=lambda x:min(x.get('width',0),x.get('height',0)),default={})

def sync_youtube(report):
    auth=LOCAL/'browser.json'
    if not auth.exists():
        report['warnings'].append('YouTube authentication missing; existing CSV retained.');return
    from ytmusicapi import YTMusic
    client=YTMusic(str(auth));records={};counts={};complete=True;unclassified=set()
    cache=load_json(LOCAL/'yt_album_art.json',{})
    def add(item,group,is_album=False):
        album=item if is_album else (item.get('album') or {})
        aid=album.get('browseId') if is_album else album.get('id') or album.get('browseId')
        title=album.get('title') if is_album else album.get('name') or album.get('title')
        if not aid or not title:
            unclassified.add(item.get('videoId') or str(item.get('title')));return
        artist=', '.join(x.get('name','') for x in item.get('artists',[]) if x.get('name'))
        thumb=best_thumb(item)
        key=str(aid)
        if key not in records:
            records[key]={'album':title,'artist':artist,'edition':'','year':str(item.get('year') or '') if is_album else '',
                          'platform_id':key,'cover_url':thumb.get('url',''),'source_groups':group,'_size':min(thumb.get('width',0),thumb.get('height',0))}
        else:
            records[key]['source_groups']=';'.join(sorted(set(records[key]['source_groups'].split(';'))|{group}))
    try:
        albums=client.get_library_albums(limit=10000);counts['albums']=len(albums)
        complete=complete and len(albums)<10000
        for x in albums:add(x,'albums',True)
        liked=client.get_liked_songs(limit=10000).get('tracks',[]);counts['liked_songs']=len(liked)
        complete=complete and len(liked)<10000
        for x in liked:add(x,'liked_songs')
        playlists=client.get_library_playlists(limit=None);counts['playlists']=len(playlists);counts['playlist_tracks']=0
        for index,p in enumerate(playlists,1):
            print(f'YouTube playlists {index}/{len(playlists)}',flush=True)
            try:tracks=client.get_playlist(p['playlistId'],limit=10000).get('tracks',[])
            except Exception:
                complete=False;continue
            complete=complete and len(tracks)<10000;counts['playlist_tracks']+=len(tracks)
            for x in tracks:add(x,'playlist:'+p['playlistId'])
    except Exception as e:
        complete=False;report['warnings'].append('YouTube collection failed: '+type(e).__name__)
    report['youtube']={**counts,'complete':complete,'album_ids':len(records),'without_album_metadata':len(unclassified)}
    if not complete:
        report['warnings'].append('Incomplete YouTube snapshot; previous Imports/YouTubeMusic.csv retained.');return
    for index,(aid,row) in enumerate(records.items(),1):
        entry=cache.get(aid,{})
        thumb=entry.get('thumbnail')
        if thumb and time.time()-entry.get('time',0)<30*86400:
            if thumb[0]>row['_size']:row['cover_url']=thumb[2]
        elif row['_size']<500:
            try:
                detail=client.get_album(aid);t=best_thumb(detail)
                if t:
                    thumb=[min(t.get('width',0),t.get('height',0)),t.get('width',0)*t.get('height',0),t['url']]
                    cache[aid]={'thumbnail':thumb,'time':time.time()};row['cover_url']=t['url']
                if detail.get('artists'):row['artist']=', '.join(x['name'] for x in detail['artists'])
            except Exception:report['warnings'].append('Album detail unavailable: '+aid)
            if index%10==0:time.sleep(.3)
    atomic_json(LOCAL/'yt_album_art.json',cache)
    write_csv(ROOT/'Imports'/'YouTubeMusic.csv',list(records.values()),IMPORT_FIELDS)

def candidates(url):
    if not url.startswith('https://'):return []
    host=urlparse(url).hostname or ''
    out=[]
    if host.endswith(('googleusercontent.com','ggpht.com')) and re.search(r'=w\d+-h\d+',url):
        out.append(re.sub(r'=w\d+-h\d+','=w1200-h1200',url))
    if host.endswith('y.qq.com') and re.search(r'T002R\d+x\d+',url):
        out.append(re.sub(r'T002R\d+x\d+','T002R1200x1200',url))
    if host.endswith('mzstatic.com'):
        out.append(re.sub(r'/\d+x\d+[^/]*\.(jpg|png|webp)',r'/1200x1200bb.\1',url))
    return list(dict.fromkeys(out+[url]))

def download(url):
    with requests.get(url,timeout=(10,25),stream=True,headers={'User-Agent':'AlbumWall/3.0','Accept':'image/*'}) as response:
        response.raise_for_status()
        data=bytearray()
        for chunk in response.iter_content(65536):
            data.extend(chunk)
            if len(data)>20_000_000:raise ValueError('Cover exceeds 20 MB')
    stage=LOCAL/(uuid.uuid4().hex+'.image')
    try:
        stage.write_bytes(data);info=inspect_image(stage)
        # Do not turn banners/error icons into album artwork.
        if min(info['width'],info['height'])<100 or not .85<=info['width']/info['height']<=1.18:
            raise ValueError('Not a usable square album cover')
        filename=info['sha256'][:24]+'.'+info['ext'];target=COVERS/filename
        if not target.exists():stage.replace(target)
        info['cover']='Covers/'+filename
        return info
    finally:
        if stage.exists():stage.unlink()

def process_art(rows,sources,offline,report):
    cache=load_json(LOCAL/'cover_cache.json',{})
    blank={'downloaded':0,'reused':0,'manual_covers_preserved':0,'failed':0,'excluded':0,'below_target':0}
    def work(row):
        stats=dict(blank);warnings=[]
        if truth(row.get('excluded')):stats['excluded']+=1;return stats,warnings
        old=None
        if row.get('cover'):
            try:
                p=safe_path(row['cover'])
                if p.is_file():old=inspect_image(p)
                elif re.fullmatch(r'Covers/[0-9a-f]{24}\.(jpg|png|webp)',row['cover']):
                    # A managed file can disappear after a move/cleanup; let the
                    # source URL refill it instead of treating a stale cache as valid.
                    row['cover']=''
                    for key in ('width','height','sha256','phash'):row[key]=''
            except Exception:warnings.append('Invalid local cover for '+row['id'])
        managed=not row.get('cover') or (old and row['cover']=='Covers/'+old['sha256'][:24]+'.'+old['ext'])
        if old and not managed:
            row.update({k:str(old[k]) for k in ('width','height','sha256','phash')})
            stats['manual_covers_preserved']+=1;return stats,warnings
        override=row.get('cover_url','').strip()
        need_override=bool(override and row.get('download_url') not in candidates(override))
        if old and min(old['width'],old['height'])>=1200 and not need_override:
            stats['reused']+=1;return stats,warnings
        urls=[override] if override else sources.get(row['id'],[])+([row['download_url']] if row.get('download_url') else [])
        urls=list(dict.fromkeys(u for url in urls for u in candidates(url)))
        chosen=None;chosen_url=''
        if not offline:
            for url in urls:
                entry=cache.get(url,{})
                if entry and time.time()-entry.get('checked',0)<30*86400:
                    if entry.get('cover') and safe_path(entry['cover']).is_file():candidate=entry
                    else:
                        cache.pop(url,None)
                        try:
                            candidate=download(url);candidate['checked']=time.time();cache[url]=candidate
                            stats['downloaded']+=1
                        except Exception as e:
                            cache[url]={'checked':time.time(),'error':type(e).__name__};continue
                else:
                    try:
                        candidate=download(url);candidate['checked']=time.time();cache[url]=candidate
                        stats['downloaded']+=1
                    except Exception as e:
                        cache[url]={'checked':time.time(),'error':type(e).__name__};continue
                baseline=max(min(old['width'],old['height']) if old else 0,min(chosen['width'],chosen['height']) if chosen else 0)
                if need_override or min(candidate['width'],candidate['height'])>baseline:
                    chosen=candidate;chosen_url=url
                    if min(candidate['width'],candidate['height'])>=1200:break
                elif old:break
        if chosen:
            row.update({k:str(chosen[k]) for k in ('cover','width','height','sha256','phash')})
            row['download_url']=chosen_url
        elif old:
            row.update({k:str(old[k]) for k in ('width','height','sha256','phash')});stats['reused']+=1
        else:
            stats['failed']+=1;warnings.append('No cover available: '+row['id'])
        if min(int(row.get('width') or 0),int(row.get('height') or 0))<1200:stats['below_target']+=1
        return stats,warnings
    totals=dict(blank)
    with ThreadPoolExecutor(max_workers=4) as executor:
        pending=[executor.submit(work,row) for row in rows]
        for index,future in enumerate(as_completed(pending),1):
            stats,warnings=future.result()
            for key,value in stats.items():totals[key]+=value
            report['warnings'].extend(warnings)
            if index%20==0 or index==len(rows):
                print(f'Artwork {index}/{len(rows)}',flush=True)
                atomic_json(LOCAL/'cover_cache.json',dict(cache))
    atomic_json(LOCAL/'cover_cache.json',cache);report['artwork']=totals

def perceptual_dedup(rows,report):
    active=[r for r in rows if not truth(r.get('excluded')) and r.get('phash') and r.get('cover')]
    near=[];merged=0
    for i,a in enumerate(active):
        for b in active[:i]:
            if a['cover']==b['cover']:continue
            distance=(int(a['phash'],16)^int(b['phash'],16)).bit_count()
            if distance>4:continue
            if not safe_path(a['cover']).exists() or not safe_path(b['cover']).exists():continue
            with Image.open(safe_path(a['cover'])) as ia,Image.open(safe_path(b['cover'])) as ib:
                aa=ImageOps.fit(ia.convert('RGB'),(64,64));bb=ImageOps.fit(ib.convert('RGB'),(64,64))
                rms=sum(ImageStat.Stat(ImageChops.difference(aa,bb)).rms)/3
            if distance<=2 and rms<=4 and can_merge(a,b):
                winner=max((a,b),key=lambda r:int(r.get('width') or 0)*int(r.get('height') or 0))
                loser=b if winner is a else a
                # Custom image choices are never changed by perceptual heuristics.
                if not re.fullmatch(r'Covers/[0-9a-f]{24}\.(jpg|png|webp)',loser['cover']):continue
                for key in ('cover','width','height','sha256','phash','download_url'):loser[key]=winner[key]
                merged+=1
            else:near.append({'first':a['id'],'second':b['id'],'distance':distance,'rms':round(rms,2)})
    report['perceptual_dedup']={'shared_files':merged,'review_candidates':near[:100]}

def export_wallpaper(rows):
    files=list(dict.fromkeys(r['cover'] for r in rows if not truth(r.get('excluded')) and r.get('cover') and safe_path(r['cover']).is_file()))
    payload={'revision':str(time.time_ns()),'files':['../'+p.replace('\\','/') for p in files]}
    text='window.albumWallLibrary='+json.dumps(payload,ensure_ascii=False)+';\nif(window.albumWallLibraryUpdated)window.albumWallLibraryUpdated();\n'
    (ROOT/'Wallpaper'/'covers.js').write_text(text,encoding='utf-8')
    config=load_json(LOCAL/'wallpaper.json',{})
    deployed=config.get('deployed')
    if deployed and Path(deployed).is_dir():
        dest=Path(deployed)
        for name in ('index.html','project.json','flip-background.png'):shutil.copy2(ROOT/'Wallpaper'/name,dest/name)
        j=load_json(dest/'project.json',{});j['general']['properties']['albumfolder']['value']=str(COVERS).replace('\\','/')
        atomic_json(dest/'project.json',j)
        payload['files']=[safe_path(p).as_uri() for p in files]
        (dest/'covers.js').write_text('window.albumWallLibrary='+json.dumps(payload)+';\nif(window.albumWallLibraryUpdated)window.albumWallLibraryUpdated();\n',encoding='utf-8')
    return files

def prepare_wallpaper(rows):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Valve\Steam') as key:
        steam=Path(winreg.QueryValueEx(key,'SteamPath')[0])
    libraries=[steam]
    vdf=(steam/'steamapps'/'libraryfolders.vdf').read_text(encoding='utf-8')
    libraries += [Path(x.replace('\\\\','\\')) for x in re.findall(r'"path"\s+"([^"]+)"',vdf)]
    engines=[p/'steamapps'/'common'/'wallpaper_engine' for p in libraries]
    engine=next((p for p in engines if (p/'wallpaper64.exe').exists()),None)
    if not engine:raise RuntimeError('Start Wallpaper Engine from Steam once before preparing this project')
    dest=engine/'projects'/'myprojects'/'albumwall_v3';dest.mkdir(parents=True,exist_ok=True)
    atomic_json(LOCAL/'wallpaper.json',{'deployed':str(dest)})
    export_wallpaper(rows)
    subprocess.run([str(engine/'wallpaper64.exe'),'-control','openWallpaper','-file',str(dest/'project.json'),'-monitor','0'],cwd=engine,check=True)
    print('Wallpaper Engine project applied to monitor 0.',flush=True)

def run(args):
    LOCAL.mkdir(exist_ok=True);COVERS.mkdir(exist_ok=True)
    report={'time':time.strftime('%Y-%m-%d %H:%M:%S'),'warnings':[]}
    if args.youtube and not args.offline:sync_youtube(report)
    library=ROOT/'albums.csv';rows=read_csv(library)
    for row in rows:
        if not row.get('id'):row['id']=uuid.uuid4().hex[:16]
    # Preserve user-added columns as well as all existing editorial fields.
    fields=list(dict.fromkeys(FIELDS+[k for r in rows for k in r if k]))
    sources=merge_imports(rows,report)
    process_art(rows,sources,args.offline,report)
    perceptual_dedup(rows,report)
    if library.exists():
        backups=LOCAL/'backups';backups.mkdir(exist_ok=True)
        shutil.copy2(library,backups/('albums-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]+'.csv'))
    write_csv(library,rows,fields)
    files=export_wallpaper(rows)
    if args.prepare_wallpaper:prepare_wallpaper(rows)
    report['library']={'albums':len(rows),'active_albums':sum(not truth(r.get('excluded')) for r in rows),'unique_images':len(files)}
    atomic_json(LOCAL/'last_report.json',report)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 1 if report['artwork']['failed'] or report.get('youtube',{}).get('complete') is False else 0

def main():
    ap=argparse.ArgumentParser(description='AlbumWall v3: local CSV library, no scheduler or server')
    ap.add_argument('--youtube',action='store_true',help='Refresh authenticated YouTube Music library before processing')
    ap.add_argument('--offline',action='store_true',help='Merge local files only; no network')
    ap.add_argument('--prepare-wallpaper',action='store_true',help='Copy wallpaper-only files to Wallpaper Engine and apply')
    args=ap.parse_args()
    LOCAL.mkdir(exist_ok=True)
    with (LOCAL/'update.lock').open('a+b') as lock:
        lock.seek(0)
        try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:print('Another update is running.');return 3
        try:return run(args)
        except KeyboardInterrupt:print('Cancelled. Existing library retained.');return 130
        except Exception as e:
            # Never print exception strings or tracebacks from authenticated clients.
            print('Update failed:',type(e).__name__,'. Existing CSV backup and covers are retained.');return 2

if __name__=='__main__':sys.exit(main())
