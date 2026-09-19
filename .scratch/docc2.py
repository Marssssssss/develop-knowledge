import json, sys, urllib.request, ssl
ctx = ssl.create_default_context()
BASE = "https://developer.apple.com/tutorials/data/documentation/"
def get(u):
    req = urllib.request.Request(u, headers={'User-Agent':'Mozilla/5.0'})
    return json.loads(urllib.request.urlopen(req, timeout=60, context=ctx).read().decode('utf-8'))
def walk(o, out):
    if isinstance(o, list):
        for x in o: walk(x, out)
    elif isinstance(o, dict):
        t = o.get('type')
        if t == 'variants' or 'variants' in o and t is None:
            pass
        if t in ('text','codeVoice') and 'text' in o and isinstance(o['text'], str):
            out.append(o['text']); return
        if t == 'codeListing' and 'code' in o:
            out.append('[CODE]'); out.append('\n'.join(o['code'])); out.append('[/CODE]'); return
        if t in ('heading',):
            out.append('\n### '); walk(o.get('text',''), out); walk(o.get('inlineContent',[]), out); return
        if t == 'table':
            for row in o.get('rows',[]): 
                cells=[]
                for c in row.get('cells',[]):
                    sub=[]; walk(c, sub); cells.append(' '.join(sub).strip())
                out.append(' | '.join(cells))
            return
        if 'variants' in o:
            walk(o['variants'][0].get('paths', o['variants']), out) if False else None
            vs = o['variants']
            if vs and isinstance(vs[0], dict):
                if 'paths' in vs[0]:
                    walk(vs[0]['paths'], out); return
                walk(vs[0], out); return
        for k, v in o.items():
            if k in ('variants','identifier','references','metadata','schemaVersion','hierarchy','seeAlsoSections','legalNotices'): continue
            walk(v, out)
def conv(path):
    d = get(BASE + path + '.json')
    out = []
    for sec in d.get('primaryContentSections', []):
        k = sec.get('kind')
        if k: out.append('\n## [%s] %s' % (k, sec.get('title','') or ''))
        walk(sec.get('content', []), out)
    txt = '\n'.join(out)
    fn = 'docc_' + path.replace('/','_') + '.txt'
    open(fn,'w',encoding='utf-8').write(txt)
    print(path, len(txt), '->', fn, file=sys.stderr)
for a in sys.argv[1:]: conv(a)
