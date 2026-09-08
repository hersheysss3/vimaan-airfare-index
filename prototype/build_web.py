# -*- coding: utf-8 -*-
"""
Builds the web deployment copy from the offline-proof console.

The local file inlines three.js and framer-motion so it runs with no network.
For a hosted copy that is dead weight, so both are swapped for CDN tags and
the page drops from ~880 KB to ~140 KB.

    python build_web.py     ->  web/index.html
"""
import io, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'vimaan-console.html')
OUT_DIR = os.path.join(HERE, 'web')
OUT = os.path.join(OUT_DIR, 'index.html')

THREE_CDN = "https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"
MOTION_CDN = "https://cdn.jsdelivr.net/npm/framer-motion@13.2.0/dist/dom.js"

s = io.open(SRC, encoding='utf-8').read()
before = len(s)

# --- framer-motion: inlined UMD -> CDN ------------------------------------
m = re.search(r'<script>/\* framer-motion .*?\n</script>\n', s, re.S)
assert m, "inlined framer-motion block not found"
s = s[:m.start()] + '<script src="%s"></script>\n' % MOTION_CDN + s[m.end():]

# --- three.js: inlined UMD -> CDN ----------------------------------------
m = re.search(r'<script>/\* three\.js r128 .*?\n</script>\n', s, re.S)
assert m, "inlined three.js block not found"
s = s[:m.start()] + '<script src="%s"></script>\n' % THREE_CDN + s[m.end():]

# a hosted page gets a viewport and a description; the artifact host adds
# its own, but a bare Vercel deploy does not
head_extra = (
    '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
    '<meta name="description" content="VIMAAN - a real-time airfare price '
    'index for India, built for Smart India Hackathon 2026 problem SIH26056.">\n'
    '<meta name="color-scheme" content="light dark">\n'
)
s = s.replace('<title>', head_extra + '<title>', 1)

if not os.path.isdir(OUT_DIR):
    os.makedirs(OUT_DIR)
io.open(OUT, 'w', encoding='utf-8').write(s)

print("offline build : %6d KB" % (before // 1024))
print("web build     : %6d KB  -> %s" % (len(s) // 1024, OUT))
