// Usage: npm i markdown-it && node tools/snapshot.js . "snapshots/YYYY-MM-DD - Project Index.html" YYYY-MM-DD
// Frozen snapshots are records: never edit or republish one after its day.
// Freeze the control center (CONTROL.md + control/*.md) into one self-contained page with real tabs.
const md = require(process.env.MD_IT || 'markdown-it')({ html: true, linkify: true });
const fs = require('fs'), path = require('path');
const [root, out, date] = process.argv.slice(2);
const GH = 'https://github.com/leftbrain847/index/blob/main/';
const todayFile = `snapshots/${date} - Today.md`;
const hasToday = fs.existsSync(path.join(root, todayFile));
const tabs = [...(hasToday ? [['today', todayFile]] : []), ['now', 'CONTROL.md'],
  ...['queue', 'lost-work', 'health', 'sprint', 'toolbox', 'cleanup', 'portfolio', 'guide'].filter(t => fs.existsSync(path.join(root, `control/${t}.md`))).map(t => [t, `control/${t}.md`])];
const label = { today: 'Today', now: 'Now', queue: 'Queue', 'lost-work': 'Lost work', health: 'Health', sprint: 'Sprint', toolbox: 'Toolbox', cleanup: 'Cleanup', portfolio: 'Portfolio', guide: 'Guide', reports: 'Reports' };
const reports = fs.readdirSync(path.join(root, 'reports')).filter(f => f.endsWith('.md')).sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));
const fileToTab = Object.fromEntries(tabs.map(([t, f]) => [f, t]));
const slug = f => 'report-' + f.replace(/\.md$/, '').toLowerCase().replace(/[^a-z0-9]+/g, '-');
function fix(html, file) {
  return html.replace(/href="([^"#:]+)"/g, (m, href) => {
    const p = path.posix.normalize(path.posix.join(path.posix.dirname(file), href));
    if (fileToTab[p]) return `href="#${fileToTab[p]}" data-tab="${fileToTab[p]}"`;
    if (p.startsWith('reports/')) return `href="#${slug(p.slice(8))}" data-tab="reports" data-report="${slug(p.slice(8))}"`;
    return `href="${GH}${p}"`;
  });
}
const counts = {};
const sections = tabs.map(([t, f]) => {
  let src = fs.readFileSync(path.join(root, f), 'utf8').split('\n');
  const bar = src[0]; src = src.slice(1).join('\n');          // drop the markdown tab bar; real tabs replace it
  src = src.replace(/^<sub>Updated [^<]*<\/sub>$/m, '');       // the snapshot banner carries the date instead
  const m = bar.match(new RegExp(`${label[t]} (\\d+)`)); if (m) counts[t] = m[1];
  return `<section id="${t}" class="tab">${fix(md.render(src), f)}</section>`;
}).join('\n') + `<section id="reports" class="tab"><h1>Reports</h1><p>${reports.length} repos · every finding, why it matters, and the prompt.</p>` +
  reports.map(f => {
    const body = fs.readFileSync(path.join(root, 'reports', f), 'utf8').split('\n').slice(1).join('\n').replace(/^# .*$/m, '');
    const head = fs.readFileSync(path.join(root, 'reports', f), 'utf8').match(/^\*\*([A-F—])\*\* · (\d+)/m);
    return `<details class="report" id="${slug(f)}"><summary>${f.replace(/\.md$/, '')}${head ? ` <span class="grade">${head[1]} · ${head[2]}</span>` : ''}</summary>${fix(md.render(body), 'reports/' + f)}</details>`;
  }).join('\n') + `</section>`;
tabs.push(['reports', 'reports/']);
const nav = tabs.map(([t]) => `<a href="#${t}" data-tab="${t}">${label[t]}${counts[t] ? ` <b>${counts[t]}</b>` : ''}</a>`).join('');
const html = `<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>${date} - Project Index</title>
<style>
:root{--bg:#f7f6f3;--fg:#1d1b18;--mut:#77716a;--card:#fff;--bd:#e4e1db;--acc:#2f5fd0;--code:#efede8}
@media(prefers-color-scheme:dark){:root{--bg:#171614;--fg:#f1efeb;--mut:#a29b92;--card:#22201d;--bd:#38352f;--acc:#7ea2ff;--code:#2c2925}}
*{box-sizing:border-box}html{scroll-padding-top:110px}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,sans-serif;padding:env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left)}
header{position:sticky;top:0;z-index:2;background:var(--bg);border-bottom:1px solid var(--bd);padding:10px 14px 0}
.frozen{font-size:12.5px;color:var(--mut);margin-bottom:8px}.frozen b{color:var(--fg)}
nav{display:flex;gap:4px;overflow-x:auto;scrollbar-width:none;padding-bottom:8px}nav::-webkit-scrollbar{display:none}
nav a{flex:none;padding:6px 11px;border-radius:99px;border:1px solid var(--bd);background:var(--card);color:var(--fg);text-decoration:none;font-size:13.5px}
nav a b{font-weight:600;color:var(--mut);margin-left:2px}nav a.on{background:var(--acc);border-color:var(--acc);color:#fff}nav a.on b{color:#fff}
main{max-width:760px;margin:auto;padding:4px 14px 60px}.tab{display:none}.tab.on{display:block}
h1{font-size:22px;margin:14px 0 2px}h3{font-size:15px;margin:22px 0 8px}
a{color:var(--acc)}code{background:var(--code);padding:1px 5px;border-radius:5px;font-size:12.5px}
pre{background:var(--code);padding:10px;border-radius:8px;white-space:pre-wrap;font-size:12.5px}pre code{background:none;padding:0}
details{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:8px 12px;margin:8px 0}
details details{background:var(--bg)}.grade{float:right;color:var(--mut);font-weight:500}details.report>summary{font-weight:600}summary{cursor:pointer;font-weight:600}ul{padding-left:20px}li{margin:3px 0}
table{border-collapse:collapse;font-size:13px}td,th{border:1px solid var(--bd);padding:4px 6px}
</style></head><body>
<header><div class="frozen"><b>${date} · Project Index</b> · frozen at the end of the day · <a href="${GH}CONTROL.md">live command center →</a></div><nav>${nav}</nav></header>
<main>${sections}</main>
<script>
function show(t){document.querySelectorAll('.tab').forEach(s=>s.classList.toggle('on',s.id===t));
document.querySelectorAll('nav a').forEach(a=>a.classList.toggle('on',a.dataset.tab===t));window.scrollTo(0,0)}
document.addEventListener('click',e=>{const a=e.target.closest('a[data-tab]');if(!a)return;e.preventDefault();show(a.dataset.tab);
const r=a.dataset.report&&document.getElementById(a.dataset.report);if(r){r.open=true;r.scrollIntoView();}
try{history.replaceState(null,'','#'+(a.dataset.report||a.dataset.tab))}catch(_){}});
const h=(location.hash||'').slice(1);show(${JSON.stringify(tabs.map(t=>t[0]))}.includes(h)?h:${JSON.stringify(tabs[0][0])});
addEventListener('hashchange',()=>{const h=location.hash.slice(1);if(document.getElementById(h))show(h)});
</script></body></html>`;
fs.writeFileSync(out, html);
console.log('wrote', out, html.length, 'bytes');
