import re, sys, urllib.request, ssl
ctx = ssl.create_default_context()
def fetch(u, out):
    req = urllib.request.Request(u, headers={'User-Agent':'Mozilla/5.0'})
    raw = urllib.request.urlopen(req, timeout=60, context=ctx).read().decode('utf-8','replace')
    # strip scripts/styles
    raw = re.sub(r'<script[^>]*>.*?</script>', ' ', raw, flags=re.S)
    raw = re.sub(r'<style[^>]*>.*?</style>', ' ', raw, flags=re.S)
    txt = re.sub(r'<[^>]+>', '\n', raw)
    import html as H
    txt = H.unescape(txt)
    txt = re.sub(r'\n{2,}', '\n', txt)
    txt = re.sub(r'[ \t]{2,}', ' ', txt)
    open(out,'w',encoding='utf-8').write(txt)
    print(u, '->', out, len(txt), 'chars')
for i, u in enumerate(sys.argv[1:]):
    name = re.sub(r'.*/', '', u) or ('p%d'%i)
    fetch(u, 'apple_%s.txt' % name)
