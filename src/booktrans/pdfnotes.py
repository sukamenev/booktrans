"""Conservative binding of numbered PDF notes after all pages are read."""
import ctypes
import os
import re
import shutil
import subprocess
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass


MARK = re.compile(
    r"<a(?P<slot>\d+)>\s*\[?(?P<link>\d{1,3})\]?\s*</a(?P=slot)>"
    r"|<sup>\s*(?P<sup>\d{1,3})\s*</sup>"
    r"|(?P<raised>[⁰¹²³⁴⁵⁶⁷⁸⁹]+)|\^(?P<caret>\d{1,3})\^?"
    r"|\[(?P<bracket>\d{1,3})\]|(?<=[.!?…»”])(?P<bare>\d{1,3})(?=\s|<|$)"
)
ENTRY = re.compile(r"^\s*(\d{1,3})\\?\.\s+")
TAGS = re.compile(r"</?[a-zA-Z][^>]*>")
WORD = re.compile(r"[^\W\d_]+")


@dataclass(frozen=True)
class Anchor:
    page: int
    to: int
    number: str
    left: tuple
    right: tuple
    entry: bool


def _words(text):
    text = re.sub(r"[\ufffe\xad]\s*|-(?:\r?\n)\s*", "", text)
    return tuple(WORD.findall(TAGS.sub("", MARK.sub(" ", text)).casefold()))


def _number(mark):
    return unicodedata.normalize("NFKC", next(mark[k] for k in
           ("link", "sup", "raised", "caret", "bracket", "bare") if mark[k]))


def _rectangles(raw, pdf, page):
    offset, link = ctypes.c_int(), raw.FPDF_LINK()
    left, bottom, right, top = page.get_bbox()
    while raw.FPDFLink_Enumerate(page.raw, ctypes.byref(offset), ctypes.byref(link)):
        dest = raw.FPDFLink_GetDest(pdf.raw, link)
        action = raw.FPDFLink_GetAction(link)
        if not dest and action and raw.FPDFAction_GetType(action) == raw.PDFACTION_GOTO:
            dest = raw.FPDFAction_GetDest(pdf.raw, action)
        if not dest and (not action or raw.FPDFAction_GetType(action) != raw.PDFACTION_GOTO):
            continue
        target = raw.FPDFDest_GetDestPageIndex(pdf.raw, dest) + 1 if dest else None
        box = raw.FS_RECTF()
        if not raw.FPDFLink_GetAnnotRect(link, ctypes.byref(box)):
            continue
        l, r = sorted((box.left, box.right))
        b, t = sorted((box.bottom, box.top))
        if 0 < r-l < 35 and 0 < t-b < 28 and l < right and r > left and b < top and t > bottom:
            yield target if target and 1 <= target <= len(pdf) else None, (l, b, r, t)


def _named_destinations(path):
    """Optional recovery for name trees PDFium cannot resolve."""
    if not shutil.which("mutool"):
        return {}
    try:
        def show(mode):
            return subprocess.run(["mutool", "show", os.path.abspath(path), mode],
                                  capture_output=True, encoding="utf-8",
                                  errors="replace", timeout=90, check=True).stdout
        pages = {int(obj): int(p) for p, obj in re.findall(
            r"^page (\d+) = (\d+) 0 R$", show("pages"), re.M)}
        objects = {int(n): v for n, v in re.findall(
            r"^(\d+) 0 obj (.*)$", show("grep"), re.M)}
    except (OSError, subprocess.SubprocessError):
        return {}
    names = defaultdict(set)
    for obj in objects.values():
        if "/Names[" in obj:
            for m in re.finditer(r"\(([^()]*)\)(?:\[(\d+) 0 R/|(\d+) 0 R)",
                                 obj.partition("/Names[")[2]):
                if m[2]:
                    ref = int(m[2])
                else:
                    indirect = re.search(r"\[(\d+) 0 R/", objects.get(int(m[3]), ""))
                    ref = int(indirect[1]) if indirect else None
                if ref in pages:
                    names[m[1]].add(pages[ref])
    result = defaultdict(set)
    for obj, page in pages.items():
        refs = re.search(r"/Annots\[([^\]]*)\]", objects.get(obj, ""))
        if not refs:
            continue
        for ref in re.findall(r"(\d+) 0 R", refs[1]):
            annot = objects.get(int(ref), "")
            a = re.search(r"/A (\d+) 0 R", annot)
            box = re.search(r"/Rect\[([^\]]*)\]", annot)
            if not a or not box or "/Subtype/Link" not in annot:
                continue
            action = objects.get(int(a[1]), "")
            if "/S/GoTo" not in action:
                continue
            dest = re.search(r"/D\(([^()]*)\)", action)
            if not dest or len(names[dest[1]]) != 1:
                continue
            try:
                x1, y1, x2, y2 = map(float, box[1].split())
            except ValueError:
                continue
            coords = min(x1,x2), min(y1,y2), max(x1,x2), max(y1,y2)
            key = page, tuple(round(x, 2) for x in coords)
            result[key].update(names[dest[1]])
    return {k: next(iter(v)) for k, v in result.items() if len(v) == 1}


