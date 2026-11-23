"""The Launch plan's sections and their spoken text: one source for audio.py and build.py.

A section is a heading with Listen: the rules, each phase, each repeating group, and upkeep (its three groups
in one). Each item is one step (or rule) with a key the page uses to find its mark.
"""
import html, json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))   # the tooling (public index_engine)


def data_dir(argv):
    """The content folder (private index repo's launch_plan/): --data DIR, else the current directory."""
    return os.path.abspath(argv[argv.index("--data") + 1] if "--data" in argv else ".")


def load_plan(data):
    return json.load(open(os.path.join(data, "plan.json")))


def sections(plan):
    out = [{"id": "rules", "title": "The rules",
            "items": [{"key": f"r{i}", "t": r["t"], "b": r["b"]} for i, r in enumerate(plan["rules"])]}]
    for i, p in enumerate(plan["phases"]):
        out.append({"id": p["id"], "title": f"{i + 1}. {p['name']}", "items": [{"key": s["id"], "t": s["t"], "b": s["b"]} for s in p["steps"]]})
    for g in plan["repeats"]:
        out.append({"id": g["id"], "title": g["name"], "items": [{"key": s["id"], "t": s["t"], "b": s["b"]} for s in g["items"]]})
    out.append({"id": "upkeep", "title": "Upkeep",
                "items": [{"key": s["id"], "t": f"{g['name']}: {s['t']}", "b": s["b"]} for g in plan["upkeep"] for s in g["items"]]})
    return out


# Words the voice would otherwise read badly. Applied after tags are stripped.
SAY = [
    (r"\$(\d+)\+", r"\1 dollars or more"), (r"/mo\b", " a month"), (r"\best\.", "estimated"),
    (r"(\d)k\b", r"\1 thousand"), (r"(\d)\+", r"\1 or more"), ("×", " times "), ("→", ", then "),
    ("–", " to "), ("_", " "), (r"\?v=", "question mark v equals "), ("e.g.", "for example"),
    ("D-U-N-S", "D U N S"), ("2FA", "two-factor"), ("JSON", "jason"), ("UAT", "U A T"),
]


def text_of(fragment):
    """Spoken text from an item's HTML: blocks and list items end as sentences, cells as commas, tags go."""
    s = re.sub(r"</(li|p|div|tr|h\d)>", ". ", fragment, flags=re.I)
    s = re.sub(r"</t[dh]>", ", ", s, flags=re.I)
    s = re.sub(r"<br\s*/?>", ". ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    for pat, rep in SAY:
        s = re.sub(pat, rep, s) if any(ch in pat for ch in "\\()[]+?") else s.replace(pat, rep)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r",\s*\.", ".", s)
    s = re.sub(r"([.:;!?])\s*\.(\s|$)", r"\1\2", s)
    s = re.sub(r"\s+([.,;:!?])", r"\1", s)
    return s.strip()


def item_text(it):
    """What one item says: its title, then its full text."""
    return (text_of(it["t"]).rstrip(".") + ". " + text_of(it["b"])).replace("..", ".")
