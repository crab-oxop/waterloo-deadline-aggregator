"""Look & feel (v3 "Apple"): theme tokens (light / dark / auto), app shell, small pieces.
Follows Apple's Human Interface Guidelines for macOS: the system font, Apple's system colours (the
increased-contrast variants wherever colour carries text), Liquid Glass only on the navigation layer
(sidebar, toolbar, sheets) with standard opaque materials for content, capsule controls, one accent
fill for primary actions, and honours Reduce Transparency / Reduce Motion."""
from __future__ import annotations

import html
import zlib
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .models import Item

LOCAL_TZ = ZoneInfo("America/Toronto")
esc = html.escape

TYPE_CLASS = {"assignment": "t-work", "manual": "t-work", "quiz": "t-test", "exam": "t-test",
              "meeting": "t-meet", "announcement": "t-meet", "message": "t-meet", "reminder": "t-rem"}
TYPE_LABEL = {"assignment": "Assignment", "quiz": "Quiz", "exam": "Exam", "meeting": "Event",
              "announcement": "Announcement", "message": "Email", "reminder": "Reminder", "manual": "Task"}
SOURCE_LABEL = {"d2l": "LEARN", "crowdmark": "Crowdmark", "odyssey": "Odyssey", "outlook": "Outlook", "manual": "Added by you",
                "gmail": "Gmail", "slack": "Slack", "discord": "Discord", "teams": "Teams", "quest": "Quest",
                "web": "Course page", "gcal": "Google Calendar"}
COMPONENT_LABEL = {"LEC": "Lecture", "TUT": "Tutorial", "LAB": "Lab", "TST": "Test", "SEM": "Seminar",
                   "PRJ": "Project", "DIS": "Discussion", "CLN": "Clinic", "STU": "Studio"}
COURSE_COLOURS = 6

ICONS = {
    "today": '<rect x="3" y="4" width="18" height="17" rx="2"/><path d="M3 10h18M8 2v4M16 2v4"/><circle cx="12" cy="15" r="1.5"/>',
    "cal": '<rect x="3" y="4" width="18" height="17" rx="2"/><path d="M3 10h18M8 2v4M16 2v4"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01"/>',
    "bell": '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.9 1.9 0 0 0 3.4 0"/>',
    "sliders": '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
    "plug": '<path d="M9 2v6M15 2v6M6 8h12v4a6 6 0 0 1-12 0V8zM12 18v4"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
    "auto": '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "shield": '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3z"/><path d="M9 12l2 2 4-4"/>',
    "refresh": '<path d="M20 11a8 8 0 0 0-14.9-3M4 5v4h4M4 13a8 8 0 0 0 14.9 3M20 19v-4h-4"/>',
    "chev_l": '<path d="M15 18l-6-6 6-6"/>',
    "chev_r": '<path d="M9 18l6-6-6-6"/>',
    "plan": '<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2M9 2h6"/>',
    "target": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.5"/>',
    "x": '<path d="M6 6l12 12M18 6L6 18"/>',
    "eyeoff": '<path d="M3 3l18 18M10.6 5.1A10 10 0 0 1 12 5c5 0 9 4.5 10 7-.4 1-1.2 2.3-2.4 3.6M6.6 6.6C4.5 8 3 10.2 2 12c1 2.5 5 7 10 7 1.7 0 3.2-.5 4.6-1.3"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
    "eye": '<path d="M2 12c1-2.5 5-7 10-7s9 4.5 10 7c-1 2.5-5 7-10 7S3 14.5 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "ext": '<path d="M14 4h6v6M20 4l-9 9M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/>',
}

TAB_LABEL = {"/plan": "Plan", "/goals": "Goals", "/connections": "Connect"}  # shorter names for the phone tab bar
NAV = (  # (section, href, label, icon)
    ("Plan", "/today", "Today", "today"),
    ("Plan", "/plan", "Work plan", "plan"),
    ("Plan", "/goals", "Goals", "target"),
    ("Plan", "/calendar", "Calendar", "cal"),
    ("Track", "/", "Deadlines", "list"),
    ("Track", "/reminders", "Reminders", "bell"),
    ("Setup", "/settings", "Courses & weights", "sliders"),
    ("Setup", "/connections", "Connections", "plug"),
)

FONTS = ""  # the system font (SF Pro on a Mac, Segoe UI Variable on Windows); nothing fetched from the web

# Runs before paint so a saved theme never flashes the wrong colours.
THEME_BOOT = ("<script>try{var t=localStorage.getItem('theme');if(t==='light'||t==='dark')"
              "document.documentElement.dataset.theme=t}catch(e){}</script>")
PAGE_JS = """<script>
(function(){
  function current(){try{return localStorage.getItem('theme')||'auto'}catch(e){return 'auto'}}
  function apply(t){
    if(t==='light'||t==='dark'){document.documentElement.dataset.theme=t}else{delete document.documentElement.dataset.theme}
    document.querySelectorAll('[data-set-theme]').forEach(function(b){
      b.setAttribute('aria-pressed', b.dataset.setTheme===t ? 'true' : 'false')});
  }
  document.querySelectorAll('[data-set-theme]').forEach(function(b){
    b.addEventListener('click',function(){var t=b.dataset.setTheme;try{localStorage.setItem('theme',t)}catch(e){}apply(t)})});
  apply(current());
  // Live updates: the server bumps a token whenever data or connections change; the page
  // reloads itself (keeping your scroll position) — but never while you're typing or choosing.
  var meta=document.querySelector('meta[name=state]');
  var key='scroll:'+location.pathname+location.search;
  try{var y=sessionStorage.getItem(key);if(y!==null){sessionStorage.removeItem(key);window.scrollTo(0,+y)}}catch(e){}
  if(meta){var token=meta.content,busy=false;
    setInterval(function(){
      var a=document.activeElement;
      if(busy||document.hidden||document.body.dataset.busy||document.querySelector('dialog[open]')||(a&&/^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)))return;
      if(document.querySelector('details[open].keep'))return;
      fetch('/state',{cache:'no-store'}).then(function(r){return r.text()}).then(function(t){
        if(t&&t!==token){if(window.__softRefresh){token=t;window.__softRefresh();return}busy=true;try{sessionStorage.setItem(key,String(window.scrollY))}catch(e){}location.reload()}
      }).catch(function(){})},2500)}
  // One click = one submit (a slow response used to invite a second click → duplicates).
  document.addEventListener('submit',function(e){var f=e.target;
    if(f.method&&f.method.toLowerCase()==='post'){if(f.dataset.sent){e.preventDefault();return}f.dataset.sent='1'}},true);
  var s=document.getElementById('q');
  document.addEventListener('keydown',function(e){
    if(s && (e.ctrlKey||e.metaKey) && (e.key==='k'||e.key==='K')){e.preventDefault();s.focus();s.select()}});
})();
</script>"""

