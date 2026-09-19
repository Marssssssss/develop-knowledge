import json, re, sys, urllib.request, ssl
ctx = ssl.create_default_context()
BASE = "https://developer.apple.com/tutorials/data/documentation/"
def get(u):
    req = urllib.request.Request(u, headers={'User-Agent':'Mozilla/5.0'})
    return json.loads(urllib.request.urlopen(req, timeout=60, context=ctx).read().decode('utf-8'))
def walk(node, out):
    if isinstance(node, dict):
        t = node.get('type')
        if t in ('paragraph','text','codeListing','heading','aside','note','listItem','termList','table','row','cell'):
            pass
        for k in ('content','inlineContent','code','items','rows','cells','title'):
            if k in node:
                walk(node[k], out)
        if t == 'codeListing':
            out.append('[CODE]')
            out.append('\n'.join(node.get('code', [])))
            out.append('[/CODE]')
        elif t in ('paragraph','heading'):
            s = ' '.join(str(x.get('text','')) for x in node.get('inlineContent',[]) if isinstance(x,dict))
            if s.strip(): out.append(s)
        elif t in ('table','row','cell'):
            pass
    elif isinstance(node, list):
        for x in node: walk(x, out)
def convert(path, outfile):
    data = get(BASE + path + '.json')
    primary = data.get('primaryContent', {}).get('sections', []) or []
    out = []
    for sec in primary:
        if 'kind' in sec: out.append('## ' + str(sec.get('kind')) + ' ' + str(sec.get('title','')))
        walk(sec.get('content', []), out)
    txt = '\n'.join(out)
    open(outfile,'w',encoding='utf-8').write(txt)
    print(path, len(txt), 'chars ->', outfile, file=sys.stderr)
for a in sys.argv[1:]:
    convert(a, 'docc_' + a.replace('/','_') + '.txt')
