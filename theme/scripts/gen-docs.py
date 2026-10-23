#!/usr/bin/env python3
"""Docs generator for Themeiversal (Zensicle docs site with live theme picker).

Emits dist/docs/index.html — a single self-contained page embedding every
scheme's resolved colors as JSON. The in-page "theme picker" lets visitors
switch palette, filter, and light/dark live; the code sample, UI mock, and
swatch grid all recolor instantly. No server, no build tooling.
"""
import json
import re

# Semantic roles surfaced in the picker (resolved per mode).
ROLES = [
    "background", "background_alt", "background_sel", "selection_bg",
    "line_highlight", "border", "foreground", "foreground_dim", "comment",
    "accent", "accent_2", "accent_3", "keyword", "string", "number",
    "function", "type", "constant", "operator", "variable", "tag",
    "attribute", "added", "modified", "removed", "error", "warning",
    "cursor",
]

TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Themeiversal — Zensicle docs</title>
<style>
  :root { --r: 8px; }
  * { box-sizing: border-box; }
  body {
    margin: 0; font: 15px/1.55 -apple-system, "Segoe UI", sans-serif;
    background: var(--background); color: var(--foreground);
    transition: background .25s, color .25s;
  }
  header {
    display: flex; flex-wrap: wrap; gap: 12px; align-items: center;
    padding: 14px 24px; background: var(--background_alt);
    border-bottom: 1px solid var(--border);
  }
  header h1 { font-size: 18px; margin: 0 16px 0 0; letter-spacing: .3px; }
  header h1 b { color: var(--accent); }
  select, button {
    font: inherit; padding: 6px 12px; border-radius: var(--r);
    border: 1px solid var(--border); background: var(--background_sel);
    color: var(--foreground); cursor: pointer;
  }
  button.active { background: var(--accent); color: var(--background); border-color: var(--accent); }
  main { max-width: 980px; margin: 0 auto; padding: 24px; }
  h2 { font-size: 14px; text-transform: uppercase; letter-spacing: 1px;
       color: var(--foreground_dim); margin: 28px 0 10px; }
  /* editor mock */
  .editor { border-radius: var(--r); border: 1px solid var(--border);
    background: var(--background); overflow: hidden; }
  .titlebar { display: flex; gap: 6px; padding: 8px 12px;
    background: var(--background_alt); border-bottom: 1px solid var(--border); }
  .dot { width: 10px; height: 10px; border-radius: 50%; }
  .tabs { display: flex; background: var(--background_alt);
    border-bottom: 1px solid var(--border); }
  .tab { padding: 6px 16px; font-size: 13px; color: var(--foreground_dim); }
  .tab.active { color: var(--foreground); box-shadow: inset 0 2px 0 var(--accent); }
  pre { margin: 0; padding: 16px 20px; overflow-x: auto; font: 13px/1.6 ui-monospace, "SF Mono", Menlo, monospace; }
  pre .ln { color: var(--comment); user-select: none; margin-right: 18px; }
  .tok-comment { color: var(--comment); font-style: italic; }
  .tok-keyword { color: var(--keyword); }
  .tok-string { color: var(--string); }
  .tok-number { color: var(--number); }
  .tok-function { color: var(--function); }
  .tok-type { color: var(--type); }
  .tok-constant { color: var(--constant); }
  .tok-operator { color: var(--operator); }
  .tok-variable { color: var(--variable); }
  .tok-attribute { color: var(--attribute); }
  .tok-tag { color: var(--tag); }
  .diagnostic { color: var(--warning); }
  .diagnostic.err { color: var(--error); }
  .added { color: var(--added); } .removed { color: var(--removed); }
  .modified { color: var(--modified); }
  /* swatches */
  .swatches { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 10px; }
  .sw { border: 1px solid var(--border); border-radius: var(--r); padding: 8px; }
  .sw .chip { height: 34px; border-radius: 5px; margin-bottom: 6px; }
  .sw .label { font-size: 12px; color: var(--foreground_dim); }
  .sw .hex { font-size: 12px; font-family: ui-monospace, Menlo, monospace; color: var(--foreground); }
  /* UI mock */
  .ui { display: grid; grid-template-columns: 120px 1fr; gap: 0;
    border: 1px solid var(--border); border-radius: var(--r); overflow: hidden; }
  .ui .side { background: var(--background_alt); padding: 10px; display: grid; gap: 4px; }
  .ui .side div { padding: 4px 8px; border-radius: 5px; font-size: 13px; color: var(--foreground_dim); }
  .ui .side div.active { background: var(--background_sel); color: var(--foreground); }
  .ui .content { padding: 16px; background: var(--background); }
  .btn { display: inline-block; padding: 6px 14px; border-radius: 5px;
    background: var(--accent); color: var(--background); font-size: 13px; }
  .btn.secondary { background: var(--background_sel); color: var(--foreground); }
  .field { margin-top: 14px; border: 1px solid var(--border); border-radius: 5px;
    padding: 8px; background: var(--background_sel); color: var(--foreground_dim); font-size: 13px; }
  .badge { display: inline-block; margin-left: 8px; padding: 1px 8px; border-radius: 10px;
    background: var(--accent_3); color: var(--background); font-size: 11px; }
  footer { padding: 24px; text-align: center; color: var(--comment); font-size: 13px; }
</style>
</head>
<body>
<header>
  <h1><b>Themeiversal</b> · Zensicle docs</h1>
  <select id="scheme" aria-label="Scheme"></select>
  <button id="mode-dark"  title="Dark mode">Dark</button>
  <button id="mode-light" title="Light mode">Light</button>
  <button id="mode-auto" class="active" title="Follow this browser's preference">Auto</button>
