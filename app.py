"""
BJOC - All In One Dashboard  ·  Streamlit
=========================================
Each tab in the Google Sheet is a metric named ``<Metric>-<Function>``
(function = FC, MH, or anything you add). Staged navigation:

    1. Two big buttons (FC / MH ...) — nothing selected by default.
    2. Click one  -> it shrinks, the function's metric buttons animate in.
    3. Click a metric -> buttons shrink small, the table animates in.

Date-based tables also get Overall / Week / Month views (latest date first).
Tables reproduce the sheet faithfully (colours, merges) and use the sheet's
own freeze settings (nothing is frozen by default). New tabs/functions added
to the sheet appear automatically (live read + auto-refresh) — no code change.

Run:  streamlit run app.py
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import data_loader as dl

# Laser/highlight mode: injected into the parent page so it works on the table
# rendered by st.markdown. Hover = laser-point a cell; double-click then move the
# cursor = paint a highlight region; click to lock it. Gated by the `.laser-on`
# body class so toggling off makes all handlers no-ops and clears highlights.
# url() wrapper uses single quotes and the SVG attribute quotes are %22-encoded,
# so the whole value contains NO double quotes and is safe inside a JS "..." string.
_LASER_CUR = ("url('data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 "
              "width=%2226%22 height=%2226%22%3E%3Ccircle cx=%2213%22 cy=%2213%22 r=%226%22 "
              "fill=%22%23ff2d2d%22 fill-opacity=%220.85%22/%3E%3Ccircle cx=%2213%22 cy=%2213%22 "
              "r=%2211%22 fill=%22none%22 stroke=%22%23ff2d2d%22 stroke-opacity=%220.45%22 "
              "stroke-width=%222%22/%3E%3C/svg%3E') 13 13, crosshair")


def inject_laser(enabled: bool) -> None:
    js = (_LASER_JS
          .replace("__ENABLED__", "true" if enabled else "false")
          .replace("__CUR__", _LASER_CUR))
    components.html(f"<script>{js}</script>", height=0)


_LASER_JS = r"""
const doc = window.parent.document;
const ENABLED = __ENABLED__;
const CUR = "__CUR__";
// Stack multi-row frozen headers: give each thead row a cumulative sticky `top`
// so ALL frozen header rows stay pinned (not just the first). Run repeatedly and
// on every layout change so it can't be beaten by late reflow.
const pwin = doc.defaultView || window.parent;
// Dynamic box height: each table box grows with its data down to the window
// bottom, then scrolls inside; a short table keeps the box as tall as the data
// (no empty space). Recomputed on every layout change via stackHeaders().
function sizeTable(){
  // The table box fills down to the window bottom so there is NO empty space below
  // it (short tables just show empty rows inside the box). A fixed margin keeps the
  // height stable after the first paint; the page never scrolls, so the banner,
  // metric title and sticky header stay put and only the body rows scroll inside.
  doc.querySelectorAll('.sheet-wrap').forEach(w=>{
    const wtop = w.getBoundingClientRect().top;
    const avail = Math.round(Math.max(180, (pwin.innerHeight || 800) - wtop - 4));
    w.style.height = avail + 'px';
    w.style.maxHeight = avail + 'px';
  });
}
function makeColsResizable(){
  // Excel-style column resizing: drag ANY header cell's right edge to resize its
  // column. Data columns resize via an injected <colgroup>; FROZEN (sticky-left)
  // label columns resize by adjusting their pinned cell widths and recomputing every
  // frozen column's sticky `left` so they stay aligned when scrolled horizontally.
  // Widths persist per-table in sessionStorage so they survive Streamlit reruns.
  doc.querySelectorAll('table.sheet').forEach(t=>{
    if(t.__resizable) return;
    const thead = t.tHead; if(!thead || !thead.rows.length) return;
    t.__resizable = true;

    // map the table into a column grid (handles rowspan/colspan); tag start col + span
    const grid=[];
    [...t.rows].forEach((tr,r)=>{
      if(!grid[r]) grid[r]=[];
      let c=0;
      for(const cell of tr.cells){
        while(grid[r][c]) c++;
        const rs=cell.rowSpan||1, csp=cell.colSpan||1;
        cell.__sc=c; cell.__cs=csp;
        for(let dr=0;dr<rs;dr++){ if(!grid[r+dr]) grid[r+dr]=[]; for(let dc=0;dc<csp;dc++) grid[r+dr][c+dc]=cell; }
        c+=csp;
      }
    });
    let ncol=0; grid.forEach(row=>{ if(row.length>ncol) ncol=row.length; });

    // frozen cells = those with an inline sticky `left` (set by the renderer)
    const frozenCells=[], frozenSet=new Set();
    for(const cell of t.querySelectorAll('th,td')){
      if(cell.style.left!==''){ frozenCells.push(cell); for(let k=0;k<cell.__cs;k++) frozenSet.add(cell.__sc+k); }
    }
    const frozenCols = frozenSet.size ? Math.max(...frozenSet)+1 : 0;

    const cg=doc.createElement('colgroup');
    for(let i=0;i<ncol;i++) cg.appendChild(doc.createElement('col'));
    t.insertBefore(cg, t.firstChild);
    const cols=[...cg.children];

    // starting width of each column (measure a single-span cell in that column)
    const colW=[];
    for(let i=0;i<ncol;i++){ let w=0;
      for(const row of grid){ const cell=row[i]; if(cell && cell.__sc===i && cell.__cs===1){ w=Math.round(cell.getBoundingClientRect().width); break; } }
      colW[i]= w || (frozenSet.has(i)?130:80);
    }
    const sig='cw:'+[...thead.rows[thead.rows.length-1].cells].map(x=>(x.textContent||'').trim()).join('|').slice(0,120);

    function applyFrozen(){
      if(!frozenCells.length) return;
      const leftOf=[]; let acc=0; for(let i=0;i<frozenCols;i++){ leftOf[i]=acc; acc+=colW[i]; }
      for(const cell of frozenCells){ let wsum=0; for(let k=0;k<cell.__cs;k++) wsum+=colW[cell.__sc+k]||0;
        cell.style.left=(leftOf[cell.__sc]||0)+'px';
        cell.style.width=wsum+'px'; cell.style.minWidth=wsum+'px'; cell.style.maxWidth=wsum+'px'; }
    }
    const applyData=i=>{ cols[i].style.width=colW[i]+'px'; };

    // restore saved widths (leave everything else at its natural size)
    let frozenSaved=false;
    for(let i=0;i<ncol;i++){ let sv=null; try{ sv=sessionStorage.getItem(sig+'#'+i); }catch(e){}
      if(sv){ colW[i]=parseFloat(sv); if(frozenSet.has(i)) frozenSaved=true; else applyData(i); } }
    if(frozenSaved) applyFrozen();

    // add a resize grip to every cell of the last header row (frozen AND data)
    for(const cell of thead.rows[thead.rows.length-1].cells){
      const colIdx=Math.min(cell.__sc+cell.__cs-1, ncol-1);
      const grip=doc.createElement('div'); grip.className='colgrip'; cell.appendChild(grip);
      grip.addEventListener('mousedown', e=>{
        e.preventDefault(); e.stopPropagation();
        const startX=e.clientX, startW=colW[colIdx], frozen=frozenSet.has(colIdx);
        doc.body.classList.add('col-resizing');
        const move=ev=>{ colW[colIdx]=Math.max(36, startW+(ev.clientX-startX)); if(frozen) applyFrozen(); else applyData(colIdx); };
        const up=()=>{ doc.removeEventListener('mousemove',move,true); doc.removeEventListener('mouseup',up,true);
          doc.body.classList.remove('col-resizing');
          try{ sessionStorage.setItem(sig+'#'+colIdx, colW[colIdx]); }catch(e){} };
        doc.addEventListener('mousemove',move,true); doc.addEventListener('mouseup',up,true);
      });
      grip.addEventListener('click', e=>e.stopPropagation());
    }
  });
}
function stackHeaders(){
  doc.querySelectorAll('table.sheet').forEach(t=>{
    const th = t.tHead; if(!th) return;
    let top = 0;
    for(const tr of th.rows){
      const h = tr.getBoundingClientRect().height;
      for(const cell of tr.cells){ cell.style.top = top + 'px'; }
      if(h) top += h;
    }
  });
  sizeTable();
  makeColsResizable();
}
stackHeaders();
if(pwin.requestAnimationFrame) pwin.requestAnimationFrame(stackHeaders);
[100, 350, 800, 1600, 3000].forEach(ms => setTimeout(stackHeaders, ms));
// re-stack on clicks (tab switches), window resize, and any table size change
if(doc.__stackH) doc.removeEventListener('click', doc.__stackH, true);
doc.__stackH = ()=> setTimeout(stackHeaders, 60);
doc.addEventListener('click', doc.__stackH, true);
if(doc.__stackR) pwin.removeEventListener('resize', doc.__stackR);
doc.__stackR = ()=> setTimeout(stackHeaders, 60);
pwin.addEventListener('resize', doc.__stackR);
try{
  if(doc.__stackRO) doc.__stackRO.disconnect();
  doc.__stackRO = new (pwin.ResizeObserver || ResizeObserver)(()=> stackHeaders());
  doc.querySelectorAll('table.sheet, .sheet-wrap').forEach(el => doc.__stackRO.observe(el));
}catch(e){}
// style (idempotent)
let s = doc.getElementById('laser-style');
if(!s){ s = doc.createElement('style'); s.id='laser-style'; doc.head.appendChild(s); }
s.textContent = `
  body.laser-on table.sheet tbody td, body.laser-on table.sheet tbody th { cursor: default !important; }
  body.laser-on table.sheet { user-select:none; -webkit-user-select:none; }
  body.laser-on table.sheet td:hover, body.laser-on table.sheet th:hover {
      outline: 3px solid #ff2d2d; outline-offset:-3px; }
  table.sheet td.laser-cur, table.sheet th.laser-cur {
      box-shadow: inset 0 0 0 9999px rgba(255,45,45,.22); }
  table.sheet td.laser-keep, table.sheet th.laser-keep {
      box-shadow: inset 0 0 0 9999px rgba(255,45,45,.22), inset 0 0 0 2px #ff2d2d; }
  /* Excel-style column resize grip on the right edge of each header cell */
  table.sheet .colgrip { position:absolute; top:0; right:-3px; width:8px; height:100%;
      cursor:col-resize; z-index:6; }
  body.col-resizing, body.col-resizing * { cursor:col-resize !important;
      user-select:none !important; -webkit-user-select:none !important; }
`;
// Delegated handlers on the document, replaced each run so they never go stale
// when Streamlit recreates this component iframe. Press-drag-release rubber band.
if(doc.__laser){
  doc.removeEventListener('mousedown', doc.__laser.md, true);
  doc.removeEventListener('mousemove', doc.__laser.mm, true);
  doc.removeEventListener('mouseup',   doc.__laser.mu, true);
  doc.removeEventListener('dblclick',  doc.__laser.db, true);
}
let sel=false, ax=0, ay=0, tbl=null;
const on = ()=> doc.body.classList.contains('laser-on');
function mark(x0,y0,x1,y1){
  const L=Math.min(x0,x1),R=Math.max(x0,x1),T=Math.min(y0,y1),B=Math.max(y0,y1);
  tbl.querySelectorAll('td,th').forEach(c=>{
    const r=c.getBoundingClientRect();
    c.classList.toggle('laser-cur', r.left<R && r.right>L && r.top<B && r.bottom>T);
  });
}
const md = e=>{ if(!on() || e.button!==0) return; const td=e.target.closest('td,th');
  if(!td || td.closest('thead')) return;   // headers are for sorting, not highlighting
  const t=td.closest('table.sheet'); if(!t) return;
  sel=true; tbl=t; ax=e.clientX; ay=e.clientY; mark(ax,ay,ax,ay); e.preventDefault(); };
