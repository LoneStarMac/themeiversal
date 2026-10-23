#!/usr/bin/env python3
"""
Themeiversal theme generator.

Reads palette YAML files (palettes/*.yml) and target maps (maps/*.yml)
and emits, per palette:
  - iTerm2 color preset        -> dist/iterm2/<palette>.itermcolors
  - Apple Terminal profile     -> dist/terminal/<palette>.terminal
  - BBEdit color scheme        -> dist/bbedit/<palette>.bbColorScheme
  - VS Code / VSCodium theme   -> dist/vscode/<palette>-color-theme.json
  - Firefox static theme       -> dist/firefox/<palette>/ (manifest + PNGs)
  - macOS accent cheat sheet   -> dist/macos/<palette>.md
  - Zipped bundle of the above -> dist/themeiversal-<version>-bundle.zip

Only the Python standard library + PyYAML are required.
"""
import argparse
import json
import plistlib
import struct
import zipfile
from pathlib import Path

import yaml


# ----------------------------------------------------------------------------
# Color helpers
# ----------------------------------------------------------------------------

def parse_hex(value):
    """'#rrggbb' or '#rrggbbaa' -> (r, g, b, a) floats 0..1."""
    v = value.lstrip("#")
    if len(v) == 6:
        v += "ff"
    if len(v) != 8:
        raise ValueError(f"bad hex color: {value!r}")
    r = int(v[0:2], 16) / 255.0
    g = int(v[2:4], 16) / 255.0
    b = int(v[4:6], 16) / 255.0
    a = int(v[6:8], 16) / 255.0
    return r, g, b, a


def to_hex(rgb, a=1.0):
    return "#{:02x}{:02x}{:02x}{:02x}".format(
        round(rgb[0] * 255), round(rgb[1] * 255), round(rgb[2] * 255),
        round(a * 255),
    )


def bbedit_color(rgb, a=1.0):
    """BBEdit scheme color string format."""
    return "rgba({:.6f},{:.6f},{:.6f},{:.6f})".format(*rgb, a)


def lerp(c1, c2, t):
    return tuple(a + (b - a) * t for a, b in zip(c1, c2))


def blend(base, over, alpha):
    return tuple(b + (o - b) * alpha for b, o in zip(base, over))


class Palette:
    """A palette file plus resolved hex -> rgba lookups."""

    def __init__(self, data, path):
        self.data = data
        self.path = path
        self.colors = data["colors"]
        self._cache = {}

    @property
    def id(self):
        return self.data["id"]

    @property
    def name(self):
        return self.data["name"]

    @property
    def type(self):
        return self.data.get("type", "dark")

    def rgba(self, key):
        """Resolve a color name (or literal hex) -> (r,g,b,a) floats."""
        if key in self._cache:
            return self._cache[key]
        raw = self.colors.get(key)
        if raw is None:
            if key.startswith("#"):
                raw = key
            else:
                raise KeyError(f"palette {self.id!r} has no color {key!r}")
        rgba = parse_hex(raw)
        self._cache[key] = rgba
        return rgba

    def hex(self, key, with_alpha=False):
        r, g, b, a = self.rgba(key)
        return to_hex((r, g, b), 1.0 if not with_alpha else a)


# ----------------------------------------------------------------------------
# PNG writer (pure stdlib: zlib + struct)
# ----------------------------------------------------------------------------

def write_png(path, width, height, pixels):
    """pixels: list of rows, each row a list of (r,g,b) floats 0..1."""
    raw = bytearray()
    for row in pixels:
        raw.append(0)  # filter type 0 (None)
        for r, g, b in row:
            raw += bytes((round(r * 255), round(g * 255), round(b * 255)))

    import zlib

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
# Target generators
# ----------------------------------------------------------------------------

def component_color(rgba):
    return {
        "Red Component": rgba[0],
        "Green Component": rgba[1],
        "Blue Component": rgba[2],
        "Alpha Component": rgba[3],
    }


def gen_iterm2(palette, mapping, out_dir):
    out = {}
    for key, ref in mapping["keys"].items():
        out[key] = component_color(palette.rgba(ref))
    path = out_dir / f"{palette.id}.itermcolors"
    with open(path, "wb") as fh:
        plistlib.dump(out, fh, fmt=plistlib.FMT_XML)
    return path