</header>
<main>
  <h2>Editor preview</h2>
  <div class="editor">
    <div class="titlebar">
      <span class="dot" style="background:var(--removed)"></span>
      <span class="dot" style="background:var(--warning)"></span>
      <span class="dot" style="background:var(--added)"></span>
    </div>
    <div class="tabs"><div class="tab active">palette.yml</div><div class="tab">generate.py</div></div>
<pre><span class="ln"> 1</span><span class="tok-comment"># pick a scheme up top — this page re-themes instantly</span>
<span class="ln"> 2</span><span class="tok-keyword">def</span> <span class="tok-function">brew</span><span class="tok-operator">(</span><span class="tok-variable">filter</span><span class="tok-operator">=</span><span class="tok-string">"frosty"</span><span class="tok-operator">,</span> <span class="tok-variable">temp</span><span class="tok-operator">=</span><span class="tok-number">-0.15</span><span class="tok-operator">):</span>
<span class="ln"> 3</span>    <span class="tok-variable">base</span> <span class="tok-operator">=</span> <span class="tok-type">Palette</span><span class="tok-operator">.</span><span class="tok-function">load</span><span class="tok-operator">(</span><span class="tok-string">"ember"</span><span class="tok-operator">)</span>
<span class="ln"> 4</span>    <span class="tok-keyword">if</span> <span class="tok-variable">temp</span> <span class="tok-operator">&lt;</span> <span class="tok-number">0</span><span class="tok-operator">:</span>
<span class="ln"> 5</span>        <span class="tok-constant">DEBUG</span> <span class="tok-operator">and</span> <span class="tok-function">log</span><span class="tok-operator">(</span><span class="tok-string">"cooling %s"</span><span class="tok-operator">,</span> <span class="tok-variable">base</span><span class="tok-operator">.</span><span class="tok-attribute">id</span><span class="tok-operator">)</span>
<span class="ln"> 6</span>    <span class="tok-keyword">return</span> <span class="tok-variable">base</span><span class="tok-operator">.</span><span class="tok-function">transform</span><span class="tok-operator">(</span><span class="tok-variable">temp</span><span class="tok-operator">=</span><span class="tok-variable">temp</span><span class="tok-operator">)</span>  <span class="diagnostic err">TODO: saturate</span>
<span class="ln"> 7</span>
<span class="ln"> 8</span><span class="tok-comment"># diffs</span>
<span class="ln"> 9</span><span class="added">+ added: "#a8c47a"</span>
<span class="ln">10</span><span class="removed">- removed: "#e07085"</span>
<span class="ln">11</span><span class="modified">~ modified: "#dcb16a"</span></pre>
  </div>

  <h2>Interface mock</h2>
  <div class="ui">
    <div class="side">
      <div class="active">Files</div><div>Search</div><div>Source</div><div>Run</div>
    </div>
    <div class="content">
      <span class="btn">Generate themes</span>
      <span class="btn secondary">View palette</span>
      <span class="badge">2 schemes</span>
      <div class="field">Search or run a command…</div>
    </div>
  </div>

  <h2>Swatches</h2>
  <div class="swatches" id="swatches"></div>
</main>
<footer>Generated by Themeiversal — palettes are plain YAML, filters are math, themes are output for iTerm2, VS Code, BBEdit, Firefox, KDE, GNOME, WindowMaker and more.</footer>
<script>
const SCHEMES = __DATA__;
let mode = matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
let modeOverride = null;

const sel = document.getElementById("scheme");
SCHEMES.forEach((s, i) => {
  const o = document.createElement("option");
  o.value = i; o.textContent = s.name; sel.appendChild(o);
});

function apply() {
  const s = SCHEMES[sel.value];
  const m = modeOverride || mode;
  for (const role of s.roles) {
    document.documentElement.style.setProperty(`--${role}`, s.modes[m][role]);
  }
  renderSwatches(s, m);
  document.getElementById("mode-dark").classList.toggle("active", modeOverride === "dark");
  document.getElementById("mode-light").classList.toggle("active", modeOverride === "light");
  document.getElementById("mode-auto").classList.toggle("active", modeOverride === null);
}

function renderSwatches(s, m) {
  const box = document.getElementById("swatches");
  box.innerHTML = "";
  for (const role of s.roles) {
    const d = document.createElement("div");
    d.className = "sw";
    const hex = s.modes[m][role];
    d.innerHTML = `<div class="chip" style="background:${hex}"></div>
      <div class="label">${role}</div><div class="hex">${hex}</div>`;
    box.appendChild(d);
  }
}

sel.addEventListener("change", apply);
document.getElementById("mode-dark").onclick  = () => { modeOverride = "dark";  apply(); };
document.getElementById("mode-light").onclick = () => { modeOverride = "light"; apply(); };
document.getElementById("mode-auto").onclick  = () => { modeOverride = null;    apply(); };
matchMedia("(prefers-color-scheme: light)").addEventListener("change", e => {
  mode = e.matches ? "light" : "dark"; if (!modeOverride) apply();
});
apply();
</script>
</body>
</html>
"""


def gen_docs(scheme_pairs, out_dir):
    """scheme_pairs: [{mode: Scheme}, ...] -> dist/docs/index.html."""
    data = []
    for pair in scheme_pairs:
        dark = pair["dark"]
        modes = {m: {role: pair[m].hex(role) for role in ROLES}
                 for m in ("dark", "light")}
        data.append({"id": dark.id, "name": dark.name,
                     "roles": ROLES, "modes": modes})
    html = TEMPLATE.replace("__DATA__", json.dumps(data, indent=1))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "index.html"
    path.write_text(html)
    return path