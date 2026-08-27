"""
ui_theme.py — Sistema de diseño compartido "VerifyData".

Centraliza el look & feel del frontend web (antes duplicado en 5 plantillas
inline con tokens divergentes). Provee:

  * THEME_CSS  — hoja de estilos completa (tokens + Montserrat self-hosted +
                 componentes: sidebar, topbar, cards, badges, tablas, formularios,
                 KPIs, tabs, evidencia, timeline, árbol, etc.).
  * head_open(title)         — <!doctype> + <head> con la CSS embebida.
  * shell_open(active, ...)  — <body> + sidebar + topbar (markup Jinja).
  * SHELL_CLOSE              — cierre de main/app/body.
  * NAV / branding constants.

Paleta de marca: gradiente magenta #d00de3 → violeta #6941f4 → azul #3e7af9 →
cian #1de5e9. Sidebar oscuro #221f33. Fuente Montserrat.
"""
from __future__ import annotations

BRAND_NAME = "VerifyData"
BRAND_TAGLINE = "Inteligencia de datos para decisiones seguras"

# Wordmark de texto (sin imágenes de marca; neutral y portable).
WORDMARK = 'Verify<span>Data</span>'

# ============================================================================
#  CSS — sistema de diseño (sin Jinja; se envuelve en {% raw %} al inyectarse)
# ============================================================================
THEME_CSS = r"""
/* ---- Montserrat (self-hosted, offline-safe) ---- */
@font-face{font-family:'Montserrat';font-weight:400;font-style:normal;font-display:swap;
  src:url('/static/fonts/Montserrat-Regular.ttf') format('truetype');}
@font-face{font-family:'Montserrat';font-weight:500;font-style:normal;font-display:swap;
  src:url('/static/fonts/Montserrat-Medium.ttf') format('truetype');}
@font-face{font-family:'Montserrat';font-weight:600;font-style:normal;font-display:swap;
  src:url('/static/fonts/Montserrat-SemiBold.ttf') format('truetype');}
@font-face{font-family:'Montserrat';font-weight:700;font-style:normal;font-display:swap;
  src:url('/static/fonts/Montserrat-Bold.ttf') format('truetype');}
@font-face{font-family:'Montserrat';font-weight:800;font-style:normal;font-display:swap;
  src:url('/static/fonts/Montserrat-ExtraBold.ttf') format('truetype');}
@font-face{font-family:'Montserrat';font-weight:400;font-style:italic;font-display:swap;
  src:url('/static/fonts/Montserrat-Italic.ttf') format('truetype');}

:root{
  --sidebar:#221f33; --sidebar-2:#2b2840; --sidebar-line:rgba(216,216,229,0.12);
  --bg:#f7f8fc; --card:#ffffff; --line:#e5e7eb; --text:#111827; --text-dim:#6b7280; --text-faint:#9ca3af;
  --magenta:#d00de3; --violet:#6941f4; --blue:#3e7af9; --cyan:#1de5e9;
  --green:#22c55e; --amber:#fbbf24; --red:#ef4444;
  --grad-main:linear-gradient(90deg,var(--magenta),var(--violet) 45%,var(--blue) 75%,var(--cyan));
  --grad-btn:linear-gradient(90deg,var(--blue),var(--violet));
  --radius:16px; --shadow:0 1px 2px rgba(17,24,39,0.04),0 8px 24px rgba(17,24,39,0.04);
}
*{box-sizing:border-box;}
html,body{margin:0;padding:0;background:var(--bg);font-family:'Montserrat',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:var(--text);-webkit-font-smoothing:antialiased;}
a{color:inherit;}
button,input,select,textarea{font-family:'Montserrat',sans-serif;}

/* ---- App shell ---- */
.app{display:grid;grid-template-columns:250px 1fr;min-height:100vh;}
.sidebar{background:var(--sidebar);padding:22px 18px;position:sticky;top:0;height:100vh;overflow:auto;}
.brand{margin-bottom:22px;padding:0 2px;}
.brand img{height:52px;width:auto;display:block;}
/* Wordmark de texto (reemplaza al logo; neutral) */
.wordmark{font-weight:800;font-size:24px;letter-spacing:-0.5px;color:#fff;line-height:1.1;}
.wordmark span{background:linear-gradient(90deg,#1de5e9,#3e7af9);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;color:transparent;}
.wordmark.wm-light{color:#111827;}
.wordmark.wm-sm{font-size:19px;}
.wordmark.wm-hero{font-size:56px;letter-spacing:-1.5px;}
.nav-group{margin-bottom:16px;}
.nav-label{color:#726e93;font-size:10px;letter-spacing:.1em;text-transform:uppercase;font-weight:700;margin:0 0 8px 10px;}
.nav-item{display:flex;align-items:center;gap:10px;color:#c3c0d9;font-size:13px;font-weight:500;padding:9px 10px;border-radius:9px;margin-bottom:2px;cursor:pointer;user-select:none;text-decoration:none;}
.nav-item svg{width:16px;height:16px;flex-shrink:0;opacity:.85;}
.nav-item:hover{background:rgba(255,255,255,0.05);color:#fff;}
.nav-item.active{background:linear-gradient(90deg,rgba(105,65,244,0.32),rgba(29,229,233,0.12));color:#fff;font-weight:600;}
.nav-item.active svg{opacity:1;}
.sidebar-foot{margin-top:24px;padding:14px 10px 0;border-top:1px solid var(--sidebar-line);color:#726e93;font-size:10.5px;line-height:1.5;}

.main{padding:26px 40px 90px;max-width:1180px;}
.topbar{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:6px;}
.topbar h1{font-size:22px;margin:0;font-weight:700;}
.topbar .who{display:flex;align-items:center;gap:10px;font-size:13px;color:var(--text-dim);white-space:nowrap;}
.topbar .who a{color:var(--violet);text-decoration:none;font-weight:600;}
.topbar .who a:hover{text-decoration:underline;}
.avatar-sm{width:30px;height:30px;border-radius:50%;background:var(--grad-main);flex-shrink:0;}
.crumbs{font-size:12.5px;color:var(--text-faint);margin-bottom:26px;}
.crumbs b{color:var(--text-dim);font-weight:600;}

/* ---- Cards ---- */
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);}
.pad{padding:24px;}
.kicker{font-size:11px;color:var(--text-faint);text-transform:uppercase;letter-spacing:.08em;font-weight:700;margin:0 0 6px;}
.section-title{font-size:16px;font-weight:700;margin:0 0 14px;display:flex;align-items:center;gap:10px;}
.section-title::before{content:'';width:4px;height:18px;border-radius:3px;background:var(--grad-btn);}
.section-sub{font-size:13px;color:var(--text-dim);line-height:1.6;margin:0 0 16px;}

/* ---- Buttons ---- */
.btn{font-weight:600;font-size:13.5px;border-radius:10px;padding:11px 20px;border:none;cursor:pointer;display:inline-flex;align-items:center;gap:8px;text-decoration:none;line-height:1;transition:transform .05s,filter .15s;}
.btn-primary{background:var(--grad-btn);color:#fff;box-shadow:0 2px 8px rgba(105,65,244,.28);}
.btn-primary:hover{filter:brightness(1.05);}
.btn-secondary{background:#fff;border:1px solid #d8d4ea;color:var(--text);}
.btn-secondary:hover{background:#faf9fe;}
.btn-ghost{background:transparent;border:none;color:var(--text-dim);}
.btn-ghost:hover{color:var(--text);}
.btn-critical{background:var(--red);color:#fff;}
.btn-success{background:var(--green);color:#fff;}
.btn-sm{font-size:12px;padding:7px 13px;border-radius:8px;}
.btn:active{transform:translateY(1px);}
.btn[disabled]{opacity:.5;cursor:not-allowed;}

/* ---- Badges / pills ---- */
.badge{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;font-weight:600;padding:5px 11px;border-radius:20px;white-space:nowrap;line-height:1.3;}
.badge-dot{width:6px;height:6px;border-radius:50%;flex-shrink:0;}
.b-verde{background:rgba(34,197,94,0.12);color:#15803d;}.b-verde .badge-dot{background:#22c55e;}
.b-amber{background:rgba(251,191,36,0.14);color:#a16207;}.b-amber .badge-dot{background:#eab308;}
.b-rojo{background:rgba(239,68,68,0.10);color:#b91c1c;}.b-rojo .badge-dot{background:#ef4444;}
.b-azul{background:rgba(62,122,249,0.10);color:#1d4ed8;}.b-azul .badge-dot{background:#3e7af9;}
.b-gris{background:#f1f0f6;color:var(--text-dim);}.b-gris .badge-dot{background:#9ca3af;}
.b-violeta{background:rgba(105,65,244,0.10);color:#5b21b6;}.b-violeta .badge-dot{background:#6941f4;}

/* ---- Tables ---- */
table.dtable{width:100%;border-collapse:collapse;font-size:13px;}
.dtable th{text-align:left;font-size:11px;color:var(--text-faint);text-transform:uppercase;letter-spacing:.06em;padding:0 12px 10px;font-weight:600;}
.dtable td{padding:13px 12px;border-top:1px solid var(--line);color:var(--text-dim);vertical-align:top;}
.dtable td.name{color:var(--text);font-weight:600;}
.dtable tr:hover td{background:#fafafe;}

/* ---- Filters / segmented ---- */
.filter-row{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:18px;}
.filter-row select,.filter-row input{border:1px solid var(--line);border-radius:9px;padding:9px 12px;font-size:12.5px;color:var(--text);background:#fff;}
.seg{display:inline-flex;background:#eceafa;border-radius:10px;padding:4px;}
.seg button{border:none;background:transparent;font-size:12.5px;font-weight:600;padding:9px 14px;border-radius:7px;color:var(--text-dim);cursor:pointer;}
.seg button.active{background:#fff;color:var(--text);box-shadow:var(--shadow);}

/* ---- Empty state ---- */
.empty-state{text-align:center;padding:56px 20px;}
.empty-state .ic{width:52px;height:52px;border-radius:14px;background:#f1f0f6;display:flex;align-items:center;justify-content:center;margin:0 auto 16px;color:var(--text-faint);}
.empty-state h4{font-size:15px;margin:0 0 6px;}
.empty-state p{font-size:13px;color:var(--text-faint);margin:0 auto;max-width:440px;line-height:1.6;}

/* ---- KPIs ---- */
.kpi-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:22px;}
.kpi{padding:20px;}
.kpi .top-row{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:14px;}
.kpi .icon-chip{width:34px;height:34px;border-radius:9px;display:flex;align-items:center;justify-content:center;}
.kpi .icon-chip svg{width:17px;height:17px;}
.kpi .val{font-size:26px;font-weight:700;margin:0 0 2px;font-variant-numeric:tabular-nums;}
.kpi .lbl{font-size:12px;color:var(--text-dim);margin:0;}

/* ---- Forms ---- */
.form-shell{display:grid;grid-template-columns:1fr 300px;gap:20px;}
.form-section{margin-bottom:26px;}
.form-section:last-child{margin-bottom:0;}
.form-section h4{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--text-faint);margin:0 0 16px;font-weight:700;}
.field-row{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px;}
.field.full{grid-column:1/-1;}
.field label{display:block;font-size:12.5px;font-weight:600;color:var(--text);margin-bottom:7px;}
.field input,.field select{width:100%;border:1px solid var(--line);border-radius:9px;padding:11px 13px;font-size:13.5px;color:var(--text);background:#fff;}
.field input::placeholder{color:var(--text-faint);}
.field input:focus,.field select:focus{outline:none;border-color:var(--blue);box-shadow:0 0 0 3px rgba(62,122,249,0.12);}
.check-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;}
.check-opt{display:flex;align-items:flex-start;gap:10px;padding:13px 14px;border:1px solid var(--line);border-radius:11px;cursor:pointer;}
.check-opt input{margin-top:2px;accent-color:var(--violet);}
.check-opt .t{font-size:13px;font-weight:600;margin-bottom:2px;}
.check-opt .d{font-size:11.5px;color:var(--text-faint);line-height:1.4;}
.radio-opt{display:flex;align-items:flex-start;gap:12px;padding:15px 16px;border:1px solid var(--line);border-radius:12px;margin-bottom:10px;cursor:pointer;}
.radio-opt.sel{border-color:var(--violet);background:rgba(105,65,244,0.04);}
.radio-opt input{margin-top:3px;accent-color:var(--violet);}
.radio-opt .t{font-size:13.5px;font-weight:600;margin-bottom:3px;}
.radio-opt .d{font-size:12px;color:var(--text-faint);line-height:1.5;}
.side-help{padding:22px;}
.side-help h4{font-size:13.5px;margin:0 0 10px;}
.side-help p{font-size:12.5px;color:var(--text-dim);line-height:1.6;margin:0 0 16px;}
.step-mini{display:flex;gap:10px;margin-bottom:14px;}
.step-mini .n{width:20px;height:20px;border-radius:50%;background:#f1f0f6;color:var(--text-dim);font-size:10.5px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0;}
.step-mini .n.on{background:var(--grad-btn);color:#fff;}
.step-mini .tx{font-size:12px;color:var(--text-dim);padding-top:1px;}
.form-footer{display:flex;justify-content:flex-end;gap:10px;margin-top:24px;}

/* ---- Route cards (menu) ---- */
.hero-row{display:flex;justify-content:space-between;align-items:center;gap:40px;margin-bottom:30px;}
.menu-hero{text-align:left;max-width:620px;flex:1;min-width:0;}
.menu-hero .eyebrow{font-size:11.5px;color:var(--violet);font-weight:700;letter-spacing:.08em;text-transform:uppercase;margin-bottom:10px;}
.menu-hero h2{font-size:28px;margin:0 0 10px;font-weight:700;}
.menu-hero p{color:var(--text-dim);font-size:14.5px;line-height:1.6;margin:0;}
.hero-logo-big{flex-shrink:0;display:flex;align-items:center;justify-content:center;}
.hero-logo-big img{height:150px;width:auto;display:block;}
.route-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px;}
.route-card{padding:30px;position:relative;overflow:hidden;cursor:pointer;transition:transform .15s;text-decoration:none;color:inherit;display:block;}
.route-card:hover{transform:translateY(-2px);}
.route-card .bar{position:absolute;top:0;left:0;right:0;height:4px;}
.route-card .ic{width:52px;height:52px;border-radius:14px;display:flex;align-items:center;justify-content:center;margin-bottom:20px;color:#fff;}
.route-card .ic svg{width:26px;height:26px;}
.route-card h3{font-size:19px;margin:0 0 8px;}
.route-card p{font-size:13.5px;color:var(--text-dim);line-height:1.6;margin:0 0 22px;}
.route-card .tags{display:flex;flex-wrap:wrap;gap:6px;}
.route-card .tag{font-size:11px;background:#f4f3fa;color:var(--text-dim);padding:4px 10px;border-radius:20px;}

/* ---- Result page ---- */
.res-header{display:flex;justify-content:space-between;align-items:flex-start;padding:24px;margin-bottom:16px;gap:20px;}
.res-header img{height:26px;width:auto;margin-bottom:14px;display:block;}
.res-header .who h3{font-size:20px;margin:0 0 8px;font-weight:700;}
.res-header .who .meta{font-size:12.5px;color:var(--text-dim);display:flex;gap:14px;flex-wrap:wrap;align-items:center;}
.res-header .acts{display:flex;gap:10px;flex-shrink:0;}
.res-kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;margin-bottom:20px;}
.res-kpi{padding:16px;text-align:center;}
.res-kpi .v{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums;}
.res-kpi .l{font-size:11px;color:var(--text-faint);margin-top:4px;text-transform:uppercase;letter-spacing:.04em;}
.evidence-card{display:flex;justify-content:space-between;align-items:center;padding:16px 18px;border:1px solid var(--line);border-radius:12px;margin-bottom:10px;gap:14px;}
.evidence-card .l{display:flex;gap:12px;align-items:center;min-width:0;}
.evidence-card .ic{width:36px;height:36px;border-radius:9px;background:#f4f3fa;display:flex;align-items:center;justify-content:center;flex-shrink:0;color:var(--violet);}
.evidence-card .ic svg{width:18px;height:18px;}
.evidence-card .name{font-size:13.5px;font-weight:600;}
.evidence-card .sub{font-size:11.5px;color:var(--text-faint);}
.evidence-card .r{display:flex;align-items:center;gap:14px;flex-shrink:0;}
.rec-box{padding:20px;border-radius:12px;background:rgba(105,65,244,0.05);border:1px solid rgba(105,65,244,0.18);display:flex;gap:14px;}
.rec-box .ic{width:38px;height:38px;border-radius:10px;background:var(--grad-btn);display:flex;align-items:center;justify-content:center;flex-shrink:0;color:#fff;}
.rec-box .ic svg{width:19px;height:19px;}
.rec-box h4{font-size:14px;margin:0 0 6px;}
.rec-box p{font-size:12.5px;color:var(--text-dim);line-height:1.6;margin:0;}
.kv-row{display:flex;justify-content:space-between;gap:16px;font-size:12.5px;padding:9px 0;border-top:1px solid var(--line);}
.kv-row:first-child{border-top:none;}
.kv-row span:first-child{color:var(--text-faint);}
.kv-row span:last-child{text-align:right;font-weight:500;color:var(--text);}

/* ---- Featured source card (results) ---- */
.featured-card{border:1px solid var(--line);border-radius:14px;padding:20px;margin-bottom:14px;background:#fff;box-shadow:var(--shadow);}
.featured-card .fhead{display:flex;align-items:center;gap:12px;margin-bottom:10px;flex-wrap:wrap;}
.featured-card .fhead h3{font-size:15px;margin:0;font-weight:700;flex:1;min-width:0;}
.featured-card .fmeta{font-size:11.5px;color:var(--text-faint);margin-bottom:12px;}
.featured-card .fsum{font-size:13px;color:var(--text-dim);line-height:1.6;margin:0 0 12px;}
.featured-card .fimg{border:1px solid var(--line);border-radius:10px;overflow:hidden;margin-top:12px;}
.featured-card .fimg img{width:100%;display:block;}
.src-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;}
.ochip{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:12px 14px;border:1px solid var(--line);border-radius:11px;background:#fff;}
.ochip .on{font-size:13px;font-weight:600;color:var(--text);min-width:0;}
.ochip .os{font-size:11px;color:var(--text-faint);margin-top:2px;}
.cat-group{margin-bottom:22px;}
.cat-group .cat-h{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--text-faint);margin:0 0 12px;display:flex;align-items:center;gap:8px;}
.cat-group .cat-h .dot{width:7px;height:7px;border-radius:50%;background:var(--violet);}

/* ---- Timeline ---- */
.timeline{position:relative;padding-left:22px;}
.timeline::before{content:'';position:absolute;left:5px;top:6px;bottom:6px;width:1px;background:var(--line);}
.tl-item{position:relative;padding-bottom:20px;}
.tl-item::before{content:'';position:absolute;left:-22px;top:3px;width:9px;height:9px;border-radius:50%;background:var(--cyan);}
.tl-item .t{font-size:11px;color:var(--text-faint);}
.tl-item .d{font-size:13px;color:var(--text);margin-top:3px;}

/* ---- Tree (beneficiarios / reps) ---- */
.tree-wrap{display:flex;flex-direction:column;align-items:center;gap:6px;}
.tree-node{background:#fff;border:1px solid var(--line);border-radius:12px;padding:14px 20px;text-align:center;box-shadow:var(--shadow);}
.tree-node b{font-size:13.5px;}
.tree-node span{display:block;font-size:11.5px;color:var(--text-faint);margin-top:2px;}
.tree-branch{display:flex;gap:20px;margin-top:6px;flex-wrap:wrap;justify-content:center;}
.tree-line{width:1px;height:22px;background:var(--line);}

/* ---- Progress bar (NIT people) ---- */
.pbar{height:8px;background:#f1f0f6;border-radius:5px;overflow:hidden;}
.pbar>i{display:block;height:100%;border-radius:5px;background:var(--grad-btn);transition:width .4s;}

/* ---- Toast ---- */
.toast{position:fixed;bottom:26px;left:50%;transform:translate(-50%,20px);background:#171522;color:#fff;padding:13px 20px;border-radius:11px;font-size:13px;font-weight:500;box-shadow:0 12px 30px rgba(0,0,0,0.25);opacity:0;pointer-events:none;transition:all .25s;z-index:1100;display:flex;align-items:center;gap:10px;}
.toast.show{opacity:1;transform:translate(-50%,0);}
.toast .dot{width:8px;height:8px;border-radius:50%;background:#22c55e;flex-shrink:0;}

/* ---- Auth (login / centered) ---- */
.auth-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;
  background:radial-gradient(1200px 600px at 15% -10%,rgba(105,65,244,.10),transparent 60%),
             radial-gradient(1000px 500px at 100% 110%,rgba(29,229,233,.10),transparent 55%),var(--bg);}
.auth-card{width:100%;max-width:420px;background:#fff;border:1px solid var(--line);border-radius:var(--radius);box-shadow:0 20px 60px rgba(17,24,39,.10);padding:34px 32px;}
.auth-card .auth-logo{height:56px;width:auto;display:block;margin:0 auto 22px;}
.auth-card h1{font-size:19px;font-weight:700;text-align:center;margin:0 0 4px;}
.auth-card .auth-sub{font-size:13px;color:var(--text-dim);text-align:center;margin:0 0 24px;line-height:1.55;}
.auth-card .field{margin-bottom:14px;}
.auth-card .btn{width:100%;justify-content:center;}
.auth-divider{display:flex;align-items:center;gap:12px;color:var(--text-faint);font-size:11px;text-transform:uppercase;letter-spacing:.08em;margin:20px 0;}
.auth-divider::before,.auth-divider::after{content:'';flex:1;height:1px;background:var(--line);}
.auth-msg{font-size:12.5px;padding:10px 12px;border-radius:9px;margin-bottom:14px;line-height:1.45;}
.auth-msg.err{background:rgba(239,68,68,.08);color:#b91c1c;border:1px solid rgba(239,68,68,.2);}
.auth-msg.ok{background:rgba(34,197,94,.10);color:#15803d;border:1px solid rgba(34,197,94,.22);}

/* ---- Utilities ---- */
.mono{font-family:'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace;}
.spin{width:16px;height:16px;border:2px solid rgba(105,65,244,.25);border-top-color:var(--violet);border-radius:50%;display:inline-block;animation:spin .7s linear infinite;vertical-align:-3px;}
@keyframes spin{to{transform:rotate(360deg);}}

/* ---- Responsive ---- */
@media (max-width:1000px){
  .app{grid-template-columns:1fr;}
  .sidebar{position:relative;height:auto;display:flex;flex-wrap:wrap;padding:16px;}
  .nav-group{margin-right:14px;}
  .kpi-grid,.route-grid,.res-kpis,.field-row,.check-grid,.form-shell,.src-grid{grid-template-columns:1fr;}
  .hero-row{flex-direction:column;align-items:flex-start;}
  .main{padding:20px;}
}
"""

