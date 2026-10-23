#!/usr/bin/env python3
"""
Themeiversal theme generator.

Model:
  palette (palettes/*.yml)   plain hex colors, _pd/_pl variants
  filter  (filters/*.yml)   math applied ON TOP of a palette:
      (background; temp:-0.15; opacity:0.5)
  map     (maps/*.yml)       app translation + switching strategy

A palette + filter = a "scheme" (e.g. ember-frosty). Each scheme is
built for every target; maps decide manual (two artifacts, dark/light)
vs auto (one dual-mode artifact).

  manual -> iterm2, terminal, bbedit, kde, gnome, windowmaker
  auto   -> vscode (extension w/ both themes), firefox (theme +
            dark_theme in one manifest)

Outputs to dist/ and zips Themeiversal-<version>-bundle.zip.
Only stdlib + PyYAML required.
"""
import argparse
import colorsys
import json
import plistlib
import re
import struct
import zipfile
import zlib
from pathlib import Path

import yaml

SUFFIX = {"dark": "_pd", "light": "_pl"}
UI_THEME = {"dark": "vs-dark", "light": "vs"}
MODES = ("dark", "light")


# ----------------------------------------------------------------------------
# Color parsing / math
# ----------------------------------------------------------------------------

def parse_hex(value):
    v = value.lstrip("#")
    if len(v) == 6:
        v += "ff"
    if len(v) != 8:
        raise ValueError(f"bad hex color: {value!r}")
    return (int(v[0:2], 16) / 255.0, int(v[2:4], 16) / 255.0,
            int(v[4:6], 16) / 255.0, int(v[6:8], 16) / 255.0)


def to_hex(rgb, a=1.0):
    return "#{:02x}{:02x}{:02x}{:02x}".format(
        round(rgb[0] * 255), round(rgb[1] * 255), round(rgb[2] * 255),
        round(a * 255))


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def op_temp(rgb, amount):
    """Cool (<0, toward blue) or warm (>0, toward red)."""
    t = amount
    r, g, b = rgb
    return (clamp(r + 0.10 * t), clamp(g + 0.02 * t), clamp(b - 0.10 * t))


def op_hue(rgb, degrees):
    h, l, s = colorsys.rgb_to_hls(*rgb)
    h = (h + degrees / 360.0) % 1.0
    return colorsys.hls_to_rgb(h, l, s)


def op_sat(rgb, mult):
    h, l, s = colorsys.rgb_to_hls(*rgb)
    return colorsys.hls_to_rgb(h, l, clamp(s * mult))


def op_brightness(rgb, mult):
    h, l, s = colorsys.rgb_to_hls(*rgb)
    return colorsys.hls_to_rgb(h, clamp(l * mult), s)


def blend(base, over, alpha):
    return tuple(b + (o - b) * alpha for b, o in zip(base, over))


TRANSFORM_RE = re.compile(
    r"\(\s*([A-Za-z0-9_]+)\s*(?:;\s*([A-Za-z0-9_]+)\s*:\s*"
    r"(\(?[^;)]+\)?|-?[\d.]+)\s*(?:;\s*[^)]+)?)?\s*\)")


class TransformError(ValueError):
    pass


def parse_transform(text):
    """'(name; op:val; op:(color;amt); ...)' -> (name, [(op, val), ...])"""
    text = text.strip()
    if not (text.startswith("(") and text.endswith(")")):
        raise TransformError(f"transform must be parenthesized: {text!r}")
    parts = [p.strip() for p in text[1:-1].split(";")]
    name = parts[0]
    ops = []
    for part in parts[1:]:
        if ":" not in part:
            raise TransformError(f"bad op {part!r} in {text!r}")
        op, val = part.split(":", 1)
        ops.append((op.strip(), val.strip()))
    return name, ops


