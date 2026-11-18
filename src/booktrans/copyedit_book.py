"""Edit text in place in the document tree, never rebuild a translation edition."""
import posixpath
import re
from pathlib import Path
from urllib.parse import unquote
from xml.dom import Node, minidom
from zipfile import ZipFile


def elements(node, name):
    return node.getElementsByTagNameNS("*", name)


def text_nodes(parent):
    for child in parent.childNodes:
        if child.nodeType == Node.TEXT_NODE and child.data.strip():
            yield child
        elif child.nodeType == Node.ELEMENT_NODE and child.localName not in (
                "code", "script", "style", "math", "svg", "a", "image", "rt"):
            yield from text_nodes(child)


class Book:
    def __init__(self, path, encoding=None):
        self.path = Path(path)
        self.ext = self.path.suffix.lower()
        self.sections = []
        self.records = {}
        self.targets = {}
        self.docs = {}
        self.changed = set()
        if self.ext == ".txt":
            raw = self.path.read_bytes()
            self.encoding = encoding or ("utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8")
            text = raw.decode(self.encoding)
            self.newline = "\r\n" if "\r\n" in text else "\n"
            self.parts = re.split(r"((?:\r?\n[ \t]*){2,})", text)
            self.sections.append([])
            for i in range(0, len(self.parts), 2):
                value = self.parts[i]
                if re.match(r"(?i)^\s*(?:chapter|глава|part|часть|акт|act)\s+\S+", value):
                    self.sections.append([])
                if value.strip():
                    self.add(value, True, i)
        elif self.ext == ".fb2":
            self.xml("", self.path.read_bytes(), "fb2")
        elif self.ext == ".epub":
            with ZipFile(path) as z:
                container = minidom.parseString(z.read("META-INF/container.xml"))
                opf = elements(container, "rootfile")[0].getAttribute("full-path")
                package = minidom.parseString(z.read(opf))
                items = {i.getAttribute("id"): i for i in elements(package, "item")}
                for ref in elements(package, "itemref"):
                    item = items[ref.getAttribute("idref")]
                    if ("nav" in item.getAttribute("properties").split() or
                            item.getAttribute("media-type") != "application/xhtml+xml"):
                        continue
                    name = posixpath.normpath(posixpath.join(posixpath.dirname(opf),
                                                            unquote(item.getAttribute("href"))))
                    if name not in self.docs:
                        self.xml(name, z.read(name), "epub")
        else:
            raise ValueError("Copyedit supports .txt, .fb2 and .epub")

    def add(self, text, split, target):
        key = str(len(self.records) + 1)
        self.sections[-1].append(key)
        self.records[key] = {"id": key, "text": text, "split": split}
        self.targets[key] = target

    def xml(self, name, raw, fmt):
        doc = self.docs[name] = minidom.parseString(raw)
        self.sections.append([])

        def walk(node):
            tag = node.localName
            if tag in ("section", "h1", "h2"):
                self.sections.append([])
            if tag in ("title", "nav", "pre", "aside", "blockquote", "poem", "table"):
                return
            if tag == "p":
                for txt in text_nodes(node):
                    self.add(txt.data, len(node.childNodes) == 1, (name, node, txt))
            else:
                for child in node.childNodes:
                    if child.nodeType == Node.ELEMENT_NODE:
                        walk(child)

        for body in elements(doc, "body"):
            if fmt != "fb2" or not body.getAttribute("name"):
                walk(body)

    def apply(self, key, value):
        target = self.targets[key]
        if self.ext == ".txt":
            self.parts[target] = re.sub(r"\r?\n", self.newline, value)
            return
        name, p, node = target
        self.changed.add(name)
        paragraphs = value.split("\n\n")
        node.data = paragraphs[0]
        prev = p
        for paragraph in paragraphs[1:]:
            sibling = p.cloneNode(False)
            for attr in ("id", "xml:id", "name"):
                if sibling.hasAttribute(attr):
                    sibling.removeAttribute(attr)
            sibling.appendChild(p.ownerDocument.createTextNode(paragraph))
            p.parentNode.insertBefore(sibling, prev.nextSibling)
            prev = sibling

    def save(self, dest):
        if self.ext == ".txt":
            Path(dest).write_bytes("".join(self.parts).encode(self.encoding))
        elif self.ext == ".fb2":
            Path(dest).write_bytes(self.docs[""].toxml(encoding="utf-8") if self.changed
                                   else self.path.read_bytes())
        else:
            with ZipFile(self.path) as src, ZipFile(dest, "w") as out:
                out.comment = src.comment
                for info in src.infolist():
                    data = (self.docs[info.filename].toxml(encoding="utf-8")
                            if info.filename in self.changed else src.read(info))
                    out.writestr(info, data)