# ============================================================================
#  Iconos SVG (stroke=currentColor, 16px) para el nav
# ============================================================================
_ICONS = {
    "search": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>',
    "user": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21a8 8 0 1 0-16 0"/><circle cx="12" cy="7" r="4"/></svg>',
    "building": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="2" width="16" height="20" rx="2"/><path d="M9 22v-4h6v4M9 6h.01M15 6h.01M9 10h.01M15 10h.01M9 14h.01M15 14h.01"/></svg>',
    "users": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13A4 4 0 0 1 16 11"/></svg>',
    "credit": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="5" width="20" height="14" rx="2"/><line x1="2" y1="10" x2="22" y2="10"/><path d="M7 15h3"/></svg>',
    "logout": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/></svg>',
}


def head_open(title: str) -> str:
    """Devuelve <!doctype> + <head> con la CSS del tema embebida (envuelta en raw)."""
    return (
        "<!doctype html>\n<html lang=\"es\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<title>" + title + "</title>\n<style>{% raw %}" + THEME_CSS +
        "{% endraw %}</style>\n</head>\n"
    )


def _nav_item(active: str, key: str, href: str, icon: str, label: str) -> str:
    cls = "nav-item active" if active == key else "nav-item"
    return ('<a class="' + cls + '" href="' + href + '">' + _ICONS.get(icon, "") +
            "<span>" + label + "</span></a>")