LIGHT = """--bg:#F5F5F7;--surface:#FFFFFF;--surface2:#F2F2F7;--hover:rgba(0,0,0,.045);--line:#E3E3E8;--line2:#EEEEF2;
--text:#1D1D1F;--text2:#515154;--text3:#86868B;
--accent:#0088FF;--accent-soft:#E6F2FF;--accent-text:#1E6EF4;--on-accent:#FFFFFF;
--ok:#008932;--ok-soft:#E8F8EC;--warn:#C55300;--warn-soft:#FFF3E6;--bad:#E9152D;--bad-soft:#FFEBEC;
--work:#1E6EF4;--work-soft:#E6F2FF;--test:#C55300;--test-soft:#FFF3E6;--meet:#008198;--meet-soft:#E2F8FA;--rem:#A16A00;--rem-soft:#FFF8DB;
--c1:#1E6EF4;--c1s:#E6F2FF;--c2:#008198;--c2s:#E2F8FA;--c3:#C55300;--c3s:#FFF3E6;--c4:#E7124D;--c4s:#FFE9EF;
--c5:#564ADE;--c5s:#EEEDFF;--c6:#008932;--c6s:#E8F8EC;--study:#B02FC2;
--glass:rgba(250,250,252,.72);--glass-edge:rgba(255,255,255,.65);--sheet:rgba(255,255,255,.86);
--shadow:0 1px 2px rgba(0,0,0,.04);--shadow2:0 1px 3px rgba(0,0,0,.08),0 1px 2px rgba(0,0,0,.04);
--float:0 8px 30px rgba(0,0,0,.08),0 1px 3px rgba(0,0,0,.06);"""
DARK = """--bg:#161618;--surface:#1F1F21;--surface2:#2C2C2E;--hover:rgba(255,255,255,.06);--line:#38383A;--line2:#2C2C2E;
--text:#F5F5F7;--text2:#AEAEB2;--text3:#8E8E93;
--accent:#0091FF;--accent-soft:#0B2A47;--accent-text:#5CB8FF;--on-accent:#FFFFFF;
--ok:#4AD968;--ok-soft:#10301A;--warn:#FFA056;--warn-soft:#3A2410;--bad:#FF6165;--bad-soft:#3D1416;
--work:#5CB8FF;--work-soft:#0B2A47;--test:#FFA056;--test-soft:#3A2410;--meet:#3BDDEC;--meet-soft:#0B3237;--rem:#FEDF43;--rem-soft:#3A3208;
--c1:#5CB8FF;--c1s:#0B2A47;--c2:#3BDDEC;--c2s:#0B3237;--c3:#FFA056;--c3s:#3A2410;--c4:#FF8AC4;--c4s:#3D1428;
--c5:#A7AAFF;--c5s:#22224A;--c6:#4AD968;--c6s:#10301A;--study:#EA8DFF;
--glass:rgba(38,38,42,.66);--glass-edge:rgba(255,255,255,.09);--sheet:rgba(40,40,44,.86);
--shadow:0 1px 2px rgba(0,0,0,.4);--shadow2:0 1px 3px rgba(0,0,0,.5);
--float:0 10px 34px rgba(0,0,0,.45),0 1px 3px rgba(0,0,0,.4);"""

