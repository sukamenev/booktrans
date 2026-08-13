import re
import os

with open('src/booktrans/build.py', 'r', encoding='utf-8') as f:
    build_src = f.read()

with open('src/booktrans/output.py', 'r', encoding='utf-8') as f:
    output_src = f.read()

# Rename build_fb2 to build_book in build.py
build_src = build_src.replace('def build_fb2(', 'def build_book(')

# Now we need to extract the FB2 generation logic from build_book.
# The logic starts after the early return for output.WRITERS
split_marker = 'return\n\n    o = []\n    w = o.append'
parts = build_src.split(split_marker)

if len(parts) == 2:
    top_part = parts[0]
    bottom_part = parts[1]
    
    # We want to keep everything up to output.WRITERS[ext](...)
    # and then add an else branch for ext == ".fb2"
    # Actually, we can just change the flow.
    new_top = top_part.replace('''
    # формат по расширению: fb2 собирается ниже, остальные — в output.py
    ext = os.path.splitext(dest)[1].lower()
    if ext in output.WRITERS:''', '''
    # сборка нужного формата по расширению
    ext = os.path.splitext(dest)[1].lower()
    if ext in output.WRITERS:''')
    
    # Now we need to prepare kwargs for FB2
    fb2_kw_prep = '''
        if ext == ".fb2":
            kw.update({
                "blocks": blocks, "tr": tr, "partial": partial, "log": log,
                "note_seq": note_seq, "nid": nid, "notes_map": notes_map, "lang": lang,
                "about_head": head, "about_body": body,
                "details_head": dhead, "details_body": dbody,
                "PIPELINE": PIPELINE, "esc": esc, "span_attr": output.span_attr
            })
'''
    
    # Let's just create a `def write_fb2(...)` in `output.py` and register it in `WRITERS`.
    # Wait, passing `esc` and `PIPELINE` from `build.py`? Yes, that avoids moving them.
    # It's much safer and simpler.

    with open('src/booktrans/build.py', 'w', encoding='utf-8') as f:
        # Reconstruct build.py
        # We need to inject fb2_kw_prep before output.WRITERS call
        before_call = '        output.WRITERS[ext](dest, meta, items, notes, images or {},'
        replaced_top = new_top.replace(before_call, fb2_kw_prep + before_call)
        
        # Remove the rest of the function by just closing it
        f.write(replaced_top + '''
        return
    else:
        raise ValueError(f"Unknown format: {ext}")
''')

        # We also need to add the rest of the original build.py that was after build_fb2
        # We need to find where build_fb2 ends.
        # It ends at `sum(1 for b in blocks if b["kind"] == "p")))`
        end_marker = 'sum(1 for b in blocks if b["kind"] == "p")))'
        if end_marker in bottom_part:
            rest = bottom_part[bottom_part.index(end_marker) + len(end_marker) + 1:]
            f.write(rest)

    # Now append `write_fb2` to output.py
    with open('src/booktrans/output.py', 'w', encoding='utf-8') as f:
        # First we need to extract the body of the fb2 writer
        fb2_writer_body = split_marker.replace('return\n\n    ', '    ') + bottom_part.split(end_marker)[0] + end_marker + ')'
        
        # Prepare function signature
        fb2_func = '''

import xml.etree.ElementTree as ET

def write_fb2(dest, meta, items, notes, images, note_prefix, st=None, cover=None, **kw):
    blocks = kw['blocks']
    tr = kw['tr']
    partial = kw['partial']
    log = kw['log']
    note_seq = kw['note_seq']
    nid = kw['nid']
    notes_map = kw['notes_map']
    lang = kw['lang']
    about_head = kw['about_head']
    about_body = kw['about_body']
    details_head = kw['details_head']
    details_body = kw['details_body']
    PIPELINE = kw['PIPELINE']
    esc = kw['esc']
    span_attr = kw['span_attr']
    code = meta.get("target_lang", "ru")

    def aid(b):
        return f' id="{b["id"]}"' if b["id"] in nid or b["id"] in (notes_map or {}) else ""
''' + fb2_writer_body.replace('    o = []', 'o = []')
        
        # We need to indent fb2_writer_body properly. It's currently indented 4 spaces.
        # It's perfect.
        
        # Add to WRITERS
        modified_output = output_src.replace('".epub": write_epub}', '".epub": write_epub, ".fb2": write_fb2}')
        f.write(modified_output + fb2_func)

    print("Done")
else:
    print("Could not find split marker")