def gen_terminal(palette, mapping, out_dir):
    window = {}
    for key, ref in mapping["keys"].items():
        window[key] = component_color(palette.rgba(ref))
    profile = {
        "name": palette.name,
        "Window Settings": window,
        "type": "Window Background",
    }
    path = out_dir / f"{palette.id}.terminal"
    with open(path, "wb") as fh:
        plistlib.dump(profile, fh, fmt=plistlib.FMT_XML)
    return path


def gen_bbedit(palette, mapping, out_dir):
    scheme = {}
    for key, ref in mapping["keys"].items():
        if isinstance(ref, bool):
            scheme[key] = ref
        else:
            scheme[key] = bbedit_color(palette.rgba(ref))
    for key, ref in mapping.get("bblm", {}).items():
        scheme[key] = bbedit_color(palette.rgba(ref))
    path = out_dir / f"{palette.id}.bbColorScheme"
    with open(path, "wb") as fh:
        plistlib.dump(scheme, fh, fmt=plistlib.FMT_XML)
    return path


def gen_vscode(palette, mapping, out_dir):
    theme = {
        "name": palette.name,
        "type": palette.type,
        "colors": {},
        "tokenColors": [],
    }
    for key, ref in mapping["ui"].items():
        theme["colors"][key] = palette.hex(ref, with_alpha=str(ref).startswith("#") and len(str(ref)) > 7)
    for spec in mapping.get("tokens", []):
        settings = {}
        if "foreground" in spec:
            settings["foreground"] = palette.hex(spec["foreground"])
        if "background" in spec:
            settings["background"] = palette.hex(spec["background"])
        style_bits = []
        if spec.get("fontStyle"):
            settings["fontStyle"] = spec["fontStyle"]
        if spec.get("bold"):
            style_bits.append("bold")
        if spec.get("italic"):
            style_bits.append("italic")
        if style_bits and "fontStyle" not in settings:
            settings["fontStyle"] = " ".join(style_bits)
        entry = {"scope": spec["scope"], "settings": settings}
        theme["tokenColors"].append(entry)
    path = out_dir / f"{palette.id}-color-theme.json"
    path.write_text(json.dumps(theme, indent=2) + "\n")
    return path