CSS = f"""
:root{{{LIGHT}color-scheme:light;--font:-apple-system,BlinkMacSystemFont,'SF Pro Text','Segoe UI Variable Text','Segoe UI',system-ui,sans-serif;--mono:var(--font)}}
:root[data-theme="dark"]{{{DARK}color-scheme:dark}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{{DARK}color-scheme:dark}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 var(--font);-webkit-font-smoothing:antialiased}}
a{{color:var(--accent-text)}}
.mono{{font-family:var(--mono);font-variant-numeric:tabular-nums}}
svg.i{{width:18px;height:18px;flex:none}}
.app{{display:flex;min-height:100vh}}
/* sidebar */
.side{{width:248px;flex:none;background:var(--surface);border-right:1px solid var(--line);display:flex;flex-direction:column;
  padding:16px 12px;gap:2px;position:sticky;top:0;height:100vh}}
.logo{{display:flex;align-items:center;gap:10px;padding:4px 8px 16px;text-decoration:none;color:var(--text)}}
.mark{{width:30px;height:30px;border-radius:8px;background:var(--accent);display:flex;align-items:center;justify-content:center;flex:none;color:var(--on-accent)}}
.logo b{{display:block;font-size:14px;font-weight:600;letter-spacing:-.01em}} .logo div span{{display:block;font-size:12px;color:var(--text3)}}
.mark svg{{stroke-width:2.6}}
.sec{{font-size:11px;font-weight:500;letter-spacing:.06em;text-transform:uppercase;color:var(--text3);padding:14px 10px 6px}}
.nav{{display:flex;align-items:center;gap:10px;height:36px;padding:0 10px;border-radius:8px;color:var(--text2);text-decoration:none;font-weight:500}}
.nav svg{{color:var(--text3)}} .nav:hover{{background:var(--hover);color:var(--text)}}
.nav[aria-current="page"]{{background:var(--surface2);color:var(--text)}} .nav[aria-current="page"] svg{{color:var(--accent)}}
.cnt{{margin-left:auto;font:12px var(--mono);color:var(--text3)}}
.alert{{margin-left:auto;min-width:18px;height:18px;padding:0 5px;border-radius:9px;background:var(--warn-soft);color:var(--warn);font-size:11px;font-weight:600;display:flex;align-items:center;justify-content:center}}
.foot{{margin-top:auto;display:flex;flex-direction:column;gap:10px}}
.syncbox{{border:1px solid var(--line);border-radius:10px;padding:10px 12px;display:flex;gap:10px;align-items:center;font-size:12px;color:var(--text2);text-decoration:none}}
.syncbox .g{{width:8px;height:8px;border-radius:4px;background:var(--ok);flex:none;box-shadow:0 0 0 3px var(--ok-soft)}}
.syncbox.warn .g{{background:var(--warn);box-shadow:0 0 0 3px var(--warn-soft)}}
.syncbox b{{display:block;color:var(--text);font-weight:500;font-size:13px}}
.themes{{display:flex;gap:2px;padding:3px;border-radius:9px;background:var(--surface2)}}
.themes button{{flex:1;height:30px;border:0;border-radius:7px;background:transparent;color:var(--text3);display:flex;align-items:center;
  justify-content:center;gap:6px;font:500 12px var(--font);cursor:pointer}}
.themes button svg{{width:15px;height:15px}}
.themes button[aria-pressed="true"]{{background:var(--surface);color:var(--text);box-shadow:var(--shadow2)}}
/* top bar + content */
.main{{flex:1;min-width:0;display:flex;flex-direction:column}}
.top{{height:64px;flex:none;display:flex;align-items:center;gap:12px;padding:0 28px;border-bottom:1px solid var(--line);background:var(--surface);
  position:sticky;top:0;z-index:4}}
.top h1{{font-size:18px;font-weight:600;letter-spacing:-.01em;margin:0;white-space:nowrap}}
.top .dt{{color:var(--text3);font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.search{{margin-left:auto;display:flex;align-items:center;gap:8px;width:280px;height:36px;padding:0 10px;border:1px solid var(--line);border-radius:8px;
  color:var(--text3);background:var(--bg)}}
.search input{{border:0;background:transparent;flex:1;min-width:0;height:34px;outline:none;font:400 13px var(--font);color:var(--text);padding:0}}
.search:focus-within{{outline:2px solid var(--accent);outline-offset:1px}}
.kbd{{font:11px var(--mono);border:1px solid var(--line);border-radius:5px;padding:1px 5px;color:var(--text3);background:var(--surface)}}
.avatar{{width:32px;height:32px;border-radius:16px;background:var(--surface2);color:var(--text2);font-weight:600;font-size:12px;display:flex;
  align-items:center;justify-content:center;border:1px solid var(--line);flex:none}}
.content{{padding:24px 28px 48px;display:flex;flex-direction:column;gap:20px;max-width:1400px;width:100%}}
/* controls */
.btn{{height:36px;padding:0 14px;border-radius:8px;border:1px solid var(--line);background:var(--surface);color:var(--text);font:500 13px var(--font);
  display:inline-flex;align-items:center;gap:6px;box-shadow:var(--shadow);cursor:pointer;white-space:nowrap;text-decoration:none}}
.btn svg{{width:16px;height:16px}} .btn:hover{{background:var(--hover)}}
.btn.pri{{background:var(--accent);border-color:var(--accent);color:var(--on-accent)}} .btn.pri:hover{{filter:brightness(1.08)}}
.btn.sm{{height:30px;padding:0 10px;font-size:12px}}
.btn:focus-visible,.nav:focus-visible,a:focus-visible,button:focus-visible{{outline:2px solid var(--accent);outline-offset:2px}}
select,input,textarea{{font:400 13px var(--font);color:var(--text);background:var(--surface);border:1px solid var(--line);border-radius:8px}}
select{{height:34px;padding:0 8px}}
input[type=text],input[type=password],input[type=date],input[type=time],input[type=number]{{height:36px;padding:0 12px}}
textarea{{padding:10px 12px;width:100%}}
select:focus,input:focus,textarea:focus{{outline:2px solid var(--accent);outline-offset:1px}}
/* cards */
.card{{background:var(--surface);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow);overflow:hidden}}
.ch{{display:flex;align-items:center;gap:10px;padding:14px 18px;border-bottom:1px solid var(--line2)}}
.ch h2{{font-size:14px;font-weight:600;margin:0}} .ch .s{{color:var(--text3);font-size:12px}}
.ch .r{{margin-left:auto;display:flex;gap:8px;align-items:center}}
.ch.hot h2{{color:var(--bad)}}
.link{{color:var(--accent-text);text-decoration:none;font-weight:500;font-size:13px}} .link:hover{{text-decoration:underline}}
.empty{{padding:16px 18px;color:var(--text3)}}
.flag{{display:flex;gap:10px;align-items:center;padding:12px 16px;border-radius:10px;background:var(--warn-soft);color:var(--text);border:1px solid color-mix(in srgb,var(--warn) 25%,transparent)}}
.flag::before{{content:"";width:8px;height:8px;border-radius:4px;background:var(--warn);flex:none}}
.flag.good{{background:var(--ok-soft);border-color:color-mix(in srgb,var(--ok) 25%,transparent)}} .flag.good::before{{background:var(--ok)}}
/* chips, badges, priority */
.chip{{display:inline-flex;align-items:center;gap:5px;height:20px;padding:0 7px;border-radius:6px;font-size:11px;font-weight:600;white-space:nowrap}}
.chip::before{{content:"";width:6px;height:6px;border-radius:3px;background:currentColor}}
.k1{{background:var(--c1s);color:var(--c1)}} .k2{{background:var(--c2s);color:var(--c2)}} .k3{{background:var(--c3s);color:var(--c3)}}
.k4{{background:var(--c4s);color:var(--c4)}} .k5{{background:var(--c5s);color:var(--c5)}} .k6{{background:var(--c6s);color:var(--c6)}}
.kn{{background:var(--surface2);color:var(--text2)}}
.badge{{display:inline-flex;align-items:center;gap:6px;height:22px;padding:0 8px;border-radius:11px;font-size:12px;font-weight:500;white-space:nowrap}}
.badge::before{{content:"";width:6px;height:6px;border-radius:3px;background:currentColor}}
.b-ok{{background:var(--ok-soft);color:var(--ok)}} .b-warn{{background:var(--warn-soft);color:var(--warn)}} .b-bad{{background:var(--bad-soft);color:var(--bad)}}
.b-mute{{background:var(--surface2);color:var(--text3)}} .b-acc{{background:var(--accent-soft);color:var(--accent-text)}}
.pbar{{display:inline-flex;align-items:center;gap:8px;font-size:12px;font-weight:500}}
.pbar i{{display:flex;gap:2px}} .pbar i b{{width:4px;height:12px;border-radius:2px;background:var(--line)}}
.pbar.p-high i b{{background:var(--bad)}} .pbar.p-med i b:nth-child(-n+2){{background:var(--warn)}} .pbar.p-low i b:first-child{{background:var(--text3)}}
.pbar.p-high{{color:var(--bad)}} .pbar.p-med{{color:var(--warn)}} .pbar.p-low,.pbar.p-done{{color:var(--text3)}}
.tdot{{width:8px;height:8px;border-radius:4px;flex:none;background:var(--work)}}
.tdot.t-work{{background:var(--work)}} .tdot.t-test{{background:var(--test)}} .tdot.t-meet{{background:var(--meet)}} .tdot.t-rem{{background:var(--rem)}}
/* tick (mark done) */
.tickf{{margin:0;display:contents}}
.tick{{width:32px;height:32px;border:0;background:transparent;padding:0;display:inline-flex;align-items:center;justify-content:center;cursor:pointer;border-radius:8px}}
.tick:hover{{background:var(--hover)}} .tick:hover .cb{{border-color:var(--accent)}}
.cb{{width:18px;height:18px;border-radius:5px;border:1.5px solid var(--text3);display:inline-block;position:relative}}
.done .cb,.cb.on{{background:var(--accent);border-color:var(--accent)}}
.done .cb::after,.cb.on::after{{content:"";position:absolute;left:5px;top:1px;width:5px;height:10px;border:solid var(--on-accent);border-width:0 2px 2px 0;transform:rotate(45deg)}}
/* stats */
.stats{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}}
.stat{{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px 16px;box-shadow:var(--shadow)}}
.stat .l{{font-size:12px;color:var(--text3);font-weight:500}}
.stat .v{{font-size:26px;font-weight:600;letter-spacing:-.02em;margin-top:4px;display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}}
.stat .v small{{font-size:12px;font-weight:500;color:var(--text3);letter-spacing:0}} .stat .v small.up{{color:var(--warn)}} .stat .v small.bad{{color:var(--bad)}}
.grid2{{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(0,1fr);gap:20px;align-items:start}}
.col{{display:flex;flex-direction:column;gap:20px;min-width:0}}
/* timeline */
.tl{{position:relative;margin:8px 18px 14px 72px}}
.hour{{position:absolute;left:-58px;font-size:11px;color:var(--text3);transform:translateY(-50%)}}
.hline{{position:absolute;left:-6px;right:0;border-top:1px dashed var(--line2)}}
.blk{{position:absolute;left:0;right:0;border-radius:8px;padding:5px 10px;display:flex;flex-direction:column;gap:1px;overflow:hidden;
  border:1px solid transparent}}
.blk b{{font-size:13px;font-weight:600}} .blk span{{font-size:12px;opacity:.85}}
.blk.k1{{border-color:color-mix(in srgb,var(--c1) 25%,transparent)}} .blk.k2{{border-color:color-mix(in srgb,var(--c2) 25%,transparent)}}
.blk.k3{{border-color:color-mix(in srgb,var(--c3) 25%,transparent)}} .blk.k4{{border-color:color-mix(in srgb,var(--c4) 25%,transparent)}}
.blk.k5{{border-color:color-mix(in srgb,var(--c5) 25%,transparent)}} .blk.k6{{border-color:color-mix(in srgb,var(--c6) 25%,transparent)}}
.blk.past{{opacity:.55}}
.tdue{{position:absolute;left:0;right:0;display:flex;align-items:center;gap:8px;font-size:12px;font-weight:500;color:var(--text2);
  transform:translateY(-50%);background:var(--surface);padding-right:6px}}
.tdue i{{width:10px;height:10px;border-radius:3px;transform:rotate(45deg);background:var(--warn);flex:none}}
.tdue.done{{color:var(--text3);text-decoration:line-through}} .tdue.done i{{background:var(--text3)}}
.now{{position:absolute;left:-12px;right:0;border-top:2px solid var(--bad);z-index:2}}
.now::before{{content:"";position:absolute;left:-5px;top:-6px;width:10px;height:10px;border-radius:5px;background:var(--bad)}}
.now span{{position:absolute;left:-60px;top:-9px;font:600 11px var(--mono);color:var(--bad);background:var(--surface);padding:0 2px}}
/* list items */
.dayg{{padding:10px 18px 4px;font-size:12px;font-weight:600;color:var(--text3);display:flex;justify-content:space-between}}
.it{{display:flex;align-items:center;gap:10px;padding:6px 18px;min-height:40px;color:inherit;text-decoration:none}}
a.it:hover{{background:var(--hover)}}
.it .t{{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:500}}
.it .tm{{font-size:12px;color:var(--text3);white-space:nowrap}}
.row{{display:grid;grid-template-columns:32px minmax(0,1fr) auto auto;gap:12px;align-items:center;padding:6px 18px 6px 12px;min-height:56px;
  border-top:1px solid var(--line2)}}
.row:first-of-type{{border-top:0}}
.row .title{{font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.row .title a{{color:inherit;text-decoration:none}} .row .title a:hover{{color:var(--accent-text);text-decoration:underline}}
.row .meta{{font-size:12px;color:var(--text3);display:flex;align-items:center;gap:8px;margin-top:2px}}
.row.done .title{{text-decoration:line-through;color:var(--text3)}}
.when{{text-align:right;white-space:nowrap}} .when b{{display:block;font:500 13px var(--mono)}} .when span{{font-size:12px;color:var(--text3)}}
.when.late b,.when.late span{{color:var(--bad)}}
/* toolbar + table */
.toolbar{{display:flex;align-items:center;gap:10px;flex-wrap:wrap}}
.tabs{{display:inline-flex;gap:2px;padding:3px;border-radius:9px;background:var(--surface2)}}
.tabs a{{height:30px;padding:0 12px;border-radius:7px;display:flex;align-items:center;gap:6px;color:var(--text2);text-decoration:none;font-weight:500;font-size:13px}}
.tabs a span{{font:11px var(--mono);color:var(--text3)}}
.tabs a[aria-current="true"]{{background:var(--surface);color:var(--text);box-shadow:var(--shadow2)}}
.fsel{{height:34px;border:1px dashed var(--line);background:transparent;color:var(--text2);font-weight:500;border-radius:8px;padding:0 8px}}
.tblwrap{{overflow-x:auto}}
.tbl{{width:100%;border-collapse:collapse}}
.tbl th{{text-align:left;font-size:12px;font-weight:500;color:var(--text3);padding:10px 12px;border-bottom:1px solid var(--line);background:var(--surface2);white-space:nowrap}}
.tbl td{{padding:0 12px;height:52px;border-bottom:1px solid var(--line2);white-space:nowrap}}
.tbl tr:last-child td{{border-bottom:0}}
.tbl tbody tr:not(.grp):hover td{{background:var(--hover)}}
.tbl tr.grp td{{height:34px;background:var(--bg);font-size:12px;font-weight:600;color:var(--text3)}}
.tbl tr.done td{{color:var(--text3)}} .tbl tr.done .ttl,.tbl tr.done .ttl a{{text-decoration:line-through;color:var(--text3)}}
.tbl .ttl{{font-weight:500;color:var(--text);overflow:hidden;text-overflow:ellipsis;display:block}}
.tbl tr.muted td{{opacity:.55}}.tbl tr.muted td:last-child{{opacity:1}}.eyebtn{{border:0;background:none;color:var(--text3);cursor:pointer;padding:4px;border-radius:6px;display:inline-flex}}.eyebtn:hover{{color:var(--text);background:var(--hover,rgba(127,127,127,.12))}}.eyebtn svg{{width:16px;height:16px}}.rowact{{display:flex;gap:6px;align-items:center}}.hidnote{{display:flex;gap:10px;align-items:center;font-size:13px;color:var(--text2);margin:-4px 0 12px}}.hidnote svg{{width:15px;height:15px}}.why{{font-size:11px;color:var(--text3);border:1px solid var(--line,rgba(127,127,127,.3));border-radius:99px;padding:0 6px;margin-left:6px}}.tbl td.tcell{{max-width:0;width:44%}}
.tbl .ttl a{{color:inherit;text-decoration:none}} .tbl .ttl a:hover{{color:var(--accent-text);text-decoration:underline}}
.tbl .num{{text-align:right}}
.rel{{display:block;color:var(--text3);font-size:12px;line-height:1.3}} .rel.soon{{color:var(--warn)}} .rel.late{{color:var(--bad)}}
.tbl select{{height:30px;font-size:12px}}
/* reminders */
.quick{{display:flex;gap:8px;padding:6px;border-radius:12px;background:var(--surface);border:1px solid var(--line);box-shadow:var(--shadow);flex-wrap:wrap;align-items:center}}
.quick input.big{{flex:1;min-width:220px;height:40px;border:0;background:transparent;font-size:14px}}
.quick input[type=date],.quick input[type=time]{{height:40px;background:var(--surface2);border:0}}
.quick .btn{{height:40px}}
.more{{color:var(--text3)}} .more summary{{cursor:pointer;padding:4px 2px;font-size:13px}}
.more .fields{{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:8px}}
.rgrp{{font-size:12px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:var(--text3);padding:14px 2px 8px;display:flex;gap:8px}}
.rgrp.hot{{color:var(--bad)}} .rgrp .n{{font-family:var(--mono);opacity:.8}}
.rem{{display:flex;gap:8px;align-items:flex-start;padding:10px 14px 10px 8px;background:var(--surface);border:1px solid var(--line);border-radius:10px;
  margin-bottom:8px;box-shadow:var(--shadow)}}
.rem .body{{padding-top:5px;min-width:0}} .rem .title{{font-weight:500}}
.rem .meta{{font-size:12px;color:var(--text3);margin-top:2px}}
.rem.done .title{{text-decoration:line-through;color:var(--text3)}}
.note{{font-size:13px;color:var(--text2);white-space:pre-line;margin-top:4px}}
details.done-list summary{{cursor:pointer;color:var(--text3);padding:14px 2px 8px;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.04em}}
/* calendar */
.legend{{display:flex;gap:16px;font-size:13px;color:var(--text3);align-items:center;flex-wrap:wrap}}
.legend span{{display:flex;align-items:center;gap:6px}}
.cal{{display:grid;grid-template-columns:repeat(7,minmax(0,1fr))}}
.dow{{font-size:12px;font-weight:500;color:var(--text3);padding:10px 12px;border-bottom:1px solid var(--line);background:var(--surface2)}}
.day{{padding:8px;border-right:1px solid var(--line2);border-bottom:1px solid var(--line2);min-height:116px;display:flex;flex-direction:column;gap:4px;min-width:0}}
.day:nth-child(7n+7){{border-right:0}}
.day.other{{background:var(--bg)}} .day.other .dn{{color:var(--text3);opacity:.6}}
.dn{{font:500 12px var(--mono);color:var(--text2);display:flex;align-items:center;gap:6px}}
.day.today .dn b{{color:var(--on-accent);background:var(--accent);min-width:24px;height:24px;border-radius:12px;display:inline-flex;
  align-items:center;justify-content:center;font-weight:600}}
.dn .wd{{display:none}}
.ev{{display:flex;align-items:center;gap:6px;font-size:12px;padding:3px 6px;border-radius:6px;white-space:nowrap;overflow:hidden;color:var(--text);
  text-decoration:none;font-weight:500}}
.ev.t-work{{background:var(--work-soft)}} .ev.t-test{{background:var(--test-soft)}} .ev.t-meet{{background:var(--meet-soft)}} .ev.t-rem{{background:var(--rem-soft)}}
.ev .et{{overflow:hidden;text-overflow:ellipsis}} .ev .tm{{color:var(--text3);font:11px var(--mono)}}
.ev.past,.ev.done{{opacity:.5}} .ev.done .et{{text-decoration:line-through}}
.day details summary{{font-size:12px;color:var(--accent-text);cursor:pointer;padding:0 6px;font-weight:500}}
/* settings */
.set-row{{display:grid;grid-template-columns:110px minmax(0,1fr) 130px 130px;gap:12px;align-items:center;padding:8px 18px;border-top:1px solid var(--line2);min-height:52px}}
.set-row.headrow{{min-height:0;font-size:12px;font-weight:500;color:var(--text3);background:var(--surface2);border-top:0}}
.set-row input{{width:100%}} .set-row .title{{font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.inline{{display:flex;align-items:center;gap:6px}}
.wb{{display:flex;align-items:center;gap:10px;padding:8px 12px 8px 18px;min-height:48px;border-top:1px solid var(--line2)}}.wb .when{{width:118px;flex:none;font-size:12px;color:var(--text2)}}.wb .t{{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:500}}.wb .acts{{display:flex;gap:4px;flex:none}}.wb form{{margin:0}}.icobtn{{width:32px;height:32px;display:inline-flex;align-items:center;justify-content:center;border-radius:8px;border:1px solid var(--line);background:var(--surface);color:var(--text2);cursor:pointer}}.icobtn:hover{{background:var(--hover);color:var(--text)}}.icobtn svg{{width:16px;height:16px}}.icobtn.ok{{color:var(--ok)}}.wb.sug .t{{color:var(--text2)}}.short{{display:flex;gap:10px;align-items:flex-start;padding:10px 18px;border-top:1px solid var(--line2);font-size:13px}}.short b{{font-weight:600}}.short .tdot{{margin-top:6px;background:var(--bad)}}.week{{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:10px}}.day{{background:var(--surface);border:1px solid var(--line);border-radius:12px;min-width:0;display:flex;flex-direction:column}}.day h3{{margin:0;padding:10px 12px;font-size:13px;font-weight:600;border-bottom:1px solid var(--line2);display:flex;justify-content:space-between;gap:6px}}.day h3 span{{color:var(--text3);font-weight:400;font-size:12px}}.day.today h3{{color:var(--accent-text)}}.pe{{margin:6px 8px 0;padding:6px 8px;border-radius:8px;font-size:12px;line-height:1.35}}.pe .tm{{font-family:var(--mono);font-size:11px;color:var(--text3);display:block}}.pe.busy{{background:var(--surface2);color:var(--text3)}}.pe.sug{{border:1px dashed color-mix(in srgb,var(--accent) 55%,transparent);color:var(--text)}}.pe.acc{{background:var(--accent-soft);color:var(--accent-text);border:1px solid color-mix(in srgb,var(--accent) 30%,transparent)}}.pe.clash{{border-color:var(--bad)}}.pe .row2{{display:flex;gap:4px;margin-top:4px}}.pe .row2 form{{margin:0}}.pe .icobtn{{width:26px;height:26px}}.pe .icobtn svg{{width:14px;height:14px}}.day .end{{height:8px}}@media (max-width:1000px){{.week{{grid-template-columns:1fr}}.wb .when{{width:96px}}}}.pgwrap{{padding:0;overflow:hidden}}.pgrid{{--px:.8;position:relative;overflow:auto;max-height:calc(100vh - 230px);min-height:340px;overscroll-behavior:contain}}.pg-head,.pg-body{{display:grid;grid-template-columns:52px repeat(var(--days),minmax(118px,1fr));min-width:calc(52px + var(--days) * 118px)}}.pg-head{{position:sticky;top:0;z-index:6;background:var(--surface);border-bottom:1px solid var(--line)}}.pg-corner{{position:sticky;left:0;z-index:7;background:var(--surface)}}.pg-dh{{padding:10px 10px 8px;display:flex;justify-content:space-between;gap:6px;font-size:13px;border-left:1px solid var(--line2)}}.pg-dh span{{color:var(--text3);font-size:12px}}.pg-dh.today b{{color:var(--accent-text)}}.pg-times,.pg-col{{position:relative;height:calc((var(--g1) - var(--g0)) * var(--px) * 1px)}}.pg-times{{position:sticky;left:0;z-index:5;background:var(--surface)}}.pg-hr,.pg-sleep,.pg-now,.pgi{{position:absolute;top:calc((var(--s) - var(--g0)) * var(--px) * 1px)}}.pg-hr{{left:0;right:0;border-top:1px solid var(--line2)}}.pg-hr span{{position:absolute;top:-8px;right:6px;font-size:10px;color:var(--text3);font-family:var(--mono);background:var(--surface);padding:0 2px}}.pg-col{{border-left:1px solid var(--line2);touch-action:pan-x pan-y;cursor:crosshair;background-image:linear-gradient(to bottom,var(--line2) 1px,transparent 1px);background-size:100% calc(60 * var(--px) * 1px)}}.pg-col.today{{background-color:color-mix(in srgb,var(--accent) 4%,transparent)}}.pg-sleep{{left:0;right:0;height:calc((var(--e) - var(--s)) * var(--px) * 1px);background:repeating-linear-gradient(135deg,var(--surface2) 0 6px,transparent 6px 12px);opacity:.9;pointer-events:none}}.pg-now{{left:0;right:0;height:0;border-top:2px solid var(--bad);z-index:3;pointer-events:none}}.pgi{{height:max(calc((var(--e) - var(--s)) * var(--px) * 1px - 2px),12px);left:calc(var(--lane,0) * 100% / var(--lanes,1) + 2px);width:calc(100% / var(--lanes,1) - 4px);border-radius:8px;overflow:hidden;container-type:size;font-size:12px;line-height:1.3;transition:top .16s ease,height .16s ease,left .16s ease,width .16s ease,transform .22s ease,opacity .2s ease;will-change:transform}}.pgi.dragging{{transition:none;z-index:8;box-shadow:0 8px 22px rgba(0,0,0,.28);opacity:.96;left:2px;width:calc(100% - 4px)}}.pgi.fresh{{opacity:0}}.pgrid.instant .pgi{{transition:none}}.pgi{{margin:0;padding:0}}.pbi{{position:absolute;inset:0;padding:4px 7px;display:flex;flex-direction:column;gap:1px;overflow:hidden}}.pbi>*{{flex-shrink:0}}.pbi .tm{{font-family:var(--mono);font-size:10.5px;opacity:.8}}.pbi .lb{{font-weight:500;overflow:hidden;text-overflow:ellipsis}}.pbi .st{{font-size:10.5px;opacity:.75}}.pbi .row2{{display:flex;gap:4px;margin-top:3px}}.pbi .row2 form{{margin:0}}.pbi .icobtn{{width:24px;height:24px}}.pbi .icobtn svg{{width:13px;height:13px}}@container (max-height: 99px){{.pbi .st,.pbi .row2{{display:none}}}}@container (max-height: 34px){{.pbi{{flex-direction:row;align-items:center;gap:5px;padding:1px 6px}}.pbi .tm,.pbi .lb{{white-space:nowrap}}.pbi .lb{{min-width:0}}}}.pe.busy{{background:var(--surface2);color:var(--text3);cursor:default}}.pb{{cursor:grab;user-select:none;z-index:1;touch-action:none}}.pb:active{{cursor:grabbing}}.pb:focus-visible{{outline:2px solid var(--accent);outline-offset:1px}}.pb.sug{{background:var(--surface);border:1px dashed color-mix(in srgb,var(--accent) 60%,transparent);color:var(--text)}}.pb.acc{{background:var(--accent-soft);color:var(--accent-text);border:1px solid color-mix(in srgb,var(--accent) 35%,transparent)}}.pb.own{{background:color-mix(in srgb,var(--ok) 14%,var(--surface));color:var(--text);border:1px solid color-mix(in srgb,var(--ok) 35%,transparent)}}.pb.ghost{{opacity:.85;border-style:dashed}}.pb.ghost.past{{background:color-mix(in srgb,var(--bad) 12%,var(--surface));border-color:var(--bad);color:var(--bad)}}.pb.clash{{border-color:var(--bad);border-style:solid}}.hdl{{position:absolute;left:0;right:0;height:8px;cursor:ns-resize;z-index:2}}.hdl[data-edge=top]{{top:-1px}}.hdl[data-edge=bottom]{{bottom:-1px}}.pb:hover .hdl::after,.pb:focus-within .hdl::after{{content:'';position:absolute;left:50%;width:22px;margin-left:-11px;height:3px;border-radius:2px;background:currentColor;opacity:.45;top:3px}}.nudge{{border-color:color-mix(in srgb,var(--accent) 35%,var(--line));background:color-mix(in srgb,var(--accent) 6%,var(--surface))}}.nudge-in{{display:flex;align-items:center;gap:14px;padding:14px 18px;flex-wrap:wrap}}.nudge-ic{{width:36px;height:36px;border-radius:10px;display:flex;align-items:center;justify-content:center;background:var(--accent-soft);color:var(--accent-text);flex:none}}.nudge-ic svg{{width:18px;height:18px}}.nudge-tx{{flex:1;min-width:220px}}.nudge-h{{font-weight:600;font-size:14px}}.nudge-b{{color:var(--text2);font-size:13px;margin-top:2px}}.nudge-b b{{color:var(--text)}}.goal{{display:flex;gap:14px;align-items:center;padding:12px 18px;border-top:1px solid var(--line2)}}.goal.paused{{opacity:.6}}.gmain{{flex:1;min-width:0}}.gtop{{display:flex;gap:8px;align-items:center;flex-wrap:wrap}}.gmeta{{font-size:12px;color:var(--text2);margin:3px 0 7px}}.gacts{{display:flex;gap:6px}}.gacts form{{margin:0}}.gbar{{height:6px;border-radius:3px;background:var(--surface2);display:flex;overflow:hidden;max-width:420px}}.gbar i{{background:var(--ok)}}.gbar b{{background:color-mix(in srgb,var(--accent) 45%,transparent)}}.gsug{{display:flex;gap:10px;align-items:center;padding:8px 18px;border-top:1px solid var(--line2);margin:0}}.gsug .t{{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:500}}.gsug .tm{{font-size:12px;color:var(--text3)}}.pb.study.sug{{border-color:color-mix(in srgb,var(--study) 60%,transparent)}}.pb.study.acc{{background:color-mix(in srgb,var(--study) 16%,var(--surface));color:var(--text);border-color:color-mix(in srgb,var(--study) 40%,transparent)}}.dsrc{{cursor:grab;user-select:none;touch-action:none}}.dsrc:active{{cursor:grabbing}}.dsrc:focus-visible{{outline:2px solid var(--accent);outline-offset:-2px}}.short.dsrc:hover{{background:var(--hover)}}.short .grip{{width:22px;height:22px;flex:none;border-radius:6px;display:inline-flex;align-items:center;justify-content:center;background:var(--accent-soft);color:var(--accent-text);margin-top:1px}}.short .grip svg{{width:13px;height:13px}}.dhint{{color:var(--text3);font-size:12px}}.tray{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 12px;font-size:13px}}.tray-l{{color:var(--text2)}}.dchip{{display:inline-flex;gap:6px;align-items:center;padding:5px 10px;border-radius:99px;border:1px dashed color-mix(in srgb,var(--study) 55%,transparent);background:color-mix(in srgb,var(--study) 8%,var(--surface))}}.dchip:hover{{background:color-mix(in srgb,var(--study) 16%,var(--surface))}}.dfloat{{position:fixed;left:0;top:0;z-index:50;pointer-events:none;padding:6px 10px;border-radius:8px;font-size:12px;background:var(--accent);color:var(--on-accent);box-shadow:0 8px 20px rgba(0,0,0,.25);max-width:260px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.fstart{{max-width:640px}}.fform{{padding:20px;display:flex;flex-direction:column;gap:14px}}.fhead{{display:flex;gap:10px;align-items:center}}.fhead h2{{margin:0;font-size:20px}}.fmeta{{color:var(--text2);font-size:13px}}.fdl{{font-size:13px;color:var(--text);background:var(--surface2);border-radius:8px;padding:8px 10px}}.fgoal{{display:flex;flex-direction:column;gap:6px;font-size:12px;color:var(--text2)}}.fgoal input{{height:44px;font-size:16px}}.fphone{{font-size:14px;display:flex;gap:8px;align-items:center}}.ftogs{{display:flex;flex-wrap:wrap;gap:8px 16px}}.ftog{{display:flex;gap:6px;align-items:center;font-size:14px}}.ftog span{{color:var(--text3);font-size:12px}}.ftog.off{{color:var(--text3)}}.facts{{display:flex;gap:8px;flex-wrap:wrap;align-items:center}}.btn.big{{height:42px;padding:0 18px;font-size:15px}}.fsess{{display:flex;flex-direction:column;gap:12px;align-items:center;text-align:center;margin:0 auto;padding:12px 0}}.fsess>:not(.fs-tw){{max-width:620px}}.fs-tw{{align-self:stretch;height:min(62vh,52vw);min-height:90px;display:flex;align-items:center;justify-content:center;overflow:hidden}}.fs-top{{display:flex;gap:8px;align-items:center;justify-content:center;flex-wrap:wrap}}.fs-title{{font-weight:600;font-size:18px}}.fs-goal{{color:var(--text2);font-size:14px}}.fs-timer{{font-family:var(--mono);font-size:96px;white-space:nowrap;font-weight:500;letter-spacing:-.02em;line-height:1;color:var(--text);font-variant-numeric:tabular-nums;transition:color .4s ease}}.fs-timer.warn{{color:var(--warn)}}.fs-timer.over{{color:var(--bad)}}.fs-timer.brk{{color:var(--text3)}}.fs-sub{{color:var(--text3);font-size:13px;margin-top:-4px}}.fs-dl{{font-size:13px;color:var(--text2);background:var(--surface2);border-radius:8px;padding:8px 12px}}.fs-actions{{display:flex;gap:8px;flex-wrap:wrap;justify-content:center}}.fs-f{{margin:0}}.fqs{{display:flex;flex-direction:column;gap:8px;align-items:center}}.fqs[hidden],.fsess [hidden]{{display:none!important}}.fq-h{{font-weight:600}}.fq-b{{display:flex;gap:8px;flex-wrap:wrap;justify-content:center}}.fq-b form{{margin:0}}.fs-claude{{width:100%;text-align:left;font-size:13px}}.fs-claude summary{{cursor:pointer;color:var(--text2)}}.fs-claude textarea{{width:100%;margin:8px 0;font:12px/1.4 var(--mono)}}body.focus-win{{display:block;height:100vh;padding:12px;background:var(--bg);overflow:auto}}.fsess.compact{{height:100%;min-height:360px;padding:0;gap:8px}}.fsess.compact .fs-tw{{flex:1;height:auto;min-height:70px}}.prep .nudge-in{{gap:10px}}.prep-h{{position:relative}}.prep-h summary{{list-style:none}}.prep-h summary::-webkit-details-marker{{display:none}}.prep-f{{position:absolute;right:0;top:calc(100% + 6px);z-index:9;display:flex;gap:6px;align-items:center;padding:10px;background:var(--surface);border:1px solid var(--line);border-radius:10px;box-shadow:0 8px 22px rgba(0,0,0,.18);font-size:13px;white-space:nowrap}}.prep-f input{{width:72px;height:32px}}.exam-cd{{display:flex;gap:10px;align-items:center;padding:10px 14px;margin:0 0 14px;border:1px solid var(--line);border-radius:12px;background:var(--surface);font-size:13px;color:var(--text2)}}.exam-cd svg{{width:16px;height:16px;color:var(--accent-text);flex:none}}.exam-cd span:first-of-type{{flex:1}}.exam-cd b{{color:var(--text)}}.exam-cd .mono{{font-size:12px;color:var(--text3)}}.dbf{{width:min(520px,calc(100vw - 32px))}}.dbf textarea{{width:100%;font:inherit;font-size:14px}}.dbf .hint{{font-size:12px;color:var(--text3)}}.zoom{{display:flex;align-items:center;gap:4px}}.zoom .btn{{min-width:30px;justify-content:center}}.zoom span{{font-size:12px;color:var(--text2);min-width:40px;text-align:center}}@media (prefers-reduced-motion: reduce){{.pgi{{transition:none}}}}.pdlg{{border:1px solid var(--line);border-radius:14px;background:var(--surface);color:var(--text);padding:0;width:min(420px,calc(100vw - 32px))}}.pdlg::backdrop{{background:rgba(15,20,30,.45)}}.pdlg form{{padding:18px;display:flex;flex-direction:column;gap:12px}}.pdlg h2{{margin:0;font-size:16px}}.pdlg .itm{{color:var(--text2);font-size:13px;margin-top:-6px}}.pdlg label{{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--text2)}}.pdlg input{{height:36px}}.pdlg .two{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}.pdlg .hint{{margin:-4px 0 0;font-size:12px;color:var(--text3)}}.pdlg .acts{{display:flex;gap:8px;align-items:center}}.pdlg [hidden]{{display:none!important}}.planset{{display:flex;flex-wrap:wrap;gap:14px 20px;align-items:flex-end;padding:14px 18px}}.planset label{{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--text2)}}.planset input{{height:34px}}.savebar{{position:sticky;bottom:0;padding:14px 0;background:linear-gradient(transparent,var(--bg) 35%);display:flex;gap:12px;align-items:center}}
/* connections */
.hero{{display:flex;gap:18px;align-items:center;padding:20px;flex-wrap:wrap}}
.hero .ic{{width:48px;height:48px;border-radius:12px;background:var(--accent-soft);color:var(--accent-text);display:flex;align-items:center;justify-content:center;flex:none}}
.hero .ic svg{{width:24px;height:24px}}
.hero h3{{margin:0;font-size:15px;font-weight:600}} .hero p{{margin:2px 0 0;color:var(--text3)}}
.steps{{display:flex;gap:18px;font-size:12px;color:var(--text3);margin-top:8px;flex-wrap:wrap}}
.steps .ok{{color:var(--ok)}} .steps .cur{{color:var(--accent-text);font-weight:600}}
.prog{{display:flex;align-items:center;gap:10px;padding:12px 16px;border-top:1px solid var(--line2);background:var(--accent-soft);color:var(--accent-text);font-weight:500}}
.spin{{width:16px;height:16px;border-radius:50%;border:2px solid currentColor;border-right-color:transparent;flex:none;animation:spin 1s linear infinite}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
@media (prefers-reduced-motion:reduce){{.spin{{animation:none}}}}
.results{{display:flex;flex-direction:column;gap:8px;padding:0 20px 16px}}
.conn{{display:grid;grid-template-columns:44px minmax(0,1fr) auto auto;gap:16px;align-items:center;padding:14px 20px;border-top:1px solid var(--line2)}}
.conn:first-of-type{{border-top:0}}
.tile{{width:44px;height:44px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:16px}}
.conn b{{display:block;font-weight:600}} .conn .d{{color:var(--text3);font-size:13px}}
.linkform{{display:flex;gap:8px;flex-wrap:wrap;padding:14px 20px}}
.linkform input{{flex:1;min-width:240px}}
.help{{font-size:13px;color:var(--text3);padding:0 20px 16px;line-height:1.5}}
.center{{max-width:600px}}
/* phone tabs */
.tabbar{{display:none}}
@media (max-width:1180px){{
  .grid2{{grid-template-columns:minmax(0,1fr)}} .stats{{grid-template-columns:repeat(2,minmax(0,1fr))}}
  .search{{width:200px}} .hide-md{{display:none}}
}}
@media (max-width:1000px){{ .hide-lt{{display:none}} }}
@media (max-width:1000px) and (min-width:761px){{
  .side{{width:68px;padding:16px 10px}} .logo{{padding:4px 0 16px;justify-content:center}} .logo div:last-child,.sec,.nav .lbl,.syncbox div{{display:none}}
  .nav{{justify-content:center;padding:0;position:relative}} .cnt,.alert{{position:absolute;top:-2px;right:-2px;margin:0;font-size:10px}}
  .syncbox{{justify-content:center;padding:10px 0}} .themes{{flex-direction:column}} .themes button span{{display:none}}
}}
@media (max-width:760px){{
  .side,.search,.avatar,.top .btn{{display:none}}
  .top{{padding:0 16px}} .content{{padding:16px 16px 96px}}
  .stats{{grid-template-columns:repeat(2,minmax(0,1fr))}} .hide-sm{{display:none}}
  .row{{grid-template-columns:32px minmax(0,1fr) auto;padding-right:12px}} .row .prio-cell{{display:none}}
  .set-row{{grid-template-columns:minmax(0,1fr) 90px 90px}} .set-row .due{{display:none}}
  .cal{{display:block}} .dow,.day.other,.day.blank{{display:none}}
  .day{{min-height:0;border-right:0;padding:10px 12px}} .dn .wd{{display:inline}} .ev{{white-space:normal}}
  .conn{{grid-template-columns:44px minmax(0,1fr);}} .conn>*:nth-child(n+3){{grid-column:2}}
  .tabbar{{display:grid;grid-template-columns:repeat(8,minmax(0,1fr));position:fixed;left:0;right:0;bottom:0;height:64px;
    background:var(--surface);border-top:1px solid var(--line);z-index:5}}
  .tab{{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;color:var(--text3);text-decoration:none;font-size:10px;font-weight:500;min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}}
  .tab[aria-current="page"]{{color:var(--accent-text)}}
}}
"""
CSS += r"""
/* ---- Apple layer (HIG, macOS) ------------------------------------------------------------ */
/* Numbers use the system font with tabular (fixed-width) digits, not a code font */
*{font-variant-numeric:tabular-nums}
.mono{font-family:var(--font);letter-spacing:0}
body{font-size:13px;line-height:1.4;letter-spacing:-.003em;font-feature-settings:"ss01"}
h1,h2,h3,b,strong{letter-spacing:-.01em}
/* Sidebar: Liquid Glass, floating inset beside the content (navigation layer) */
.side{margin:8px 0 8px 8px;height:calc(100vh - 16px);top:8px;border-radius:18px;border:1px solid var(--glass-edge);
  background:var(--glass);-webkit-backdrop-filter:blur(30px) saturate(180%);backdrop-filter:blur(30px) saturate(180%);
  box-shadow:var(--float);padding:14px 10px}
.logo b{font-size:13px;font-weight:700} .mark{border-radius:9px}
.sec{font-size:11px;font-weight:600;letter-spacing:0;text-transform:none;color:var(--text3);padding:14px 10px 4px}
.nav{height:32px;border-radius:9px;color:var(--text);font-weight:500;transition:background-color .15s ease}
.nav svg{color:var(--accent)}  /* sidebar icons take the app accent colour */
.nav:hover{background:var(--hover)}
.nav[aria-current="page"]{background:color-mix(in srgb,var(--accent) 16%,transparent);color:var(--text)}
.cnt{color:var(--text3)}
.syncbox{border:0;background:color-mix(in srgb,var(--surface) 55%,transparent);border-radius:12px}
.themes{border-radius:999px;background:color-mix(in srgb,var(--text) 7%,transparent)} .themes button{border-radius:999px}
/* Toolbar: Liquid Glass over scrolling content */
.top{background:color-mix(in srgb,var(--bg) 72%,transparent);-webkit-backdrop-filter:blur(24px) saturate(180%);
  backdrop-filter:blur(24px) saturate(180%);border-bottom:1px solid color-mix(in srgb,var(--line) 70%,transparent);height:60px}
.top h1{font-size:22px;font-weight:700;letter-spacing:-.02em}
.search{border-radius:999px;border:0;background:color-mix(in srgb,var(--text) 6%,transparent);height:32px}
.search:focus-within{outline:0;box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 40%,transparent)}
.kbd{border-radius:6px;border-color:transparent;background:color-mix(in srgb,var(--text) 7%,transparent)}
.avatar{border:0}
/* Controls: capsules; colour only on primary actions */
.btn{height:32px;padding:0 14px;border-radius:999px;border:0;background:color-mix(in srgb,var(--text) 7%,transparent);box-shadow:none;
  font-weight:500;transition:background-color .15s ease,transform .1s ease,filter .15s ease}
.btn:hover{background:color-mix(in srgb,var(--text) 11%,transparent)}
.btn:active{transform:scale(.97)}
.btn.pri{background:var(--accent);color:var(--on-accent);box-shadow:0 1px 2px color-mix(in srgb,var(--accent) 35%,transparent)}
.btn.pri:hover{filter:brightness(1.07)}
.btn.sm{height:28px;padding:0 11px}
.btn.big{height:40px;padding:0 20px}
.btn:focus-visible,.nav:focus-visible,a:focus-visible,button:focus-visible{outline:0;box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 45%,transparent)}
select,input,textarea{border-radius:8px;border:1px solid var(--line);background:var(--surface);transition:box-shadow .15s ease,border-color .15s ease}
select:focus,input:focus,textarea:focus{outline:0;border-color:var(--accent);box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 30%,transparent)}
input[type=checkbox]{accent-color:var(--accent)}
/* Content: standard opaque material, soft corners */
.card,.stat{border-radius:14px;border:1px solid var(--line2);box-shadow:0 1px 2px rgba(0,0,0,.03),0 0 0 .5px rgba(0,0,0,.02)}
.ch{border-bottom:1px solid var(--line2);padding:13px 18px}
.ch h2{font-size:15px;font-weight:700;letter-spacing:-.01em}
.stat .v{font-size:28px;font-weight:700;letter-spacing:-.025em}
.flag{border-radius:12px;border:0}
.chip{border-radius:999px;height:20px;padding:0 8px}
.chip::before{display:none}
.badge{border-radius:999px}
/* Mark done: round like Reminders */
.cb{width:20px;height:20px;border-radius:50%;border:1.5px solid color-mix(in srgb,var(--text3) 80%,transparent);transition:background-color .15s ease,border-color .15s ease}
.done .cb::after,.cb.on::after{left:6px;top:2px}
/* Sheets and popovers: glass */
.pdlg{border-radius:20px;border:1px solid var(--glass-edge);background:var(--sheet);
  -webkit-backdrop-filter:blur(40px) saturate(180%);backdrop-filter:blur(40px) saturate(180%);box-shadow:var(--float)}
.pdlg::backdrop{background:rgba(0,0,0,.25)}
.pdlg h2,.dbf h2{font-size:17px;font-weight:700}
.prep-f{border-radius:14px;border:1px solid var(--glass-edge);background:var(--sheet);-webkit-backdrop-filter:blur(30px);backdrop-filter:blur(30px)}
/* Work plan blocks: a touch softer */
.pgi{border-radius:9px}
.pb.acc{box-shadow:inset 3px 0 0 var(--accent)}
/* Focus timer: SF-style numerals */
.fs-timer{font-family:var(--font);font-weight:300;letter-spacing:-.04em}
/* Phone tab bar: glass */
@media (max-width:760px){
  .tabbar{background:var(--glass);-webkit-backdrop-filter:blur(30px) saturate(180%);backdrop-filter:blur(30px) saturate(180%);
    border-top:1px solid var(--glass-edge)}
  .tab[aria-current="page"]{color:var(--accent)}
}
@media (max-width:1000px) and (min-width:761px){ .side{width:64px} }
/* Accessibility settings: Reduce Transparency → solid surfaces; Reduce Motion → no movement */
@media (prefers-reduced-transparency: reduce){
  .side,.top,.pdlg,.prep-f,.tabbar{background:var(--surface);-webkit-backdrop-filter:none;backdrop-filter:none}
}
@media (prefers-reduced-motion: reduce){
  *{transition:none!important;animation:none!important}
  .btn:active{transform:none}
}
"""