class Scheme:
    """A base palette with a filter applied. Resolves color names
    per mode, applying any `(name; ops...)` transform lazily."""

    def __init__(self, palette, filt, mode):
        self.palette = palette          # raw palette dict
        self.filt = filt or {}          # raw filter dict or None
        self.mode = mode
        self.base = palette["colors"]
        self.name = palette["name"] + (f" {filt['name']}" if filt else "")
        self.id = palette["id"] + (f"-{filt['id']}" if filt else "")
        self._cache = {}
        if filt:
            self._all_ops = parse_transform(filt.get("all", "(all)"))[1] \
                if filt.get("all") else []
            self._filter_colors = filt.get("colors", {})

    # -- palette access ------------------------------------------------

    def raw(self, key):
        for cand in (key + SUFFIX[self.mode], key):
            if cand in self.base:
                return parse_hex(self.base[cand])
            if cand.startswith("#"):
                return parse_hex(cand)
        raise KeyError(f"palette has no color {key!r} ({self.mode})")

    # -- filter math ---------------------------------------------------

    def _apply_ops(self, rgb, alpha, ops, key):
        for op, val in ops:
            if op == "temp":
                rgb = op_temp(rgb, float(val))
            elif op == "hue":
                rgb = op_hue(rgb, float(val))
            elif op in ("sat", "saturation"):
                rgb = op_sat(rgb, float(val))
            elif op in ("brightness", "light"):
                rgb = op_brightness(rgb, float(val))
            elif op == "tint":
                m = re.match(r"\(\s*([A-Za-z0-9_]+)\s*;\s*amt:\s*([\d.]+)\s*\)", val)
                if not m:
                    raise TransformError(
                        f"tint needs (color; amt:x) â got {val!r}")
                tint_rgb = self.rgba(m.group(1))[:3] if not m.group(1).startswith("#") \
                    else parse_hex(m.group(1))[:3]
                rgb = blend(rgb, tint_rgb, float(m.group(2)))
            elif op in ("opacity", "contrast"):
                # blend/contrast toward the mode's background surface;
                # if we ARE the background, use its raw (unfiltered-so-far)
                # value to avoid infinite recursion.
                if key.startswith("background"):
                    bg = self.raw("background")[:3]
                else:
                    bg = self.rgba("background")[:3]
                if op == "opacity":
                    rgb = blend(rgb, bg, 1.0 - float(val))
                else:
                    rgb = tuple(clamp(c + (c - g) * (float(val) - 1.0))
                               for c, g in zip(rgb, bg))
            elif op == "alpha":
                alpha = clamp(alpha * float(val))
            else:
                raise TransformError(f"unknown op {op!r} for {key!r}")
        return rgb, alpha

    # -- resolution ----------------------------------------------------

    def rgba(self, key):
        if key in self._cache:
            return self._cache[key]
        if key.startswith("#"):
            rgba = parse_hex(key)
            self._cache[key] = rgba
            return rgba
        rgb, alpha = self.raw(key)[:3], self.raw(key)[3]
        if self.filt:
            rgb, alpha = self._apply_ops(rgb, alpha, self._all_ops, key)
            spec = self._filter_colors.get(key)
            if spec:
                _, ops = parse_transform(spec)
                # tint inside a per-color transform may reference colors
                rgb, alpha = self._apply_ops(rgb, alpha, ops, key)
        rgba = (rgb[0], rgb[1], rgb[2], alpha)
        self._cache[key] = rgba
        return rgba

    def hex(self, key, keep_alpha=False):
        r, g, b, a = self.rgba(key)
        return to_hex((r, g, b), a if keep_alpha else 1.0)

    def variant_name(self, mode=None):
        mode = mode or self.mode
        return f"{self.name} {'Dark' if mode == 'dark' else 'Light'}"

    def has(self, key):
        return key.startswith("#") or \
            any(key + s in self.base or key in self.base
                for s in SUFFIX.values())


# ----------------------------------------------------------------------------
# PNG writer (pure stdlib)
# ----------------------------------------------------------------------------

def write_png(path, width, height, pixels):
    raw = bytearray()
    for row in pixels:
        raw.append(0)
        for r, g, b in row:
            raw += bytes((round(r * 255), round(g * 255), round(b * 255)))

    def chunk(tag, data):
        payload = tag + data
        crc = zlib.crc32(payload) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + payload + struct.pack(">I", crc)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    Path(path).write_bytes(png)


# ----------------------------------------------------------------------------
# Manual-switching generators (one artifact per mode)
# ----------------------------------------------------------------------------

def component_color(rgba):
    return {"Red Component": rgba[0], "Green Component": rgba[1],
            "Blue Component": rgba[2], "Alpha Component": rgba[3]}


