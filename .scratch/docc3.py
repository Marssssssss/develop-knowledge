import json, sys, urllib.request, ssl
ctx = ssl.create_default_context()
BASE = "https://developer.apple.com/tutorials/data/documentation/"
def get(u):
    req = urllib.request.Request(u, headers={'User-Agent':'Mozilla/5.0'})
    return json.loads(urllib.request.urlopen(req, timeout=60, context=ctx).read().decode('utf-8'))
def walk(o, out):
    if isinstance(o, str):
        if o.strip(): out.append(o.strip())
        return
    if isinstance(o, list):
        for x in o: walk(x, out)
        return
    if isinstance(o, dict):
        t = o.get('type')
        if t == 'codeListing' and isinstance(o.get('code'), list):
            out.append('[CODE]'); out.append('\n'.join(o['code'])); out.append('[/CODE]'); return
        for k in ('heading','title','text','code','inlineContent','content','rows','cells','items','name','kind'):
            if k in o: walk(o[k], out)
        return
for a in sys.argv[1:]:
    d = get(BASE + a + '.json')
    out = []
    for sec in d.get('primaryContentSections', []):
        walk(sec, out)
    seen=set(); res=[]
    for s in out:
        if s not in seen or len(s) > 60:
            seen.add(s); res.append(s)
    fn = 'docc_' + a.replace('/','_') + '.txt'
    open(fn,'w',encoding='utf-8').write('\n'.join(res))
    print(a, len(res), 'lines ->', fn, file=sys.stderr)
