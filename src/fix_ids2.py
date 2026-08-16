from booktrans import extract
import re

text = """These principles are shown in **Exhibit 2-2**.

![image](images/img_p0060_188_1384.png)

*Henri Fayol’s general administrative theory focused on what managers do and what defined good management practice. Source: Yogi Black/Alamy Stock Photo*"""

paras = text.split("\n\n")
n = 0
for p in paras:
    p = p.strip()
    if not p: continue
    
    # New logic
    img_matches = list(re.finditer(r"!\[(.*?)\]\(images/([^)]+)\)", p))
    if img_matches:
        last_end = 0
        for match in img_matches:
            pre_text = p[last_end:match.start()].strip()
            if pre_text:
                n += 1
                print(f"NEW: b{n:04d} -> {pre_text[:30]}")
            print(f"NEW: IMAGE MATCH")
            last_end = match.end()
        post_text = p[last_end:].strip()
        if post_text:
            n += 1
            print(f"NEW: b{n:04d} -> {post_text[:30]}")
        continue

    m = re.match(r"^(#{1,6})\s+(.*)", p)
    if m:
        pass
    else:
        n += 1
        print(f"NEW: b{n:04d} -> {p[:30]}")