def sidebar(active: str = "") -> str:
    """Sidebar Jinja (usa current_user). `active` marca el nav item vigente."""
    return (
        '<aside class="sidebar">\n'
        '  <div class="brand"><div class="wordmark">' + WORDMARK + '</div></div>\n'
        '  <div class="nav-group">\n'
        '    <p class="nav-label">General</p>\n'
        + _nav_item(active, "busqueda", "/", "search", "Búsqueda automatizada") + "\n"
        '  </div>\n'
        '  <div class="nav-group">\n'
        '    <p class="nav-label">Verificación</p>\n'
        + _nav_item(active, "persona", "/?tab=persona", "user", "Verificar persona") + "\n"
        + _nav_item(active, "empresa", "/?tab=empresa", "building", "Verificar empresa") + "\n"
        '  </div>\n'
        '  <div class="nav-group">\n'
        '    <p class="nav-label">Riesgo</p>\n'
        + _nav_item(active, "credito", "/credito", "credit", "Perfil crediticio") + "\n"
        '  </div>\n'
        '  {% if current_user and current_user.rol in ["admin", "jefe_cartera", "ejecutivo"] %}\n'
        '  <div class="nav-group">\n'
        '    <p class="nav-label">Gestión</p>\n'
        + _nav_item(active, "cartera", "/cartera", "credit", "Cartera — Aprobaciones") + "\n"
        '  </div>\n'
        '  {% endif %}\n'
        '  {% if current_user and current_user.rol in ["admin", "jefe_cartera"] %}\n'
        '  <div class="nav-group">\n'
        '    <p class="nav-label">Sistema</p>\n'
        + _nav_item(active, "usuarios", "/auth/admin/users", "users", "Usuarios y roles") + "\n"
        '  </div>\n'
        '  {% endif %}\n'
        '  <div class="nav-group">\n'
        '    <p class="nav-label">Sesión</p>\n'
        + _nav_item(active, "logout", "/auth/logout", "logout", "Cerrar sesión") + "\n"
        '  </div>\n'
        '  <div class="sidebar-foot">' + BRAND_NAME + '<br>' + BRAND_TAGLINE + '</div>\n'
        '</aside>\n'
    )


def shell_open(active: str, page_title: str, crumb: str) -> str:
    """<body> + sidebar + <main> + topbar. Continúa con el contenido de la página."""
    who = (
        '<div class="who">\n'
        '  {% if current_user %}<span>{{ current_user.email }}'
        ' · {{ (current_user.rol or "").title() }}</span>{% endif %}\n'
        '  <div class="avatar-sm"></div>\n'
        '</div>\n'
    )
    return (
        '<body>\n<div class="app">\n' + sidebar(active) +
        '<main class="main">\n'
        '  <div class="topbar"><h1>' + page_title + '</h1>\n' + who + '  </div>\n'
        '  <p class="crumbs"><b>' + BRAND_NAME + '</b> &middot; ' + crumb + '</p>\n'
    )


SHELL_CLOSE = "</main>\n</div>\n</body>\n</html>"


def page(title: str, active: str, page_title: str, crumb: str, body: str,
         scripts: str = "") -> str:
    """Compone una página completa: head + shell + body + scripts + cierre."""
    return (head_open(title) + shell_open(active, page_title, crumb) + body +
            (("\n<script>\n" + scripts + "\n</script>\n") if scripts else "") +
            SHELL_CLOSE)