def gen_iterm2(scheme, mapping, out_dir):
    out = {k: component_color(scheme.rgba(ref))
           for k, ref in mapping["keys"].items()}
    path = out_dir / f"{scheme.id}-{scheme.mode}.itermcolors"
    with open(path, "wb") as fh:
        plistlib.dump(out, fh, fmt=plistlib.FMT_XML)
    return path


def gen_terminal(scheme, mapping, out_dir):
    window = {k: component_color(scheme.rgba(ref))
              for k, ref in mapping["keys"].items()}
    profile = {"name": scheme.variant_name(),
               "Window Settings": window, "type": "Window Background"}
    path = out_dir / f"{scheme.id}-{scheme.mode}.terminal"
    with open(path, "wb") as fh:
        plistlib.dump(profile, fh, fmt=plistlib.FMT_XML)
    return path


def gen_bbedit(scheme, mapping, out_dir):
    scheme_str = lambda c: "rgba({:.6f},{:.6f},{:.6f},{:.6f})".format(*c)
    out = {}
    for key, ref in mapping["keys"].items():
        out[key] = ref if isinstance(ref, bool) else scheme_str(scheme.rgba(ref))
    for key, ref in mapping.get("bblm", {}).items():
        out[key] = scheme_str(scheme.rgba(ref))
    path = out_dir / f"{scheme.id}-{scheme.mode}.bbColorScheme"
    with open(path, "wb") as fh:
        plistlib.dump(out, fh, fmt=plistlib.FMT_XML)
    return path


def gen_kde(scheme, mapping, out_dir):
    lines = ["[General]", f"Name={scheme.variant_name()}",
             "ColorSchemeVersion=1"]
    for group, keys in mapping["groups"].items():
        lines += ["", group]
        lines += [f"{k}={scheme.hex(ref)}" for k, ref in keys.items()]
    path = out_dir / f"{scheme.id}-{scheme.mode}.colors"
    path.write_text("\n".join(lines) + "\n")
    return path


def gen_gnome(scheme, mapping, out_dir):
    theme_dir = out_dir / scheme.variant_name().replace(" ", "")
    css = f"/* {scheme.variant_name()} â generated by Themeiversal. */\n"
    css += "".join(f"@define-color {n} {scheme.hex(ref)};\n"
                   for n, ref in mapping["defines"].items())
    for gtk_ver in ("gtk-3.0", "gtk-4.0"):
        d = theme_dir / gtk_ver
        d.mkdir(parents=True, exist_ok=True)
        (d / "gtk.css").write_text(css)
    return theme_dir


def gen_windowmaker(scheme, mapping, out_dir):
    lines = ["{"] + [f'  {k} = "{scheme.hex(ref)}";'
                     for k, ref in mapping["keys"].items()] + ["}"]
    path = out_dir / f"{scheme.id}-{scheme.mode}.style"
    path.write_text("\n".join(lines) + "\n")
    return path


# ----------------------------------------------------------------------------
# Auto-switching generators (one artifact, both modes)
# ----------------------------------------------------------------------------

def build_vscode_theme(scheme_data, mapping):
    theme = {"name": scheme_data.variant_name(),
             "type": scheme_data.mode,
             "colors": {}, "tokenColors": []}
    for key, ref in mapping["ui"].items():
        keep_alpha = str(ref).startswith("#") and len(str(ref)) > 7
        theme["colors"][key] = scheme_data.hex(ref, keep_alpha=keep_alpha)
    for spec in mapping.get("tokens", []):
        settings = {}
        if "foreground" in spec:
            settings["foreground"] = scheme_data.hex(spec["foreground"])
        if "background" in spec:
            settings["background"] = scheme_data.hex(spec["background"])
        if spec.get("fontStyle"):
            settings["fontStyle"] = spec["fontStyle"]
        bits = [b for b in ("bold", "italic") if spec.get(b)]
        if bits and "fontStyle" not in settings:
            settings["fontStyle"] = " ".join(bits)
        theme["tokenColors"].append(
            {"scope": spec["scope"], "settings": settings})
    return theme