const mm = e=>{ if(on() && sel && tbl){ mark(ax,ay,e.clientX,e.clientY); e.preventDefault(); } };
const mu = e=>{ if(on() && sel && tbl){ sel=false;
  tbl.querySelectorAll('.laser-cur').forEach(c=>{ c.classList.remove('laser-cur'); c.classList.add('laser-keep'); }); } };
const db = e=>{ if(!on() || e.target.closest('thead')) return;   // dbl-click body clears highlights
  doc.querySelectorAll('.laser-cur,.laser-keep').forEach(c=>c.classList.remove('laser-cur','laser-keep'));
  e.preventDefault(); };
doc.addEventListener('mousedown', md, true);
doc.addEventListener('mousemove', mm, true);
doc.addEventListener('mouseup',   mu, true);
doc.addEventListener('dblclick',  db, true);
doc.__laser = {md, mm, mu, db};

// Click a header on a sortable table to sort by that column (1st click = descending,
// click again = ascending). Blanks always sort to the bottom.
function _colIndexOf(th){ let col=0; for(const c of th.parentElement.cells){ if(c===th) break; col += c.colSpan||1; } return col; }
function _sortTable(t, col, dir){
  const tb=t.tBodies[0]; if(!tb) return;
  const num=s=>{ const n=parseFloat(String(s).replace(/[,\s%]/g,'')); return isNaN(n)?null:n; };
  const val=r=> (col<r.cells.length ? r.cells[col].textContent.trim() : '');
  [...tb.rows].sort((a,b)=>{
    const sa=val(a), sb=val(b);
    if(!sa && !sb) return 0; if(!sa) return 1; if(!sb) return -1;
    const na=num(sa), nb=num(sb);
    const c = (na!==null && nb!==null) ? na-nb : sa.localeCompare(sb, undefined, {numeric:true});
    return dir==='desc' ? -c : c;
  }).forEach(r=> tb.appendChild(r));
}
const sortClick = e=>{
  if(e.target.closest('.colgrip')) return;   // resizing a column, not sorting
  const th=e.target.closest('th'); if(!th || !th.closest('thead')) return;
  const t=th.closest('table.sheet'); if(!t || !t.dataset.sortable) return;
  const col=_colIndexOf(th);
  const dir=(t.__sortCol===col && t.__sortDir==='desc') ? 'asc' : 'desc';
  t.__sortCol=col; t.__sortDir=dir;
  _sortTable(t, col, dir);
  t.querySelectorAll('thead th').forEach(x=>x.classList.remove('sort-asc','sort-desc'));
  th.classList.add(dir==='desc' ? 'sort-desc' : 'sort-asc');
};
if(doc.__sortClick) doc.removeEventListener('click', doc.__sortClick, true);
doc.__sortClick = sortClick;
doc.addEventListener('click', sortClick, true);
if(ENABLED){ doc.body.classList.add('laser-on'); }
else { doc.body.classList.remove('laser-on');
       doc.querySelectorAll('.laser-cur,.laser-keep').forEach(c=>c.classList.remove('laser-cur','laser-keep')); }
