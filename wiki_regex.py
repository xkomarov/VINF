from __future__ import annotations

import re
import html
import unicodedata

TITLE = re.compile(r"<title>(.*?)</title>", re.S)
NS = re.compile(r"<ns>(-?\d+)</ns>")
REDIRECT = re.compile(r'<redirect\s+title="([^"]*)"\s*/?>')
TEXT = re.compile(r"<text\b[^>]*?(?:/>|>(.*?)</text>)", re.S)
PAGE_START = re.compile(r"<page>")

INFOBOX_TYPES = {
    "f1 driver": "driver",
    "racing driver": "driver",
    "le mans driver": "driver",
    "champ car driver": "driver",
    "nascar driver": "driver",
    "wrc driver": "driver",
    "grand prix race report": "grand_prix",
    "f1 race": "grand_prix",
    "motor race": "grand_prix",
    "indy500": "grand_prix",
    "motorsport venue": "circuit",
    "racetrack": "circuit",
    "f1 team": "team",
    "former f1 team": "team",
    "f1 constructor": "team",
    "motor racing team": "team",
    "indycar team": "team",
}
F1_ONLY_TYPES = {
    "person": "driver",
    "sportsperson": "driver",
}
F1_CATEGORY = re.compile(r"\[\[\s*Category\s*:[^\]]*Formula One", re.I)

TEMPLATE_START = re.compile(r"\{\{\s*([^|{}<\n]+)")
INFOBOX_AFFIX = re.compile(r"^infobox\s+|\s+infobox$")
BRACES = re.compile(r"\{\{|\}\}")
SPLIT_TOKENS = re.compile(r"\{\{|\}\}|\[\[|\]\]|\|")
FIELD = re.compile(r"^\s*([\w][\w '\-]*?)\s*=\s*(.*)$", re.S)

COMMENT = re.compile(r"<!--.*?-->", re.S)
REF_SELF = re.compile(r"<ref\b[^>]*/>", re.I)
REF = re.compile(r"<ref\b[^>]*>.*?</ref\s*>", re.S | re.I)
BR = re.compile(r"<br\s*/?>", re.I)
TAG = re.compile(r"</?[a-zA-Z][^>]*>")
FILE_LINK = re.compile(r"\[\[\s*(?:File|Image)\s*:[^\[\]]*(?:\[\[[^\]]*\]\][^\[\]]*)*\]\]", re.I)
LINK = re.compile(r"\[\[(?:[^|\[\]]*\|)?([^|\[\]]*)\]\]")
EXT_LINK = re.compile(r"\[(?:https?:)?//[^\s\]]+\s*([^\]]*)\]")
INNER_TEMPLATE = re.compile(r"\{\{([^{}]*)\}\}")
TABLE = re.compile(r"^\{\|.*?^\|\}", re.S | re.M)
BOLD_ITALIC = re.compile(r"'{2,}")
SPACES = re.compile(r"\s+")
PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
CATEGORY_LINK = re.compile(r"\[\[\s*Category\s*:[^\]]*\]\]", re.I)
LIST_SEPARATOR = re.compile(r"\s*\n\s*[*#]+\s*")

PAREN_SUFFIX = re.compile(r"\s*\([^)]*\)\s*$")
PUNCT = re.compile(r"[^a-z0-9]+")
NAMED_PARAM = re.compile(r"^\s*[\w ]+\s*=")
DATE_TEMPLATES = {"birth date and age", "birth date", "bda", "death date and age", "death date", "dda",
                  "start date", "start date and age", "end date", "dts", "date", "film date"}
LIST_TEMPLATES = {"plainlist", "plain list", "ubl", "unbulleted list", "hlist", "flatlist", "flat list",
                  "bulleted list", "nowrap", "small", "big", "nobr", "sup", "abbr"}
FLAG_TEMPLATES = {"flagicon", "flag", "flagcountry", "flagu", "flagathlete", "flagdeco", "flagicon image"}


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.lower())
    return "".join(c for c in value if not unicodedata.combining(c))


def template_key(name: str) -> str:
    return SPACES.sub(" ", name.replace("_", " ")).strip().lower()


def infobox_key(name: str) -> str:
    return INFOBOX_AFFIX.sub("", template_key(name))


def is_infobox_name(name: str) -> bool:
    key = template_key(name)
    return key.startswith("infobox ") or key.endswith(" infobox")


def parse_page(record: str) -> dict | None:
    start = PAGE_START.search(record)
    if not start:
        return None
    record = record[start.end():]
    title = TITLE.search(record)
    ns = NS.search(record)
    if not title or not ns:
        return None
    redirect = REDIRECT.search(record)
    text = TEXT.search(record)
    return {
        "title": html.unescape(title.group(1)).strip(),
        "ns": int(ns.group(1)),
        "redirect": html.unescape(redirect.group(1)).strip() if redirect else None,
        "text": html.unescape(text.group(1) or "") if text else "",
    }


def matching_braces(text: str, start: int) -> int:
    depth = 0
    for m in BRACES.finditer(text, start):
        depth += 1 if m.group(0) == "{{" else -1
        if depth == 0:
            return m.end()
    return -1


def is_f1_article(text: str) -> bool:
    return bool(F1_CATEGORY.search(text))


def infobox_types(text: str) -> dict:
    return dict(INFOBOX_TYPES, **F1_ONLY_TYPES) if is_f1_article(text) else INFOBOX_TYPES