def gen_vscode(scheme_pair, mapping, cfg, out_dir):
    """scheme_pair: {mode: Scheme} for the same palette+filter."""
    dark = scheme_pair["dark"]
    ext_id = f"themeiversal-{dark.id}"
    ext_dir = out_dir / ext_id
    themes_dir = ext_dir / "themes"
    themes_dir.mkdir(parents=True, exist_ok=True)
    contributed = []
    for mode in MODES:
        scheme = scheme_pair[mode]
        theme = build_vscode_theme(scheme, mapping)
        rel = f"themes/{scheme.id}-{mode}-color-theme.json"
        (ext_dir / rel).write_text(json.dumps(theme, indent=2) + "\n")
        contributed.append({"label": scheme.variant_name(),
                            "uiTheme": UI_THEME[mode], "path": rel})
    package = {
        "name": ext_id,
        "displayName": f"Themeiversal {dark.name}",
        "description": dark.filt.get("description", "") if dark.filt else
                       dark.palette.get("description", ""),
        "version": cfg.get("version", "0.1.0"),
        "publisher": "Themeiversal",
        "engines": {"vscode": "^1.60.0"},
        "categories": ["Themes"],
        "contributes": {"themes": contributed},
    }
    (ext_dir / "package.json").write_text(json.dumps(package, indent=2) + "\n")
    return ext_dir


def build_firefox_section(scheme, mapping, mode):
    colors = {key: {"rgb": [round(c * 255) for c in scheme.rgba(ref)[:3]]}
              for key, ref in mapping["colors"].items()}
    return {
        "images": {"headerURL": f"header-{'dark' if mode=='dark' else 'light'}.png"},
        "colors": colors,
        "properties": {"color_scheme": "dark" if mode == "dark" else "light"},
    }