"""

try:
    from streamlit_autorefresh import st_autorefresh
    _HAS_AUTOREFRESH = True
except Exception:  # noqa: BLE001
    _HAS_AUTOREFRESH = False

# --------------------------------------------------------------------------- #
# Page setup
# --------------------------------------------------------------------------- #
st.set_page_config(
    page_title="BJOC - All In One Dashboard",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)

HDR_LABEL = "#a4c2f4"

# Appearance toggle. Read here (before the theme CSS) so the styling matches; the
# sidebar toggle widget shares the same session_state key, so a change is already
# reflected in session_state at the top of the next rerun (no one-frame lag).
st.session_state.setdefault("dark_mode", False)
DARK = bool(st.session_state.dark_mode)

st.markdown(
    """
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
      :root { --accent:#1f6feb; --accent2:#4f46e5; --ink:#102a4a; --line:#dbe2ea; }
      html, body, [data-testid="stAppViewContainer"] { font-family:'Inter', system-ui, sans-serif; }

      .block-container,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]{
          max-width:100% !important; padding:0.7rem 1.2rem 0 !important; }
      /* Keep the Streamlit header (so the collapsed-sidebar EXPAND arrow keeps
         working) but make it transparent and hide only the toolbar / menu / status.
         The header does not cover the banner because it is transparent. */
      header[data-testid="stHeader"]{ background:transparent !important; box-shadow:none !important;
          z-index:1000 !important; pointer-events:none; }
      header[data-testid="stHeader"] *{ pointer-events:auto; }  /* keep controls clickable */
      [data-testid="stToolbar"]{ display:none !important; }   /* hide the Deploy / ⋮ menu */
      [data-testid="stDecoration"]{ display:none !important; }   /* thin rainbow bar at the very top */
      /* keep Streamlit Cloud's owner controls (Manage app / status) visible */
      [data-testid="manage-app-button"], [data-testid="stStatusWidget"]{
          display:flex !important; visibility:visible !important; }
      /* Keep the sidebar permanently open: hide the collapse control so it can never
         be closed with no way to reopen it (the native expand arrow is unreliable).
         Combined with initial_sidebar_state="expanded", the sidebar is always shown. */
      [data-testid="stSidebarCollapseButton"]{ display:none !important; }
      /* collapse invisible helper wrappers so they don't reserve blank space above
         the banner: injected <style> blocks (the CSS still applies globally) and the
         0-height auto-refresh iframe (its timer keeps running at zero height) */
      [data-testid="stMain"] [data-testid="stElementContainer"]:has(style){ display:none !important; }
      [data-testid="stMain"] [data-testid="stElementContainer"]:has(iframe[title*="autorefresh"]){
          height:0 !important; min-height:0 !important; margin:0 !important; overflow:hidden !important; }
      /* Sidebar: a slim, LIGHT panel with dark-text menu items. Forced permanently
         OPEN (override any collapse: width / transform / margin) so it can never
         disappear with no way to reopen it. */
      [data-testid="stSidebar"]{ background:#eef2f8 !important; border-right:1px solid #d3dae3;
          transform:none !important; margin-left:0 !important; left:0 !important;
          min-width:200px !important; width:200px !important; max-width:200px !important;
          visibility:visible !important; }
      [data-testid="stSidebarContent"], [data-testid="stSidebarUserContent"]{ visibility:visible !important; }
      [data-testid="stSidebar"] h5, [data-testid="stSidebar"] label,
      [data-testid="stSidebar"] p, [data-testid="stSidebar"] span{ color:#1f2d3d !important; }
      [data-testid="stSidebar"] .sec-label{ color:#64748b !important; }
      /* function / metric buttons: FLAT left-aligned menu items, dark text;
         selected = accent-blue text with an accent left bar */
      [data-testid="stSidebar"] .stButton>button{ background:transparent !important;
          border:none !important; border-left:3px solid transparent !important; box-shadow:none !important;
          border-radius:8px; justify-content:flex-start !important; text-align:left !important;
          padding:.38rem .55rem !important; font-weight:600; transition:all .12s ease; }
      [data-testid="stSidebar"] .stButton>button *{ color:#334155 !important; text-align:left !important;
          justify-content:flex-start !important; white-space:normal; overflow-wrap:anywhere; }
      [data-testid="stSidebar"] .stButton>button [data-testid="stMarkdownContainer"]{
          width:100% !important; text-align:left !important; }
      [data-testid="stSidebar"] .stButton>button p{ text-align:left !important; width:100%; margin:0; }
      [data-testid="stSidebar"] .stButton>button:hover{ background:rgba(31,111,235,.08) !important;
          transform:none !important; }
      [data-testid="stSidebar"] .stButton>button:hover *{ color:var(--accent) !important; }
      [data-testid="stSidebar"] .stButton>button[kind="primary"]{ background:rgba(31,111,235,.12) !important;
          border-left:3px solid var(--accent) !important; }
      [data-testid="stSidebar"] .stButton>button[kind="primary"] *{ color:var(--accent) !important; font-weight:700; }
      /* Auto-refresh selectbox: white field with dark text */
      [data-testid="stSidebar"] [data-baseweb="select"]>div{ background:#fff !important; border-radius:8px; }
      [data-testid="stSidebar"] [data-baseweb="select"] div,
      [data-testid="stSidebar"] [data-baseweb="select"] span,
      [data-testid="stSidebar"] [data-baseweb="select"] input{ color:#1f2d3d !important; }

      .app-banner{ text-align:center; font-weight:800; font-size:1.45rem; letter-spacing:1.5px;
          color:#fff; padding:.42rem 1rem; border-radius:12px; margin:0 0 .3rem;
          background:linear-gradient(135deg,#3b5169 0%,#2b3d4f 100%);
          box-shadow:0 4px 14px rgba(31,45,61,.28); }
      .sec-label{ font-weight:700; color:#64748b; font-size:.75rem; letter-spacing:.8px;
          text-transform:uppercase; margin:.35rem 0 .15rem; }
      .metric-title{ text-align:center; font-weight:800; font-size:1.1rem; color:var(--ink);
          margin:.1rem 0 .05rem; animation:fadeInUp .4s ease both; }
      .metric-title .accent{ display:block; width:54px; height:3px; border-radius:3px;
          margin:.25rem auto 0; background:linear-gradient(90deg,var(--accent),#7aa7ff); }
      .hint{ text-align:center; color:#7b8794; padding:1.2rem; font-size:1.05rem;
          animation:fadeInUp .4s ease both; }
      /* compact vertical rhythm in the main area so the table box gets the most
         height (lets ~3 metric groups show at once) */
      [data-testid="stMain"] [data-testid="stVerticalBlock"]{ gap:.4rem; }
      [data-baseweb="tab-list"]{ margin-bottom:.1rem; }
      button[data-baseweb="tab"]{ padding-top:.2rem; padding-bottom:.2rem; }
      [data-baseweb="tab-panel"]{ padding-top:.1rem; }

      /* Faint tiled diagonal "N K" watermark over the content. Subtle at 100% zoom,
         clearer when zoomed in; click-through. Low z-index so it sits above the table
         content but BELOW the sidebar and Streamlit Cloud's "Manage app" chrome. */
      body::after{ content:""; position:fixed; top:0; left:0; right:0; bottom:0;
          z-index:5; pointer-events:none; opacity:.05;
          background-repeat:repeat; background-size:210px 150px;
          background-image:url("data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20width='210'%20height='150'%3E%3Ctext%20x='105'%20y='80'%20font-family='Inter,Arial,sans-serif'%20font-size='26'%20font-weight='700'%20fill='%23808080'%20text-anchor='middle'%20transform='rotate(-28%20105%2075)'%3EN%20K%3C/text%3E%3C/svg%3E"); }

      /* transforms only (no opacity) so a stalled animation can never leave an
         element invisible — the table, titles and buttons are always rendered */
      @keyframes fadeInUp{ from{transform:translateY(10px);} to{transform:none;} }
      @keyframes popIn{ 0%{transform:scale(.95) translateY(10px);} 100%{transform:none;} }

      /* pill buttons: white default, solid-blue when selected, blue-outline on hover */
      .stButton > button{ border-radius:11px; font-weight:600; border:1.5px solid #cbd5e1;
          background:#fff; color:#1f2d3d; box-shadow:0 1px 3px rgba(16,42,74,.10);
          transition:all .15s ease; animation:popIn .35s ease both; }
      .stButton > button:hover{ border-color:var(--accent); color:var(--accent);
          background:#fff; transform:translateY(-2px); box-shadow:0 6px 16px rgba(31,111,235,.20); }
      .stButton > button[kind="primary"]{ background:var(--accent); border:1.5px solid var(--accent);
          color:#fff; box-shadow:0 4px 12px rgba(31,111,235,.32); }
      .stButton > button[kind="primary"]:hover{ background:var(--accent2);
          border-color:var(--accent2); color:#fff; }
      .stButton > button[kind="secondary"]{ background:#fff; color:#1f2d3d; }
      /* cascade metric buttons within a row */
      [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(1) .stButton>button{animation-delay:.02s}
      [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(2) .stButton>button{animation-delay:.06s}
      [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(3) .stButton>button{animation-delay:.10s}
      [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(4) .stButton>button{animation-delay:.14s}
      [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(5) .stButton>button{animation-delay:.18s}
      [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(6) .stButton>button{animation-delay:.22s}
      [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(7) .stButton>button{animation-delay:.26s}
      [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(8) .stButton>button{animation-delay:.30s}

      .sheet-wrap{ overflow:auto; max-height:calc(100vh - 12rem); border:1.5px solid var(--cell-border,#000);
          border-radius:8px; box-shadow:0 1px 4px rgba(16,42,74,.08); animation:fadeInUp .45s ease both; }
      /* width:100% so few-column tables stretch to fill the box; min-width:max-content
         keeps wide tables their natural width (horizontal scroll) */
      table.sheet{ border-collapse:separate; border-spacing:0; width:100%; min-width:max-content;
          font-size:var(--fs,0.9rem); font-family:'Inter', system-ui, sans-serif; color:var(--cell-fg,#1f2d3d); }
      /* "all borders" on every cell of every table (theme-aware colour) */
      table.sheet th, table.sheet td{ border:1px solid var(--cell-border,#000); padding:6px 11px; line-height:1.3;
          text-align:center; vertical-align:middle; white-space:nowrap; overflow-wrap:normal;
          min-width:var(--cw,6em); }
      table.sheet thead th{ position:sticky; top:0; z-index:2; font-weight:700; }
      /* sortable tables: header cells are clickable and show a sort arrow */
      table.sheet[data-sortable] thead th{ cursor:pointer; }
      table.sheet th.sort-desc::after{ content:' \\25BC'; font-size:.72em; opacity:.85; }
      table.sheet th.sort-asc::after{ content:' \\25B2'; font-size:.72em; opacity:.85; }
      /* long paragraph cells wrap to a readable width instead of one huge line */
      table.sheet .wrapcell{ display:inline-block; max-width:30em; white-space:normal;
          overflow-wrap:anywhere; text-align:left; line-height:1.3; }
    </style>
    """,
    unsafe_allow_html=True,
)


# Theme: the dashboard controls its OWN light/dark palette via CSS variables (the
# sidebar "Dark mode" toggle sets DARK). We override Streamlit's theme variables so
# the whole app — chrome AND tables — follows our palette regardless of the browser
# or Streamlit theme. Sheet-coloured cells keep their own text colour (set inline,
# keyed to the cell's own background) so they stay readable in either mode.
_PALETTE = ("--page-bg:#0e1117; --ink:#e6e9ef; --muted:#9aa4b2; "
            "--cell-bg:#0e1117; --cell-fg:#e6e9ef; --cell-border:#3a414d; --line:#2a2f3a;"
            if DARK else
            "--page-bg:#e9eef5; --ink:#1f2d3d; --muted:#64748b; "
            "--cell-bg:#ffffff; --cell-fg:#1f2d3d; --cell-border:#000000; --line:#dbe2ea;")
_SCHEME = "dark" if DARK else "light"
st.markdown(
    f"""
    <style>
      html{{ color-scheme:{_SCHEME} !important; }}
      :root, .stApp, [data-testid="stApp"]{{
          {_PALETTE}
          --background-color:var(--page-bg) !important;
          --secondary-background-color:var(--cell-bg) !important;
          --text-color:var(--ink) !important;
          color-scheme:{_SCHEME} !important; }}
      .stApp, [data-testid="stApp"], [data-testid="stAppViewContainer"],
      [data-testid="stMain"], [data-testid="stMainBlockContainer"], [data-testid="stBottom"]{{
          background-color:var(--page-bg) !important; color:var(--ink); }}
      /* keep the top toolbar transparent so it never covers the banner */
      [data-testid="stHeader"]{{ background:transparent !important; }}
      /* MAIN-area text follows the light/dark palette; the sidebar is styled
         separately (fixed slate panel) so these must not touch it */
      [data-testid="stMain"] [data-baseweb="tab"]{{ color:var(--ink) !important; }}
      [data-testid="stMain"] [data-testid="stMarkdownContainer"],
      [data-testid="stMain"] [data-testid="stCaptionContainer"]{{ color:var(--ink); }}
      .metric-title{{ color:var(--ink) !important; }}
      .sheet-wrap{{ background:var(--cell-bg); }}
      table.sheet{{ color:var(--cell-fg); }}
    </style>
    """,
    unsafe_allow_html=True,
)


# The table-box height is computed dynamically in JS (sizeTable in _LASER_JS):
# it grows with the data down to the window bottom and shrinks to fit short tables.


# --------------------------------------------------------------------------- #
# Rendering helpers
# --------------------------------------------------------------------------- #
def _text_color(hexc: str) -> str:
    if not isinstance(hexc, str) or len(hexc) != 7 or not hexc.startswith("#"):
        return ""
    r, g, b = int(hexc[1:3], 16), int(hexc[3:5], 16), int(hexc[5:7], 16)
    # explicit text colour keyed to the CELL's own bg (not the app theme) so a
    # sheet-coloured cell stays readable in both light and dark mode
    return "#ffffff" if (0.299 * r + 0.587 * g + 0.114 * b) < 140 else "#1f2d3d"


def _bg_style(bg: str) -> str:
    if not isinstance(bg, str) or not bg:
        return ""
    tc = _text_color(bg)
    return f"background-color:{bg};" + (f"color:{tc};" if tc else "")


def _esc(s) -> str:
    s = "" if s is None else str(s)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _frozen(pos: int, frozen_cols: int, is_header: bool, bg: str, w: float) -> str:
    if pos >= frozen_cols:
        return ""
    s = (f"position:sticky;left:{round(pos * w, 2)}em;width:{w}em;min-width:{w}em;max-width:{w}em;"
         f"white-space:normal;overflow-wrap:anywhere;text-align:left;z-index:{4 if is_header else 1};")
    if not bg:
        s += "background-color:var(--cell-bg,#ffffff);color:var(--cell-fg,#1f2d3d);"
    return s


# Date parsing & per-metric format disambiguation live in data_loader so the
# same logic is shared by the app and the regression tool (no drift).
_to_date = dl.to_date
_choose_parser = dl.choose_parser


def find_date_cols(header_row, start: int):
    out = []
    for c in range(start, len(header_row)):
        d = _to_date(header_row[c])
        if d is not None:
            out.append((c, d))
    return out


def find_date_rows(values, start_row: int, col: int = 0):
    out = []
    for r in range(start_row, len(values)):
        cell = values[r][col] if col < len(values[r]) else ""
        d = _to_date(cell)
        if d is not None:
            out.append((r, d))
    return out


def drop_blank_cols(values, colors, merges):
    """Remove columns that are completely empty (no date, no site, no data) — e.g.
    trailing blanks after the last date. Keeps merges aligned."""
    if not values:
        return values, colors, merges
    ncols = max(len(r) for r in values)
    keep = [c for c in range(ncols)
            if any(c < len(r) and str(r[c]).strip() for r in values)]
    if len(keep) == ncols:
        return values, colors, merges
    return slice_cols(values, colors, merges, keep)


def slice_rows(values, colors, merges, keep_rows):
    """Keep only `keep_rows` (in given order); remap merges that stay contiguous."""
    rowmap = {r: i for i, r in enumerate(keep_rows)}
    keepset = set(keep_rows)
    nv = [values[r] for r in keep_rows]
    nc = [(colors[r] if r < len(colors) else []) for r in keep_rows]
    nm = []
    for sr, er, sc, ec in (merges or []):
        rows = list(range(sr, er))
        if rows and all(rr in keepset for rr in rows):
            nw = [rowmap[rr] for rr in rows]
            if nw == list(range(min(nw), max(nw) + 1)):
                nm.append((min(nw), max(nw) + 1, sc, ec))
    return nv, nc, nm


def slice_cols(values, colors, merges, keep_cols):
    """Keep only `keep_cols` (in given order); remap merges that stay contiguous."""
    colmap = {c: i for i, c in enumerate(keep_cols)}
    keepset = set(keep_cols)
    nv = [[(row[c] if c < len(row) else "") for c in keep_cols] for row in values]
    nc = [[(colors[r][c] if r < len(colors) and c < len(colors[r]) else "")
           for c in keep_cols] for r in range(len(values))]
    nm = []
    for sr, er, sc, ec in (merges or []):
        cols = list(range(sc, ec))
        if cols and all(c in keepset for c in cols):
            nw = [colmap[c] for c in cols]
            if nw == list(range(min(nw), max(nw) + 1)):
                nm.append((sr, er, min(nw), max(nw) + 1))
    return nv, nc, nm


def render_table(values, colors, frozen=(0, 0), merges=None,
                 font_rem: float = 0.9, cell_w: float = 6.0, label_w: float = 9.0,
                 sortable: bool = False) -> str:
    grid = [list(r) for r in values]
    while grid and not any(str(c).strip() for c in grid[-1]):
        grid.pop()
    if not grid:
        return "<em>No data.</em>"
    nrows = len(grid)
    ncols = max(len(r) for r in grid)
    while ncols > 0 and all(len(r) < ncols or not str(r[ncols - 1]).strip() for r in grid):
        ncols -= 1
    if ncols == 0:
        return "<em>No data.</em>"

    def color_at(r, c):
        # each format cell is a (background_hex, is_bold) tuple; tolerate a bare
        # string or missing cell (slicing pads out-of-range cells with "")
        v = colors[r][c] if (r < len(colors) and c < len(colors[r])) else ""
        if isinstance(v, (list, tuple)):
            return (v[0] or ""), (bool(v[1]) if len(v) > 1 else False)
        return (v or ""), False

    fr, fc = frozen
    fc = max(0, min(fc, ncols))
    fr = max(0, fr)

    anchor, covered = {}, set()
    for sr, er, sc, ec in (merges or []):
        er, ec = min(er, nrows), min(ec, ncols)
        if sr >= nrows or sc >= ncols or er - sr < 1 or ec - sc < 1:
            continue
        if (er - sr) == 1 and (ec - sc) == 1:
            continue
        anchor[(sr, sc)] = (er - sr, ec - sc)
        for rr in range(sr, er):
            for cc in range(sc, ec):
                if (rr, cc) != (sr, sc):
                    covered.add((rr, cc))

    # Freeze the first `fr` rows (sheet's setting). Don't let a vertical merge
    # straddle the thead/tbody split (rowspans can't cross it).
    header_n = fr
    for (r, _c), (rs, _cs) in anchor.items():
        if r < header_n < r + rs:
            header_n = r
    header_n = max(0, min(header_n, nrows))

    def render_row(r: int, tag: str) -> str:
        cells = []
        for c in range(ncols):
            if (r, c) in covered:
                continue
            span = ""
            if (r, c) in anchor:
                rs, cs = anchor[(r, c)]
                if rs > 1:
                    span += f' rowspan="{rs}"'
                if cs > 1:
                    span += f' colspan="{cs}"'
            bg, bold = color_at(r, c)
            if tag == "th" and not bg:
                bg = HDR_LABEL
            val = grid[r][c] if c < len(grid[r]) else ""
            ev = _esc(val)
            if len(str(val).strip()) > 30:   # large text -> wrap to a readable width
                ev = f'<div class="wrapcell">{ev}</div>'
            style = _bg_style(bg) + _frozen(c, fc, tag == "th", bg, label_w)
            # bold header cells, plus any cell the sheet itself marks bold
            wt = "font-weight:700;" if (tag == "th" or bold) else ""
            cells.append(f'<{tag}{span} style="{style}{wt}">{ev}</{tag}>')
        return "<tr>" + "".join(cells) + "</tr>"

    sattr = ' data-sortable="1"' if sortable else ''
    html = [f'<div class="sheet-wrap"><table class="sheet" data-frozenrows="{header_n}"{sattr} '
            f'style="--fs:{font_rem}rem;--cw:{cell_w}em">']
    if header_n > 0:
        html.append("<thead>")
        for r in range(header_n):
            html.append(render_row(r, "th"))
        html.append("</thead><tbody>")
        start = header_n
    else:
        html.append("<tbody>")
        start = 0
    for r in range(start, nrows):
        html.append(render_row(r, "td"))
    html.append("</tbody></table></div>")
    return "".join(html)


def _is_num(s):
    try:
        float(str(s).strip().replace(",", "").replace("%", ""))
        return True
    except (ValueError, TypeError):
        return False


def _sort_key(x):
    s = str(x).strip().replace(",", "").replace("%", "")
    try:
        return (0, float(s))
    except (ValueError, TypeError):
        return (1, str(x).strip().lower())


# Geo columns we offer as filters (case-insensitive header match)
_GEO_NAMES = {"region", "state", "zone"}


def _flat_controls(values):
    """True if a flat table has a Subtotal/Total row near the TOP (first column),
    so the Sort/Filter + live-subtotal renderer should handle it."""
    for r in range(min(5, len(values))):
        row = values[r] if r < len(values) else []
        first = str(row[0]).strip().lower() if row else ""
        if first.startswith("subtotal") or first.startswith("grand total") or first == "total":
            return True
    return False


def render_fm(values, colors, merges, fr, fc, kp):
    """Flat tables (FM + any metric with a top subtotal): a Region/State/Zone Filter
    + live subtotal. Data rows are flattened (forward-filled geo, no row merges) and
    the table is marked sortable so clicking a column header sorts it (handled in JS)."""
    def label_count(row):
        return sum(1 for x in row if str(x).strip()
                   and _to_date(x) is None and not _is_num(x))

    hdr_idx = (max(range(min(8, len(values))), key=lambda i: label_count(values[i]))
               if values else 0)
    header = values[hdr_idx] if values else []
    colnames = [(i, str(header[i]).strip()) for i in range(len(header))
                if str(header[i]).strip()]
    # only rows with actual content (skip wholly-empty rows so forward-fill can't
    # turn a blank row into a phantom Region/State row)
    data_rows = [r for r in range(hdr_idx + 1, len(values))
                 if any(str(x).strip() for x in values[r])]
    geo = [(i, n) for i, n in colnames if n.lower() in _GEO_NAMES]
    ncols_all = max((len(r) for r in values), default=0)

    # a subtotal/total row among the header rows (recomputed live when filtered)
    subtotal_row = None
    for hr in range(hdr_idx + 1):
        first = str(values[hr][0]).strip().lower() if values[hr] else ""
        if first.startswith("subtotal") or first.startswith("grand total") or first == "total":
            subtotal_row = hr
            break

    def _is_pct_col(c):
        vals = [str(values[r][c]).strip() for r in data_rows
                if c < len(values[r]) and str(values[r][c]).strip()]
        return bool(vals) and sum(v.endswith("%") for v in vals) >= len(vals) * 0.5

    # working copy with geo columns forward-filled (so merged region/state cells
    # filter & display correctly once we reorder/subset rows)
    wv = [list(r) for r in values]
    for ci, _ in geo:
        last = ""
        for r in data_rows:
            cur = str(wv[r][ci]).strip() if ci < len(wv[r]) else ""
            if cur:
                last = cur
            elif last and ci < len(wv[r]):
                wv[r][ci] = last

    # --- State filter: a single dropdown directly above the table (rendered inline,
    # NOT in a popover, so its options always show). Only "State" is filterable. ---
    filters = {}
    state_cols = [(i, n) for i, n in geo if n.lower() == "state"]
    if state_cols:
        fcol, _rest = st.columns([2, 6])
        with fcol:
            for ci, name in state_cols:
                vals = sorted({str(wv[r][ci]).strip() for r in data_rows
                               if ci < len(wv[r]) and str(wv[r][ci]).strip()})
                picked = st.multiselect(name, vals, key=f"{kp}__f{ci}",
                                        placeholder="All states")
                if picked:
                    filters[ci] = set(picked)

    # If the sheet merges cells in the DATA region (grouped layout) and no filter is
    # applied, render it exactly as the sheet (merges preserved). Such tables aren't
    # sortable (reordering would break the rowspans).
    data_merges = any(m[0] > hdr_idx for m in merges)
    if data_merges and not filters:
        st.markdown(render_table(values, colors, frozen=(fr, fc), merges=merges,
                                 font_rem=font_rem, cell_w=cell_w, label_w=label_w,
                                 sortable=False),
                    unsafe_allow_html=True)
        return

    # apply filter -> visible rows (kept in sheet order; click-to-sort handles ordering)
    drows = data_rows
    for ci, vals in filters.items():
        drows = [r for r in drows if ci < len(wv[r]) and str(wv[r][ci]).strip() in vals]

    # recompute the subtotal row from the currently-visible rows (like the sheet):
    # sum count columns, average percentage columns, leave text/label cells alone.
    # A value column with NO visible values must read 0 — not the stale full total.
    def _is_val_col(c):
        vals = [str(values[r][c]).strip() for r in data_rows
                if c < len(values[r]) and str(values[r][c]).strip()]
        return bool(vals) and sum(1 for v in vals
                                  if v.endswith("%") or _is_num(v)) >= len(vals) * 0.5

    if subtotal_row is not None:
        while len(wv[subtotal_row]) < ncols_all:
            wv[subtotal_row].append("")
        for c in range(1, ncols_all):
            if not _is_val_col(c):
                continue                       # text/label column — leave it as-is
            nums = []
            for r in drows:
                s = str(values[r][c]).strip() if c < len(values[r]) else ""
                if not s:
                    continue
                try:
                    nums.append(float(s[:-1] if s.endswith("%") else s.replace(",", "")))
                except ValueError:
                    pass
            if _is_pct_col(c):
                wv[subtotal_row][c] = f"{sum(nums) / len(nums):.2f}%" if nums else ""
            else:
                tot = sum(nums)                # 0 when no visible row has a value
                wv[subtotal_row][c] = (f"{tot:,.0f}" if abs(tot - round(tot)) < 1e-9
                                       else f"{tot:,.2f}")

    keep = list(range(hdr_idx + 1)) + drows
    # keep only header-region merges (date/band rows); drop data-row merges so each
    # data row is independent and click-to-sort can't break a rowspan
    hmerges = [m for m in merges if m[0] <= hdr_idx]
    vv, cc, mm = slice_rows(wv, colors, hmerges, keep)
    if filters:
        st.caption(f"Showing {len(drows)} of {len(data_rows)} rows · filtered")
    st.markdown(render_table(vv, cc, frozen=(fr, fc), merges=mm,
                             font_rem=font_rem, cell_w=cell_w, label_w=label_w,
                             sortable=True),
                unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Data access (cached, on-demand)
# --------------------------------------------------------------------------- #
def _has_sa() -> bool:
    try:
        return "gcp_service_account" in st.secrets
    except Exception:  # noqa: BLE001
        return False


@st.cache_data(ttl=300, show_spinner=False)
def get_meta(tick: int = 0) -> list:
    return dl.load_meta(dict(st.secrets["gcp_service_account"]))


@st.cache_data(ttl=300, show_spinner="Loading table…")
def get_grid(title: str, ncols: int = 60, nrows: int = 60, tick: int = 0):
    # Date-grouped tables run oldest→newest left→right, so the LATEST dates live in
    # the rightmost columns — we must fetch the full width or "last 4 days" silently
    # shows stale dates. The sheet's rowCount is usually inflated (1000s) vs the few
    # real rows, so size the budget generously; the API only returns rows that hold
    # data, keeping the actual payload small.
    mc = min(max(int(ncols), 60), 700)
    mr = min(max(int(nrows), 40), 400)
    if mr * mc > 300_000:
        mc = max(60, 300_000 // mr)
    return dl.load_tab_grid(dict(st.secrets["gcp_service_account"]), title,
                            max_rows=mr, max_cols=mc)


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
if not _has_sa():
    st.error("No service account configured. Add `gcp_service_account` to "
             "`.streamlit/secrets.toml` (see README).")
    st.stop()

st.sidebar.markdown("##### ⚙️ Controls")
if st.sidebar.button("🔄 Refresh now", key="refresh_btn", use_container_width=True):
    get_meta.clear()
    get_grid.clear()
    st.rerun()

_RF = {"Off": 0, "30 sec": 30, "1 min": 60, "5 min": 300}
rf_choice = st.sidebar.selectbox("⏱️ Auto-refresh", list(_RF), index=2)
rf_sec = _RF[rf_choice]
tick = 0
if rf_sec and _HAS_AUTOREFRESH:
    tick = st_autorefresh(interval=rf_sec * 1000, key="auto_rf")

# Light/Dark appearance (read at the top of the script as DARK; toggling reruns)
st.sidebar.toggle("🌙 Dark mode", key="dark_mode",
                  help="Switch the whole dashboard between light and dark.")

st.sidebar.divider()
# functions + metrics navigation (kept below the refresh controls)
nav_ph = st.sidebar.container()
st.sidebar.divider()
font_rem = st.sidebar.slider("🔠 Table font size", 0.6, 1.5, 0.9, 0.05)
# Column widths are now resized directly IN the table (Excel-style: drag a column
# header's right edge). These are just the starting widths.
cell_w, label_w = 6.0, 9.0

# --------------------------------------------------------------------------- #
# Catalogue
# --------------------------------------------------------------------------- #
try:
    tabs = get_meta(tick=tick)
except Exception as exc:  # noqa: BLE001
    st.error(f"Could not read the sheet.\n\n```\n{exc}\n```")
    st.stop()
if not tabs:
    st.warning("No visible tabs found in the sheet.")
    st.stop()

present = [t["function"] for t in tabs if t["function"]]
functions = [f for f in ("FC", "MH") if f in present] + \
            [f for f in sorted(set(present)) if f not in ("FC", "MH")]

st.session_state.setdefault("func", None)          # nothing selected by default
st.session_state.setdefault("metric", None)
if st.session_state.func not in functions:
    st.session_state.func = None
    st.session_state.metric = None

metrics = ([t for t in tabs if t["function"] == st.session_state.func]
           if st.session_state.func else [])

# --------------------------------------------------------------------------- #
# Sidebar navigation: Functions, then Metrics for the chosen function
# --------------------------------------------------------------------------- #
with nav_ph:
    st.markdown("<div class='sec-label'>Function</div>", unsafe_allow_html=True)
    for f in functions:
        if st.button(f, key=f"fn_{f}", use_container_width=True,
                     type="primary" if st.session_state.func == f else "secondary"):
            st.session_state.func = f
            st.session_state.metric = None
            st.rerun()
    if st.session_state.func:
        st.markdown(f"<div class='sec-label'>{st.session_state.func} metrics</div>",
                    unsafe_allow_html=True)
        for t in metrics:
            active = st.session_state.metric == t["title"]
            if st.button(t["metric"], key=f"mt_{t['title']}", use_container_width=True,
                         type="primary" if active else "secondary"):
                st.session_state.metric = t["title"]
                st.rerun()

# --------------------------------------------------------------------------- #
# Banner + main content (full-width table)
# --------------------------------------------------------------------------- #
st.markdown("<div class='app-banner'>BJOC&nbsp;-&nbsp;ALL IN ONE DASHBOARD</div>",
            unsafe_allow_html=True)

with st.container():
    sel = st.session_state.metric
    if st.session_state.func is None:
        st.markdown("<div class='hint'>👈 Select a function from the sidebar to begin.</div>",
                    unsafe_allow_html=True)
        st.stop()
    if not sel or sel not in {t["title"] for t in metrics}:
        st.markdown("<div class='hint'>👈 Pick a metric from the sidebar to view its table.</div>",
                    unsafe_allow_html=True)
        st.stop()

    # ----------------------------------------------------------------------- #
    # Selected metric table (with Overall / Week / Month for dated tables)
    # ----------------------------------------------------------------------- #
    tab = next(t for t in tabs if t["title"] == sel)
    st.markdown(f"<div class='metric-title'>{_esc(tab['metric'])} "
                f"<span style='color:#94a3b8;font-weight:600;font-size:.9rem'>· {tab['function']}</span>"
                f"<span class='accent'></span></div>", unsafe_allow_html=True)

    try:
        values, colors, truncated = get_grid(sel, ncols=tab["cols"], nrows=tab["rows"], tick=tick)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not load **{sel}**.\n\n```\n{exc}\n```")
        st.stop()

    fr, fc = tab["frozen"]
    merges = tab["merges"]
    # strip fully-blank columns (e.g. empty space after the last date) everywhere
    values, colors, merges = drop_blank_cols(values, colors, merges)
    hdr_rows = max(1, fr)

    def _iso(d):
        return (int(d.isocalendar().year), int(d.isocalendar().week))

    def detect_date_header(grid, max_search=6):
        """Find the row that holds the dates (search the first few rows). Returns
        (row_index, [(col, date), ...] sorted by col). Handles multi-row headers."""
        best_row, best = None, []
        for r in range(min(max_search, len(grid))):
            starts = [(c, _to_date(grid[r][c])) for c in range(len(grid[r])) if _to_date(grid[r][c])]
            if len(starts) > len(best):
                best, best_row = starts, r
        return best_row, sorted(best, key=lambda x: x[0])

    def date_groups_from(date_starts, ncols_full):
        """Each date owns the columns from its start up to the next date's start
        (so a date that visually spans several sub-columns keeps them all)."""
        gmap = {}
        for i, (c, d) in enumerate(date_starts):
            end = date_starts[i + 1][0] if i + 1 < len(date_starts) else ncols_full
            gmap.setdefault(d, []).extend(range(c, end))
        return gmap

    # horizontal dates (a header row anywhere in the first rows) — generic.
    # `detect_date_header` locates date-like cells permissively; we then lock the
    # correct format for THIS metric and re-parse (fixes DD-MM vs MM-DD sheets).
    dr_row, date_starts = detect_date_header(values)
    if dr_row is not None and len(date_starts) >= 4:
        cols = [c for c, _ in date_starts]
        parser = _choose_parser([values[dr_row][c] for c in cols])
        date_starts = [(c, parser(values[dr_row][c])) for c in cols
                       if parser(values[dr_row][c]) is not None]

    # vertical dates (down the first column) — only if not a header-date table
    date_rows = []
    if len(date_starts) < 4:
        rows0 = [(r, values[r][0]) for r in range(hdr_rows, len(values))
                 if values[r] and _to_date(values[r][0]) is not None]
        if len(rows0) >= 4 and rows0[0][0] <= hdr_rows + 1:
            parser = _choose_parser([s for _, s in rows0])
            date_rows = [(r, parser(s)) for r, s in rows0 if parser(s) is not None]

    def show_cols(keep_cols, frozen):
        v, c, m = slice_cols(values, colors, merges, keep_cols)
        st.markdown(render_table(v, c, frozen=frozen, merges=m,
                                 font_rem=font_rem, cell_w=cell_w, label_w=label_w),
                    unsafe_allow_html=True)

    def show_rows(keep_rows):
        v, c, m = slice_rows(values, colors, merges, keep_rows)
        st.markdown(render_table(v, c, frozen=(fr, fc), merges=m,
                                 font_rem=font_rem, cell_w=cell_w, label_w=label_w),
                    unsafe_allow_html=True)

    if tab["function"] == "FM":
        # ---- FM: flat table with Sort + Region/State Filter controls ----
        render_fm(values, colors, merges, fr, fc, sel)
    elif len(date_starts) >= 4:
        # ---- dates across a header row: slice COLUMNS by date group (latest first) ----
        ncols_full = max(len(r) for r in values)
        first_date_col = date_starts[0][0]
        labels = list(range(first_date_col))            # everything before the first date
        gmap = date_groups_from(date_starts, ncols_full)
        dates_desc = sorted(gmap.keys(), reverse=True)
        frz = (fr, first_date_col)                       # freeze header rows + label cols
        t_over, t_week, t_month = st.tabs(["Overall (last 4 days)", "Week", "Month"])
        with t_over:
            cols = labels + [c for d in dates_desc[:4] for c in gmap[d]]
            show_cols(cols, frz)
        with t_week:
            weeks = sorted({_iso(d) for d in gmap}, reverse=True)
            wk = st.selectbox("Week", weeks, key="wk_sel", format_func=lambda k: f"Week {k[1]}, {k[0]}")
            cols = labels + [c for d in dates_desc if _iso(d) == wk for c in gmap[d]]
            show_cols(cols, frz)
        with t_month:
            months = sorted({(d.year, d.month) for d in gmap}, reverse=True)
            mo = st.selectbox("Month", months, key="mo_sel",
                              format_func=lambda k: pd.Timestamp(year=k[0], month=k[1], day=1).strftime("%B %Y"))
            cols = labels + [c for d in dates_desc if (d.year, d.month) == mo for c in gmap[d]]
            show_cols(cols, frz)
    elif date_rows:
        # ---- dates down the first column: slice ROWS (latest first) ----
        head = list(range(hdr_rows))
        desc = sorted(date_rows, key=lambda x: x[1], reverse=True)
        t_over, t_week, t_month = st.tabs(["Overall (last 4 days)", "Week", "Month"])
        with t_over:
            show_rows(head + [r for r, _ in desc[:4]])
        with t_week:
            weeks = sorted({_iso(d) for _, d in date_rows}, reverse=True)
            wk = st.selectbox("Week", weeks, key="wk_sel_r", format_func=lambda k: f"Week {k[1]}, {k[0]}")
            show_rows(head + [r for r, d in desc if _iso(d) == wk])
        with t_month:
            months = sorted({(d.year, d.month) for _, d in date_rows}, reverse=True)
            mo = st.selectbox("Month", months, key="mo_sel_r",
                              format_func=lambda k: pd.Timestamp(year=k[0], month=k[1], day=1).strftime("%B %Y"))
            show_rows(head + [r for r, d in desc if (d.year, d.month) == mo])
    elif _flat_controls(values):
        # any flat table with a top subtotal or region/state column gets the same
        # Sort/Filter + live-subtotal treatment as FM
        render_fm(values, colors, merges, fr, fc, sel)
    else:
        st.markdown(render_table(values, colors, frozen=(fr, fc), merges=merges,
                                 font_rem=font_rem, cell_w=cell_w, label_w=label_w),
                    unsafe_allow_html=True)

    if truncated:
        st.caption("⚠️ Large tab — showing the first portion of rows/columns.")

# highlighter is always on: hover outline + click-drag region highlight
inject_laser(True)