def icon(name: str, cls: str = "i") -> str:
    return (f'<svg class="{cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICONS[name]}</svg>')


def shell(title: str, body: str, active: str, counts: dict[str, int] | None = None, head_extra: str = "",
          heading: str = "", subtitle: str = "", sync: tuple[str, str, bool] | None = None, term: str = "",
          query: str = "", state: str = "") -> str:
    """The app frame: grouped sidebar with a sync footer, a top bar (title, search, new reminder), content."""
    counts = counts or {}
    side, tabs, last_sec = [], [], None
    for sec, href, label, ic in NAV:
        if sec != last_sec:
            side.append(f'<div class="sec">{esc(sec)}</div>')
            last_sec = sec
        cur = ' aria-current="page"' if href == active else ""
        n = counts.get(href)
        badge = ""
        if n:
            badge = f'<span class="alert">{n}</span>' if href == "/connections" else f'<span class="cnt">{n}</span>'
        side.append(f'<a class="nav" href="{href}"{cur} title="{esc(label)}">{icon(ic)}<span class="lbl">{esc(label)}</span>{badge}</a>')
        short = TAB_LABEL.get(href) or label.split(" &")[0].split()[0]  # phone tab bar: one short word
        tabs.append(f'<a class="tab" href="{href}"{cur}>{icon(ic)}{esc(short)}</a>')
    sync_title, sync_detail, ok = sync or ("Not synced yet", "Open Connections", False)
    foot = (f'<div class="foot"><a class="syncbox{"" if ok else " warn"}" href="/connections"><span class="g"></span>'
            f'<div><b>{esc(sync_title)}</b>{esc(sync_detail)}</div></a>'
            '<div class="themes" role="group" aria-label="Colour theme">'
            f'<button type="button" data-set-theme="light" aria-pressed="false">{icon("sun")}<span>Light</span></button>'
            f'<button type="button" data-set-theme="dark" aria-pressed="false">{icon("moon")}<span>Dark</span></button>'
            f'<button type="button" data-set-theme="auto" aria-pressed="true">{icon("auto")}<span>Auto</span></button></div></div>')
    logo = (f'<a class="logo" href="/today"><span class="mark">{icon("check")}</span>'
            f'<div><b>Waterloo Deadlines</b><span>{esc(term)}</span></div></a>')
    top = (f'<header class="top"><h1>{esc(heading or title.split(" — ")[0])}</h1><span class="dt">{esc(subtitle)}</span>'
           f'<form class="search" method="get" action="/" role="search">{icon("search")}'
           f'<input id="q" name="q" value="{esc(query)}" placeholder="Search deadlines, courses…" aria-label="Search">'
           '<span class="kbd">Ctrl K</span></form>'
           f'<a class="btn pri" href="/reminders#add">{icon("plus")}New reminder</a><div class="avatar" aria-hidden="true">EL</div></header>')
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{esc(title)}</title>'
        + (f'<meta name="state" content="{esc(state)}">' if state else '')
        + f'{head_extra}{THEME_BOOT}{FONTS}<style>{CSS}</style></head><body>'
        f'<div class="app"><nav class="side" aria-label="Main">{logo}{"".join(side)}{foot}</nav>'
        f'<div class="main">{top}<main class="content">{body}</main></div></div>'
        f'<nav class="tabbar" aria-label="Main">{"".join(tabs)}</nav>{PAGE_JS}</body></html>')


