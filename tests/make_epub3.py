"""Собрать книгу epub3 с настоящими сносками — такой в библиотеке не нашлось."""
import os, zipfile, struct, zlib

# Книга пишется рядом со скриптом, в набор для проверок.
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   'corpus', '04_epub3_notes_en.epub')

def png(w, h, rgb):
    def chunk(t, d):
        c = t + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c))
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))

CH1 = """<h1>Chapter One: The Long Watch</h1>
<p>The lighthouse had stood on the headland for ninety years, and for eighty of
them a keeper had climbed its stairs every evening at dusk. Mira was the last of
them. The automation crew was due in spring.</p>
<p>She kept the log the way her grandmother had taught her: the weather first,
then the sea, then anything else worth saying. Most evenings there was nothing
else worth saying.</p>
<p>On the ninth of October she wrote that the wind had gone round to the
north-east and that a ship had passed the point without answering her
light<a epub:type="noteref" href="#n1" id="r1"><sup>1</sup></a>. She underlined
the second part twice.</p>
<p>Ships had answered the light for as long as there had been a light. It was
not a rule anyone had written down. It was simply what happened: you passed the
point, you flashed twice, the keeper flashed back, and both of you went on about
your business a little less alone.</p>
<p>The ship that did not answer was a bulk carrier riding high and empty, and it
made no more noise than a cloud. Mira watched it until it was a smudge, then
went down and made tea she did not drink.</p>
<p>In the morning the wind had dropped and the sea was the colour of a coin. She
walked the shingle as far as the wreck of the <em>Katharine Beale</em>, which had
gone ashore in 1911 with a cargo of pianos<a epub:type="noteref" href="#n2" id="r2"><sup>2</sup></a>
and had been settling into the beach ever since.</p>
<p>Her grandmother had called it the loudest wreck in the county, because for a
week afterwards the tide had played it. Mira had never been able to decide
whether that was true or whether it was the kind of thing a keeper says to a
child who is afraid of the dark.</p>
<p>She sat on the ribs of the ship and thought about the spring, and about the
crew who would come with their boxes of relays, and about the fact that nobody
had yet told her where she was supposed to go afterwards.</p>
<aside epub:type="footnote" id="n1"><p>The practice of answering a shore light
with two flashes is not required by any convention, and appears in no British
regulation of the period. <a epub:type="backlink" href="#r1">Return</a></p></aside>
<aside epub:type="footnote" id="n2"><p>The cargo manifest lists forty upright
pianos consigned to a dealer in Liverpool; eleven were recovered.
<a epub:type="backlink" href="#r2">Return</a></p></aside>"""

CH2 = """<h1>Chapter Two: The Relay Crew</h1>
<p>They arrived in March, three of them, in a van that had trouble with the last
half-mile of track. The foreman was a courteous man named Alder who shook her
hand and then looked past her at the tower the way a doctor looks at an X-ray.</p>
<p>"You'll have had the letter," he said.</p>
<p>"I had the letter."</p>
<p>"Then you'll know we're not the ones who decided it."</p>
<p>She showed them the lamp room and the clockwork that had driven the lens
before the electricity came, and which she still wound on Sundays because a
thing that has been wound for ninety years should not be allowed to stop on
your watch<a epub:type="noteref" href="#n3" id="r3"><sup>3</sup></a>.</p>
<p>Alder wound it once himself, badly, and laughed at himself, and after that
she liked him better than she had intended to.</p>
<p>The work took eleven days. On the twelfth the light came on by itself at
17:42, four minutes earlier than she would have lit it, and went off at dawn
without being told. Mira stood in the yard and watched it do this and found that
she had no feelings about it whatsoever, which frightened her more than grief
would have.</p>
<p>She wrote in the log: <em>Light automatic from this date. Wind south-west,
moderate. Sea slight. Nothing else worth saying.</em></p>
<p>Then she wrote, underneath, in smaller letters: <em>A ship answered.</em></p>
<aside epub:type="footnote" id="n3"><p>The mechanism is a weight-driven train of
the type supplied by Chance Brothers; the weight falls the full height of the
tower over roughly four hours. <a epub:type="backlink" href="#r3">Return</a></p></aside>"""

