# Themeiversal

**One palette system, every app.** Themeiversal turns easy-to-read YAML
palettes into matching color schemes for iTerm2, VS Code / VSCodium,
BBEdit, Firefox, Apple Terminal, KDE Plasma, GNOME, WindowMaker, and
macOS — with first-class dark/light support.

**Zensicle** is Themeiversal's documentation site: a single generated
HTML page (`dist/docs/index.html`) with a **live theme picker**. Visitors
choose a palette, a filter, and dark/light, and the code sample, UI mock,
and swatches recolor instantly in their browser. It's published to
GitHub Pages by CI.

## The model

```
palette (palettes/*.yml)   plain hex colors, _pd/_pl dark/light variants
filter  (filters/*.yml)   math applied ON TOP of a palette:
                             (background; temp:-0.15; opacity:0.5)
map     (maps/*.yml)       per-app translation + switching strategy
scheme  =  palette × filter  (e.g. ember-frosty)
```

Filter operations: `temp` (-1..1), `hue` (±180°), `sat` / `brightness`
(0..2 multipliers), `tint:(color; amt:x)`, `opacity` (toward the
background), `contrast`, `alpha`. `all:` applies first; per-color
entries chain on top.

| Target | Switching |
|---|---|
| VS Code / VSCodium (extension, both themes) | auto via OS |
| Firefox (extension, `theme` + `dark_theme`) | auto via OS |
| BBEdit (scheme pair; BBEdit remembers per-mode selection) | auto via macOS |
| KDE Plasma (`.colors` pair, assigned in settings) | auto via Plasma |
| GNOME (gtk-3.0/gtk-4.0 theme dirs) | auto via GNOME |
| iTerm2 / Terminal.app (profile pairs) | scripted (extras/) |
| WindowMaker (`.style` pair) | manual |
| macOS (accent cheat sheets) | n/a |

## Layout

```
themeiversal/
├── themeiversal.yml          # global config (version, Firefox header art)
├── palettes/ember.yml        # base palette (_pd/_pl hex)
├── filters/                  # frosty, parchment — color math
├── maps/                     # iterm2, terminal, bbedit, vscode, firefox,
│                             # kde, gnome, windowmaker
├── scripts/
│   ├── generate.py           # theme generator (stdlib + PyYAML)
│   └── gen_docs.py           # Zensicle docs site w/ live theme picker
├── extras/                   # macOS appearance watcher for iTerm2/Terminal
└── .github/workflows/build.yml  # check → build → validate → zip → release
                                  + deploy docs to GitHub Pages
```

## Building

```bash
pip install pyyaml
python scripts/generate.py --check   # resolve every ref in every scheme
python scripts/generate.py           # build all schemes + docs into dist/
open dist/docs/index.html            # try the theme picker locally
```

## CI

On pushes touching palettes/filters/maps/scripts, the workflow:
1. runs `--check` (every map reference resolves through every filter,
   both modes — failures are precise),
2. generates all schemes for all targets plus the docs page,
3. validates output (JSON parses, plists well-formed, docs present),
4. uploads `themeiversal-<version>-bundle.zip` as an artifact, attached
   to `v*` tag releases,
5. deploys `dist/docs/` to GitHub Pages — the live Zensicle site.

## Installing schemes

- **VS Code**: copy `dist/vscode/themeiversal-ember-frosty/` to
  `~/.vscode/extensions/`; enable `window.autoDetectColorScheme` and set
  preferredDark/LightColorTheme to the scheme's theme names. Publish
  via `vsce` (Open VSX for VSCodium).
- **Firefox**: `dist/firefox/ember-frosty/` — one extension, auto
  light/dark; sign via AMO for permanent install.
- **BBEdit**: copy both `.bbColorScheme` files; select each once in its
  matching OS appearance.
- **iTerm2**: import both presets; use the extras/ watcher to switch.
- **Terminal.app**: import both `.terminal` files.
- **KDE**: copy `.colors` to `~/.local/share/color-schemes/`, assign in
  System Settings → Colors.
- **GNOME**: copy theme dirs to `~/.themes/`, pick via GNOME Tweaks.
- **WindowMaker**: copy `.style` files to
  `~/GNUstep/Library/WindowMaker/Styles/`.
- **macOS**: see `dist/macos/` cheat sheets.

## License

MIT — see `LICENSE`.