# --- small pieces ----------------------------------------------------------------

def colour_class(context: str | None) -> str:
    if not context:
        return "kn"
    return f"k{zlib.crc32(context.encode()) % COURSE_COLOURS + 1}"  # stable: same course, same colour everywhere


def course_chip(context: str | None) -> str:
    return f'<span class="chip {colour_class(context)}">{esc(context)}</span>' if context else ""


def type_class(kind: str) -> str:
    return TYPE_CLASS.get(kind, "t-work")


def local(dt: datetime) -> datetime:
    return dt.astimezone(LOCAL_TZ)


def clock(dt: datetime) -> str:
    """'4:05 PM'; 11:59 PM reads as 'end of day' everywhere."""
    l = local(dt)
    return "end of day" if (l.hour, l.minute) == (23, 59) else l.strftime("%I:%M %p").lstrip("0")


def short_date(dt: datetime) -> str:
    l = local(dt)
    return f"{l:%a %b} {l.day}"


def full_due(dt: datetime | None) -> str:
    return f"{short_date(dt)}, {clock(dt)}" if dt else "No date"


def tick_form(item: Item, next_path: str) -> str:
    """Checkbox-style tick: marks done (or reopens a done item)."""
    to = "not_started" if item.status == "done" else "done"
    label = "Mark not done" if item.status == "done" else "Mark done"
    return (f'<form class="tickf" method="post" action="/status"><input type="hidden" name="id" value="{item.id}">'
            f'<input type="hidden" name="status" value="{to}"><input type="hidden" name="next" value="{esc(next_path)}">'
            f'<button class="tick" title="{label}" aria-label="{label}: {esc(item.title)}"><span class="cb"></span></button></form>')


def title_html(item: Item, cls: str = "title") -> str:
    t = esc(item.title)
    if item.source_link:
        t = f'<a href="{esc(item.source_link)}" target="_blank" rel="noopener">{t}</a>'
    return f'<div class="{cls}">{t}</div>'


def meta_html(item: Item, extra: str = "") -> str:
    kind = TYPE_LABEL.get(item.type, item.type.capitalize())
    src = SOURCE_LABEL.get(item.source, item.source)
    if item.extra.get("room") or item.extra.get("seat"):
        extra = " · ".join(x for x in (extra, item.extra.get("location", "")) if x)
    tail = f" · {extra}" if extra else ""
    return (f'<div class="meta">{course_chip(item.context)}<span>{esc(kind)} · {esc(src)}{esc(tail)}</span></div>')


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