def read_anchors(path):
    """Get the visible numeric anchors and their page destinations from PDF."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return []
    raw = pdfium.raw
    result = []
    fallback = None
    try:
        with pdfium.PdfDocument(path) as pdf:
            for i in range(len(pdf)):
                page = pdf[i]
                try:
                    rects = list(_rectangles(raw, pdf, page))
                    if any(dst is None for dst, _ in rects):
                        if fallback is None:
                            fallback = _named_destinations(path)
                        rects = [(dst or fallback.get((i+1, tuple(round(x, 2) for x in rect))), rect)
                                 for dst, rect in rects]
                    rects = [(dst, rect) for dst, rect in rects if dst is not None]
                    if not rects:
                        continue
                    tp = page.get_textpage()
                    try:
                        result.extend(_page_anchors(raw, tp, i+1, rects))
                    finally:
                        tp.close()
                finally:
                    page.close()
    except (OSError, pdfium.PdfiumError):
        return []
    return result


def _page_anchors(raw, tp, page, rects):
    # A PDF link rectangle can overlap letters on the lines above and below.
    # Pick a digit run by glyph centers instead of reading the whole rectangle.
    count = tp.count_chars()
    digits = []
    run = []
    for i in range(count+1):
        num = raw.FPDFText_GetUnicode(tp.raw, i) if i < count else 0
        if 48 <= num <= 57:
            b = tp.get_charbox(i)
            run.append((i, chr(num), (b[0]+b[2])/2, (b[1]+b[3])/2))
        elif run:
            if len(run) <= 3:
                digits.append(run)
            run = []
    seen = set()
    for dst, (l, b, r, t) in rects:
        runs = [v for v in digits if all(l <= x <= r and b <= y <= t for _, _, x, y in v)]
        if len(runs) != 1:
            continue
        items = runs[0]
        at = items[0][0]
        after = items[-1][0]+1
        key = dst, at
        if key in seen:
            continue
        seen.add(key)
        n = "".join(v[1] for v in items)
        left = tp.get_text_range(max(0, at-160), min(at, 160))
        right = tp.get_text_range(after, min(160, count-after))
        yield Anchor(page, dst, n, _words(left)[-12:], _words(right)[:12],
                     bool(re.fullmatch(r"\s*"+n+r"\s*\.\s*", tp.get_text_bounded(l,b,r,t))))


def _entry(block):
    if block["kind"] == "note" and str(block.get("name", "")).isdecimal():
        return str(block["name"]), block["text"]
    if block["kind"] == "p":
        m = ENTRY.match(block["text"])
        if m:
            return m[1], block["text"][m.end():]
    return "", ""


def _near(words, other):
    # A word inserted before the reference by OCR can shift the comparison.
    return any(words[a:a+4] == other[b:b+4] and len(words[a:a+4]) == 4
               for a in range(3) for b in range(3))


def _same_head(left, right):
    return len(left) >= 8 and left[:8] == right[:8]


def _next_slot(block, mark):
    existing = list(block.get("links") or ())
    # Keep the ordinal of legacy <aN> placeholders where possible.
    slot = int(mark["slot"]) if mark["slot"] else 0
    if (slot and slot <= len(existing) and re.fullmatch(r"#n\d+", existing[slot-1])
            and block["text"].count(f"<a{slot}>") == 1):
        return existing, slot
    existing.append("")
    return existing, len(existing)


def bind(anchors, blocks, translation=None):
    """Resolve only pairs with a PDF backlink, matching entry and context.

    With an old translation, apply changes to the assembly copies of the
    blocks and text. Missing or ambiguous marks are counted for review.
    """
    # The legacy translation argument can be removed from 2027-02-01.
    ids = Counter(b.get("note_id") for b in blocks if b["kind"] == "note")
    ambiguous = {f"#{key}" for key, count in ids.items() if key and count > 1}
    by_page = defaultdict(list)
    for b in blocks:
        if not b.get("drop"):
            by_page[b.get("_page")].append(b)
    reverse = defaultdict(list)
    for a in anchors:
        if a.entry:
            reverse[a.to, a.page, a.number].append(a)
    plans = defaultdict(list)
    claimed, targets = set(), {}
    pending = 0
    for ref in anchors:
        if ref.entry or not (entries := reverse.get((ref.page, ref.to, ref.number))):
            continue
        matches, renumber = [], []
        for b in by_page[ref.to]:
            n, body = _entry(b)
            if not n:
                continue
            head = _words(body)[:10]
            if n == ref.number and any(_near(e.right, head) for e in entries):
                matches.append((b,n))
            elif any(_same_head(e.right, head) for e in entries):
                # Ignore a wrong OCR number only when the entry itself has
                # a long, unique match. Short entries such as "Ibid." do
                # not give enough evidence to repair the numbering.
                if translation is not None and b["id"] in translation and b["kind"] == "p":
                    printed = ENTRY.match(translation[b["id"]])
                    if not printed or printed[1] != n:
                        continue
                renumber.append((b,n))
        matches = matches or renumber
        src, relabel = [], []
        for b in by_page[ref.page-1]+by_page[ref.page]:
            if b["kind"] != "p" or any(b is target for target, _ in matches):
                continue
            text = b["text"]
            for m in MARK.finditer(text):
                left, right = _words(text[:m.start()])[::-1], _words(text[m.end():])
                if _number(m) == ref.number and (_near(ref.left[::-1], left) or _near(ref.right, right)):
                    src.append((b,m))
                elif _same_head(ref.left[::-1], left) and _same_head(ref.right, right):
                    relabel.append((b,m))
        src = src or relabel
        if len(matches) != 1 or len(src) != 1:
            pending += 1
            continue
        b, mark = src[0]
        source_key = b["id"], mark.span()
        if source_key in claimed:
            pending += 1
            continue
        rt = None
        if translation is not None and b["id"] in translation:
            ru = translation[b["id"]]
            exact = [m for m in MARK.finditer(ru) if m[0] == mark[0]]
            if len(exact) != 1:
                pending += 1
                continue
            rt = exact[0]
        elif translation is not None and not b.get("asis"):
            pending += 1
            continue
        target, old_n = matches[0]
        if target["id"] in targets and targets[target["id"]][1] != ref.number:
            pending += 1
            continue
        claimed.add(source_key)
        targets[target["id"]] = target, ref.number, old_n
        plans[b["id"]].append((b,mark,rt,target["id"],ref.number))

    for _, edits in plans.items():
        b = edits[0][0]
        changes, translated = [], []
        for _, mark, rt, target, n in edits:
            links, slot = _next_slot(b, mark)
            links[slot-1] = "#" + target
            b["links"] = links
            tag = f"<sup><a{slot}>{n}</a{slot}></sup>"
            # The legacy mark may already be inside a superscript.
            changes.append((*_sup_span(b["text"], mark), tag))
            if rt is not None:
                translated.append((*_sup_span(translation[b["id"]], rt), tag))
        b["text"] = _replace(b["text"], changes)
        if translated:
            translation[b["id"]] = _replace(translation[b["id"]], translated)
    for target, n, old_n in targets.values():
        target["note_origin"] = "source"
        if target["kind"] == "note":
            target["text"] = f"{n}. " + target["text"]
            if translation is not None and target["id"] in translation:
                translation[target["id"]] = f"{n}. " + translation[target["id"]]
            target["kind"] = "p"
            target.pop("note_id", None)
        elif n != old_n:
            target["text"] = ENTRY.sub(f"{n}. ", target["text"], count=1)
            if translation is not None and target["id"] in translation:
                translation[target["id"]] = ENTRY.sub(f"{n}. ", translation[target["id"]], count=1)
    if ambiguous:
        for b in blocks:
            if any(u in ambiguous for u in b.get("links", ())):
                b["links"] = ["" if u in ambiguous else u for u in b["links"]]
            if b["kind"] == "note" and f'#{b.get("note_id")}' in ambiguous:
                b["kind"] = "p"
                n = str(b.get("name", ""))
                if n.isdecimal():
                    b["text"] = f"{n}. " + b["text"]
                    if translation is not None and b["id"] in translation:
                        translation[b["id"]] = f"{n}. " + translation[b["id"]]
    return sum(map(len, plans.values())), pending


def _sup_span(text, mark):
    lo, hi = mark.span()
    if text[max(0,lo-5):lo] == "<sup>" and text[hi:hi+6] == "</sup>":
        return lo-5, hi+6
    return lo, hi


def _replace(text, edits):
    for lo,hi,tag in sorted(edits, reverse=True):
        text = text[:lo]+tag+text[hi:]
    return text
