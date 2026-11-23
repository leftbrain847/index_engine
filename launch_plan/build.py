"""Build the Launch plan page: template.html (here) + DATA/plan.json + DATA/audio/marks.json -> DATA/index.html.

    python3 build.py --data DATA      # DATA = the content folder in the private index repo (launch_plan/)

Run audio.py first for sound; sections without a recording simply get no Listen button.
"""
import json, os, sys
from sections import HERE, data_dir

data = data_dir(sys.argv[1:])
plan = json.load(open(os.path.join(data, "plan.json")))
mp = os.path.join(data, "audio", "marks.json")
rec = json.load(open(mp)) if os.path.exists(mp) else {}
rec = {k: v for k, v in rec.items() if os.path.exists(os.path.join(data, v["file"]))}
dump = lambda o: json.dumps(o, ensure_ascii=False).replace("</", "<\\/")
page = open(os.path.join(HERE, "template.html")).read()
assert "/*PLAN*/null" in page and "/*REC*/{}" in page
page = page.replace("/*PLAN*/null", dump(plan), 1).replace("/*REC*/{}", dump(rec), 1)
open(os.path.join(data, "index.html"), "w").write(page)
print("built index.html with", len(rec), "recorded sections")