PAGES = {
 "cover.xhtml": ('Cover', '<div class="cover"><img src="images/cover.png" alt="Cover"/></div>'),
 "titlepage.xhtml": ('Title', '<h1>The Long Watch</h1><p class="au">Elinor Vance</p>'),
 "copyright.xhtml": ('Copyright', '<h1>From the Publisher</h1>'
    '<p>First published by Harbour House. This edition prepared for testing '
    'purposes. All rights of the author are asserted.</p>'
    '<p>No part of this book may be reproduced without permission.</p>'),
 "dedication.xhtml": ('Dedication', '<p class="ded">For the keepers, and for those who answered.</p>'),
 "ch01.xhtml": ('Chapter One', CH1),
 "ch02.xhtml": ('Chapter Two', CH2),
 "acknowledgments.xhtml": ('Acknowledgments', '<h1>Acknowledgments</h1>'
    '<p>My thanks to the keepers of Souter and St Mary\'s, who let me climb their '
    'towers and answered questions no one had asked them in years.</p>'
    '<p>Thanks also to my editor, who cut the chapter about the pianos, and was right.</p>'),
 "abouttheauthor.xhtml": ('About the Author', '<h1>About the Author</h1>'
    '<p><img src="images/author.png" alt="The author"/></p>'
    '<p>Elinor Vance was born in Whitby and worked for eleven years as a marine '
    'surveyor before writing her first novel. She lives on the Northumberland coast.</p>'),
 "backad.xhtml": ('Also by', '<h1>Also by Elinor Vance</h1>'
    '<p><img src="images/backad.png" alt="Also by this author"/></p>'
    '<p><em>The Coal Road</em> — a novel of the pit villages.</p>'),
}
ORDER = list(PAGES)

def xhtml(title, body):
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">'
            f'<head><title>{title}</title>'
            '<link rel="stylesheet" type="text/css" href="style.css"/></head>'
            f'<body>{body}</body></html>')

man, spine, nav = [], [], []
for i, name in enumerate(ORDER):
    t, b = PAGES[name]
    man.append(f'<item id="p{i}" href="{name}" media-type="application/xhtml+xml"/>')
    spine.append(f'<itemref idref="p{i}"/>')
    nav.append(f'<li><a href="{name}">{t}</a></li>')
IMGS = {"cover.png": png(60, 90, (40, 60, 110)),
        "author.png": png(40, 40, (150, 140, 130)),
        "backad.png": png(60, 40, (110, 90, 60))}
for i, n in enumerate(IMGS):
    props = ' properties="cover-image"' if n == "cover.png" else ''
    man.append(f'<item id="i{i}" href="images/{n}" media-type="image/png"{props}/>')
man.append('<item id="css" href="style.css" media-type="text/css"/>')
man.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')

OPF = ('<?xml version="1.0" encoding="utf-8"?>\n'
 '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bid">'
 '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
 '<dc:identifier id="bid">urn:uuid:booktrans-test-0004</dc:identifier>'
 '<dc:title>The Long Watch</dc:title><dc:creator>Elinor Vance</dc:creator>'
 '<dc:language>en</dc:language><dc:publisher>Harbour House</dc:publisher>'
 '<dc:date>2019</dc:date></metadata>'
 f'<manifest>{"".join(man)}</manifest><spine>{"".join(spine)}</spine></package>')
NAV = xhtml('Contents', f'<nav epub:type="toc"><h1>Contents</h1><ol>{"".join(nav)}</ol></nav>')
CONT = ('<?xml version="1.0"?>\n<container version="1.0" '
 'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
 '<rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/>'
 '</rootfiles></container>')
CSS = "body{font-family:serif;margin:1em} .cover img{width:100%} .au{font-style:italic}"

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with zipfile.ZipFile(OUT, 'w', zipfile.ZIP_DEFLATED) as z:
    z.writestr('mimetype', 'application/epub+zip', zipfile.ZIP_STORED)
    z.writestr('META-INF/container.xml', CONT)
    z.writestr('EPUB/package.opf', OPF)
    z.writestr('EPUB/nav.xhtml', NAV)
    z.writestr('EPUB/style.css', CSS)
    for n, (t, b) in PAGES.items():
        z.writestr(f'EPUB/{n}', xhtml(t, b))
    for n, d in IMGS.items():
        z.writestr(f'EPUB/images/{n}', d)
print("собрана", OUT)