def gen_firefox(palette, mapping, cfg, out_dir):
    theme_dir = out_dir / palette.id
    theme_dir.mkdir(parents=True, exist_ok=True)

    colors = {}
    for key, ref in mapping["colors"].items():
        r, g, b, a = palette.rgba(ref)
        colors[key] = {"rgb": [round(r * 255), round(g * 255), round(b * 255)]}
    manifest = {
        "manifest_version": 2,
        "name": palette.name,
        "version": cfg.get("version", "0.1.0"),
        "description": palette.data.get("description", palette.name),
        "theme": {
            "images": {"headerURL": "header.png"},
            "colors": colors,
        },
    }
    (theme_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    _gen_firefox_graphics(palette, cfg, theme_dir)
    return theme_dir


def _gen_firefox_graphics(palette, cfg, theme_dir):
    fx = cfg.get("firefox", {})
    W = fx.get("header_width", 2560)
    H = fx.get("header_height", 200)
    top = palette.rgba("background")[:3]
    bottom = palette.rgba("background_alt")[:3]

    orbs = []
    for orb in fx.get("orbs", []):
        r, g, b = palette.rgba(orb["color"])[:3]
        orbs.append({
            "rgb": (r, g, b),
            "radius": orb.get("radius", 200),
            "cx": orb.get("x", 0.5) * W,
            "cy": orb.get("y", 0.5) * H,
            "alpha": orb.get("alpha", 0.1),
        })

    rows = []
    for y in range(H):
        t = y / max(H - 1, 1)
        base = lerp(top, bottom, t)
        row = []
        for x in range(W):
            px = base
            for orb in orbs:
                dx, dy = x - orb["cx"], y - orb["cy"]
                dist2 = dx * dx + dy * dy
                rad = orb["radius"]
                if dist2 < rad * rad:
                    falloff = 1.0 - (dist2 / (rad * rad)) ** 0.5
                    px = blend(px, orb["rgb"], orb["alpha"] * falloff)
            row.append(px)
        rows.append(row)
    write_png(theme_dir / "header.png", W, H, rows)

    # Theme icon: palette background with an accent disc.
    S = fx.get("icon_size", 128)
    bg = palette.rgba("background_alt")[:3]
    disc = palette.rgba("accent")[:3]
    rows = []
    for y in range(S):
        row = []
        for x in range(S):
            dx, dy = x - S / 2, y - S / 2
            row.append(disc if dx * dx + dy * dy <= (S * 0.36) ** 2 else bg)
        rows.append(row)
    write_png(theme_dir / "icon.png", S, S, rows)


def gen_macos(palette, out_dir):
    lines = [
        f"# {palette.name} — macOS accents",
        "",
        "macOS does not support custom system-wide accent colors, but you can",
        "get close with the built-in presets:",
        "",
        f"- **System Settings → Appearance**: choose {'Dark' if palette.type == 'dark' else 'Light'} mode.",
        f"- **Accent color preset**: pick the preset closest to `{palette.hex('accent')}`",
        "  (Amber/Orange, Purple/Pink, or Graphite are usually the best matches).",
        "- **Highlight color**: set to a preset nearest "
        f"`{palette.hex('selection_bg')}`, or use 'Other' with a color picker value below.",
        "",
        "## Palette values for the Color Picker",
        "",
    ]
    for role in ("accent", "accent_2", "accent_3", "background",
                 "selection_bg", "error", "warning", "added"):
        lines.append(f"- {role}: `{palette.hex(role)}`")
    lines += [
        "",
        "## Terminal.app",
        "",
        "Import the matching `.terminal` profile from the `terminal/` folder",
        "(double-click it, or Shell → Settings → Profiles → Import).",
    ]
    path = out_dir / f"{palette.id}.md"
    path.write_text("\n".join(lines) + "\n")
    return path


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def load_yaml(path):
    with open(path) as fh:
        return yaml.safe_load(fh)


def main():
    ap = argparse.ArgumentParser(description="Generate themeiversal themes")
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--out", type=Path, default=None, help="output dir (default: <repo>/<output_dir>)")
    args = ap.parse_args()

    repo = args.repo
    cfg = load_yaml(repo / "themeiversal.yml")
    out = args.out or repo / cfg.get("output_dir", "dist")
    out = Path(out)
    maps_dir = repo / "maps"

    mappings = {p.stem: load_yaml(p) for p in sorted(maps_dir.glob("*.yml"))}
    palettes = [Palette(load_yaml(p), p) for p in sorted((repo / "palettes").glob("*.yml"))]

    for target_dir in ("iterm2", "terminal", "bbedit", "vscode", "firefox", "macos"):
        (out / target_dir).mkdir(parents=True, exist_ok=True)

    print(f"themeiversal v{cfg.get('version', '?')} → {out}")
    generated = []
    for pal in palettes:
        print(f"  palette: {pal.name}")
        generated.append(gen_iterm2(pal, mappings["iterm2"], out / "iterm2"))
        generated.append(gen_terminal(pal, mappings["terminal"], out / "terminal"))
        generated.append(gen_bbedit(pal, mappings["bbedit"], out / "bbedit"))
        generated.append(gen_vscode(pal, mappings["vscode"], out / "vscode"))
        generated.append(gen_firefox(pal, mappings["firefox"], cfg, out / "firefox"))
        generated.append(gen_macos(pal, out / "macos"))

    # Zip a bundle of everything generated.
    bundle_path = out / f"themeiversal-{cfg.get('version', '0.0.0')}-bundle.zip"
    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in generated:
            if path.is_dir():
                for p in sorted(path.rglob("*")):
                    if p.is_file():
                        zf.write(p, p.relative_to(out))
            else:
                zf.write(path, path.relative_to(out))
    print(f"  bundle: {bundle_path}")
    print("done.")


if __name__ == "__main__":
    main()