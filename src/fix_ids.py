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
    
    # Old logic
    m = re.match(r"^(#{1,6})\s+(.*)", p)
    if m:
        pass
    else:
        n += 1
        print(f"OLD: b{n:04d} -> {p[:30]}")