def find_infobox(text: str, types: dict | None = None) -> tuple[str, str, int] | None:
    types = INFOBOX_TYPES if types is None else types
    for m in TEMPLATE_START.finditer(text):
        name = infobox_key(m.group(1))
        if name not in types:
            continue
        end = matching_braces(text, m.start())
        if end < 0:
            return None
        return name, text[m.end():end - 2], end
    return None


def split_top_level(body: str) -> list[str]:
    parts, depth, last = [], 0, 0
    for m in SPLIT_TOKENS.finditer(body):
        token = m.group(0)
        if token in ("{{", "[["):
            depth += 1
        elif token in ("}}", "]]"):
            depth = max(depth - 1, 0)
        elif depth == 0:
            parts.append(body[last:m.start()])
            last = m.end()
    parts.append(body[last:])
    return parts


def parse_fields(body: str) -> dict:
    fields = {}
    for part in split_top_level(body)[1:]:
        m = FIELD.match(part)
        if m:
            key = template_key(m.group(1)).replace(" ", "_")
            value = m.group(2).strip()
            if value:
                fields[key] = value
    return fields


def template_params(inner: str) -> tuple[str, list[str]]:
    parts = [p.strip() for p in inner.split("|")]
    return template_key(parts[0]), [p for p in parts[1:] if not NAMED_PARAM.match(p)]


def date_from_params(params: list[str]) -> str:
    numbers = [p for p in params if p.isdigit()][:3]
    if len(numbers) == 3:
        return "{}-{:0>2}-{:0>2}".format(*numbers)
    return " ".join(numbers)


def coord_from_params(params: list[str]) -> str:
    values = [p for p in params if re.match(r"^-?\d+(\.\d+)?$|^[NSEW]$", p)]
    if values and not any(v in "NSEW" for v in values):
        return ", ".join(values[:2])
    return " ".join(values)


def render_template(inner: str) -> str:
    name, params = template_params(inner)
    if name in FLAG_TEMPLATES:
        return params[0] if params else ""
    if name in DATE_TEMPLATES:
        return date_from_params(params)
    if name == "convert" and len(params) >= 2:
        return "{} {}".format(params[0], params[1])
    if name in ("coord", "coor"):
        return coord_from_params(params)
    if name in LIST_TEMPLATES:
        return ", ".join(p for p in params if p)
    if name in ("f1", "f1 driver", "f1 team", "f1 constructor") and params:
        return params[0]
    return ""


def render_templates(value: str) -> str:
    while True:
        new_value = INNER_TEMPLATE.sub(lambda m: render_template(m.group(1)), value)
        if new_value == value:
            return value
        value = new_value


def strip_markup(value: str) -> str:
    value = COMMENT.sub("", value)
    value = REF_SELF.sub("", value)
    value = REF.sub("", value)
    value = FILE_LINK.sub("", value)
    value = LINK.sub(lambda m: m.group(1), value)
    value = EXT_LINK.sub(lambda m: m.group(1), value)
    value = BR.sub(", ", value)
    value = TAG.sub("", value)
    return BOLD_ITALIC.sub("", value)


def clean_value(value: str) -> str:
    value = COMMENT.sub("", value)
    value = REF_SELF.sub("", value)
    value = REF.sub("", value)
    value = FILE_LINK.sub("", value)
    value = LINK.sub(lambda m: m.group(1), value)
    value = render_templates(value)
    value = strip_markup(value)
    value = LIST_SEPARATOR.sub(", ", "\n" + value)
    value = SPACES.sub(" ", value.replace("\xa0", " ")).strip(" ,;")
    return re.sub(r",\s*(?:,\s*)+", ", ", value)


def remove_templates(text: str) -> str:
    out, pos, depth, start = [], 0, 0, 0
    for m in BRACES.finditer(text):
        if m.group(0) == "{{":
            if depth == 0:
                out.append(text[pos:m.start()])
            depth += 1
        elif depth > 0:
            depth -= 1
            if depth == 0:
                pos = m.end()
    if depth == 0:
        out.append(text[pos:])
    return "".join(out)


def abstract(text: str, after: int = 0, limit: int = 1000) -> str:
    body = COMMENT.sub("", text[after:])
    body = REF_SELF.sub("", body)
    body = REF.sub("", body)
    body = remove_templates(body)
    body = TABLE.sub("", body)
    body = FILE_LINK.sub("", body)
    body = CATEGORY_LINK.sub("", body)
    for paragraph in PARAGRAPH_SPLIT.split(body):
        paragraph = paragraph.strip()
        if not paragraph or paragraph[0] in "=*#|:;{!_<":
            continue
        cleaned = SPACES.sub(" ", strip_markup(paragraph).replace("\xa0", " ")).strip()
        if len(cleaned) >= 20:
            return cleaned[:limit]
    return ""


def parse_infobox_page(page: dict) -> dict | None:
    types = infobox_types(page["text"])
    found = find_infobox(page["text"], types)
    if not found:
        return None
    name, body, end = found
    raw = parse_fields(body)
    infobox = {k: v for k, v in ((k, clean_value(v)) for k, v in raw.items()) if v}
    return {"title": page["title"], "wiki_type": types[name], "template": name,
            "infobox": infobox, "abstract": abstract(page["text"], end)}


def infobox_names(text: str) -> list[str]:
    known = set(INFOBOX_TYPES) | set(F1_ONLY_TYPES)
    names = []
    for m in TEMPLATE_START.finditer(text):
        if is_infobox_name(m.group(1)) or infobox_key(m.group(1)) in known:
            names.append(infobox_key(m.group(1)))
    return names


def join_key(value: str) -> str:
    value = PAREN_SUFFIX.sub("", normalize(value or "").replace("_", " "))
    return PUNCT.sub(" ", value).strip()