def gen_firefox(scheme_pair, mapping, cfg, out_dir):
    dark = scheme_pair["dark"]
    theme_dir = out_dir / dark.id
    theme_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "manifest_version": 2,
        "name": f"Themeiversal {dark.name}",
        "version": cfg.get("version", "0.1.0"),
        "description": (dark.filt or dark.palette).get("description", ""),
        "theme": build_firefox_section(scheme_pair["light"], mapping, "light"),
        "dark_theme": build_firefox_section(dark, mapping, "dark"),
    }
    (theme_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    _gen_firefox_graphics(scheme_pair, cfg, theme_dir)
    return theme_dir


def _gen_firefox_graphics(scheme_pair, cfg, theme_dir):
    fx = cfg.get("firefox", {})
    W, H = fx.get("header_width", 2560), fx.get("header_height", 200)
    for mode in MODES:
        scheme = scheme_pair[mode]
        top, bottom = scheme.rgba("background")[:3], scheme.rgba("background_alt")[:3]
        orbs = [{"rgb": scheme.rgba(o["color"])[:3],
                 "radius": o.get("radius", 200),
                 "cx": o.get("x", 0.5) * W, "cy": o.get("y", 0.5) * H,
                 "alpha": o.get("alpha", 0.1)} for o in fx.get("orbs", [])]
        rows = []
        for y in range(H):
            t = y / max(H - 1, 1)
            base = tuple(a + (b - a) * t for a, b in zip(top, bottom))
            row = []
            for x in range(W):
                px = base
                for orb in orbs:
                    dx, dy = x - orb["cx"], y - orb["cy"]
                    d2 = dx * dx + dy * dy
                    if d2 < orb["radius"] ** 2:
                        fall = 1.0 - (d2 / orb["radius"] ** 2) ** 0.5
                        px = blend(px, orb["rgb"], orb["alpha"] * fall)
                row.append(px)
            rows.append(row)
        write_png(theme_dir / f"header-{mode}.png", W, H, rows)

    S = fx.get("icon_size", 128)
    bg = scheme_pair["dark"].rgba("background_alt")[:3]
    disc = scheme_pair["dark"].rgba("accent")[:3]
    rows = [[disc if (x - S/2)**2 + (y - S/2)**2 <= (S*0.36)**2 else bg
             for x in range(S)] for y in range(S)]
    write_png(theme_dir / "icon.png", S, S, rows)


# ----------------------------------------------------------------------------
# macOS cheat sheet per scheme
# ----------------------------------------------------------------------------

def gen_macos(scheme_pair, out_dir):
    dark = scheme_pair["dark"]
    lines = [f"# Themeiversal {dark.name} â macOS", "",
             "macOS has no custom system accent colors; use the closest",
             "built-in presets plus the Color Picker values below.",
             "", "## Color Picker values", ""]
    for role in ("accent", "accent_2", "accent_3", "background",
                 "selection_bg", "error", "warning", "added"):
        lines.append(f"- {role}: dark `{dark.hex(role)}` Â· "
                     f"light `{scheme_pair['light'].hex(role)}`")
    lines += ["",
              "## BBEdit (auto-switches with the OS): install both "
              "schemes, select each once in its matching appearance.",
              "",
              "## iTerm2 / Terminal.app: no OS hook â both variants "
              "ship; swap via extras/ watcher or manually."]
    path = out_dir / f"{dark.id}.md"
    path.write_text("\n".join(lines) + "\n")
    return path


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def load_yaml(path):
    with open(path) as fh:
        return yaml.safe_load(fh)


MANUAL_TARGETS = {
    "iterm2": gen_iterm2, "terminal": gen_terminal, "bbedit": gen_bbedit,
    "kde": gen_kde, "gnome": gen_gnome, "windowmaker": gen_windowmaker,
}
AUTO_TARGETS = ("vscode", "firefox")


def collect_refs(mapping):
    refs = []
    for value in mapping.values():
        items = value.values() if isinstance(value, dict) else \
                (value if isinstance(value, list) else [value])
        for v in items:
            if isinstance(v, dict):
                refs += [x for x in v.values() if isinstance(x, str)]
            elif isinstance(v, str):
                refs.append(v)
    return refs


def build_scheme_pairs(palettes, filters):
    """-> [ {mode: Scheme}, ... ] one entry per paletteÃfilterÃ{modes}."""
    pairs = []
    for pal in palettes:
        variants = [None] + filters  # None = unfiltered base
        for filt in variants:
            pair = {mode: Scheme(pal, filt, mode) for mode in MODES}
            pairs.append(pair)
    return pairs


def main():
    ap = argparse.ArgumentParser(description="Generate Themeiversal themes")
    ap.add_argument("--repo", type=Path,
                    default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    repo = args.repo
    cfg = load_yaml(repo / "themeiversal.yml")
    out = Path(args.out) if args.out else repo / cfg.get("output_dir", "dist")
    mappings = {p.stem: load_yaml(p) for p in sorted((repo / "maps").glob("*.yml"))}
    palettes = [load_yaml(p) for p in sorted((repo / "palettes").glob("*.yml"))]
    filters = [load_yaml(p) for p in sorted((repo / "filters").glob("*.yml"))]
    scheme_pairs = build_scheme_pairs(palettes, filters)

    # --check: force-resolve every reference in every scheme.
    if args.check:
        failures = 0
        for pair in scheme_pairs:
            for mode in MODES:
                scheme = pair[mode]
                for target, mapping in mappings.items():
                    for ref in collect_refs(mapping):
                        try:
                            scheme.rgba(ref)
                        except (KeyError, TransformError, ValueError) as e:
                            failures += 1
                            print(f"FAIL {scheme.id}/{mode}/{target}: {e}")
        print("check: OK" if not failures else f"check: {failures} failure(s)")
        raise SystemExit(1 if failures else 0)

    for d in list(MANUAL_TARGETS) + list(AUTO_TARGETS) + ["macos", "docs"]:
        (out / d).mkdir(parents=True, exist_ok=True)

    print(f"Themeiversal v{cfg.get('version', '?')} -> {out}")
    generated = []
    for pair in scheme_pairs:
        print(f"  scheme: {pair['dark'].name}")
        for target, gen in MANUAL_TARGETS.items():
            for mode in MODES:
                generated.append(gen(pair[mode], mappings[target], out / target))
        generated.append(gen_vscode(pair, mappings["vscode"], cfg, out / "vscode"))
        generated.append(gen_firefox(pair, mappings["firefox"], cfg, out / "firefox"))
        generated.append(gen_macos(pair, out / "macos"))

    # Zensicle docs site with live theme picker (all schemes on one page).
    from gen_docs import gen_docs
    generated.append(gen_docs(scheme_pairs, out / "docs"))
    print("  docs: dist/docs/index.html")

    bundle = out / f"themeiversal-{cfg.get('version', '0.0.0')}-bundle.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in generated:
            if path.is_dir():
                for p in sorted(path.rglob("*")):
                    if p.is_file():
                        zf.write(p, p.relative_to(out))
            else:
                zf.write(path, path.relative_to(out))
    print(f"  bundle: {bundle}")
    print("done.")


if __name__ == "__main__":
    main()