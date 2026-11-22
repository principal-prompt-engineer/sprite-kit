#!/usr/bin/env python3
"""
sprite_kit.py - generic sprite-sheet toolkit.

One project JSON describes a character + its poses. From it this script makes:
  build     prompts for the image AI, a labelled blank guide, and a spec document
  assemble  turns the row images the image AI produced into a master sheet,
            an atlas.json (frame names + animations), single frames and a preview
  check     reports cropped / empty / off-scale frames and leftover background
  recolor   hue-shifts the tint colour (e.g. red -> blue) to preview or ship colour variants

Needs: Python 3.9+, Pillow, numpy.
Usage:  python3 sprite_kit.py <command> -h
"""
import argparse, colorsys, json, math, re, sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ----------------------------------------------------------------- helpers
def hex2rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

def slug(s):
    return re.sub(r'[^a-z0-9]+', '_', s.lower()).strip('_')

def font(size):
    for name in ('DejaVuSans.ttf', 'DejaVuSans-Bold.ttf', 'Arial.ttf'):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()

def warn(msg):
    print('  WARN:', msg, file=sys.stderr)

# ----------------------------------------------------------------- project
def load_project(path):
    p = json.loads(Path(path).read_text(encoding='utf-8'))
    fw, fh = p['frame']['w'], p['frame']['h']
    p.setdefault('cols', 8)
    p.setdefault('margin', 4)
    p.setdefault('anchor', {'x': fw // 2, 'y': fh - 8})
    p.setdefault('background', {'mode': 'chroma', 'name': 'magenta', 'hex': '#FF00FF'})
    p.setdefault('align_x', 'centroid')
    p.setdefault('default_fps', 8)
    p.setdefault('genre', 'a game')
    seen = set()
    for ri, row in enumerate(p['rows']):
        row.setdefault('id', slug(row['title']))
        if len(row['frames']) > p['cols']:
            raise SystemExit(f"row {ri} has {len(row['frames'])} frames but cols = {p['cols']}")
        for f in row['frames']:
            f.setdefault('id', slug(f['name']))
            if f['id'] in seen:
                f['id'] = f"{f['id']}_r{ri}"
            seen.add(f['id'])
            f.setdefault('anchor', 'ground')
    return p

def auto_animations(p):
    """Group consecutive frames 'Name 1, Name 2, ...' inside a row into looping animations."""
    if 'animations' in p:
        return p['animations']
    anims = {}
    for row in p['rows']:
        groups = {}
        for f in row['frames']:
            m = re.match(r'^(.*?)\s*(\d+)$', f['name'])
            if m:
                groups.setdefault(slug(m.group(1)), []).append(f['id'])
        for k, ids in groups.items():
            if len(ids) >= 2 and k not in anims:
                anims[k] = {'frames': ids, 'fps': p['default_fps'], 'loop': True}
    return anims

# ----------------------------------------------------------------- prompts
def character_block(p):
    return p['character'].strip()

def rules_block(p):
    fw, fh, m = p['frame']['w'], p['frame']['h'], p['margin']
    bg = p['background']
    sh = p.get('standing_height')
    L = []
    if bg['mode'] == 'chroma':
        L.append(f"BACKGROUND: one perfectly flat, solid {bg['name']} ({bg['hex']}) fill everywhere behind the character. "
                 f"No gradient, texture, floor, shadow or glow on the background, and do not use {bg['hex']} or similar "
                 f"hues anywhere on the character (a script removes this colour afterwards).")
    else:
        L.append("BACKGROUND: transparent (real alpha channel, no white halo, no floor, no shadow).")
    L.append("CONSISTENCY: every frame shows the same character with identical proportions, outline thickness, colours "
             "and shading, drawn at exactly the same scale.")
    if sh:
        pct = round(100 * sh / fh)
        L.append(f"SCALE: each frame is one cell as tall as the strip. A standing, upright character is about {pct}% of the "
                 f"strip height (about {sh} px when a cell is {fw} x {fh} px).")
    L.append("GROUNDING: grounded frames have the feet/base on one shared baseline near the bottom of the cell, "
             "centred horizontally. Airborne frames keep the body centred in the cell and lift the feet.")
    L.append(f"MARGINS: keep at least {m} px (at {fw} x {fh} px scale) of empty space around the character in every cell; "
             f"nothing cropped, nothing crossing into a neighbouring cell.")
    L.append("LAYOUT: frames are evenly spaced left to right with clear empty background between them; "
             "no overlap, no frame borders, grid lines, guide marks, numbers, labels or text anywhere in the image.")
    return '\n'.join(L)

def row_prompt(p, row):
    n = len(row['frames'])
    fw, fh = p['frame']['w'], p['frame']['h']
    out = [f"Draw ONE ROW of a sprite sheet for {p['genre']}: {n} frames side by side, left to right, in a single "
           f"horizontal strip on a wide landscape canvas (exact pixel size does not matter). Each frame fits an equal "
           f"cell with the proportion {fw}:{fh}; use only the first {n} cells and leave the rest empty.",
           '',
           'CHARACTER: ' + character_block(p),
           '',
           rules_block(p)]
    if row.get('note'):
        out += ['', 'ROW NOTE: ' + row['note']]
    out += ['', 'FRAMES, left to right:']
    for i, f in enumerate(row['frames'], 1):
        out.append(f"{i}. {f['name']}: {f['desc']}")
    return '\n'.join(out)

def reference_prompt(p):
    views = p.get('reference_views', ['front view', 'three-quarter view', 'side view facing right', 'back view'])
    bg = p['background']
    bgline = (f"on a perfectly flat solid {bg['name']} ({bg['hex']}) background" if bg['mode'] == 'chroma'
              else 'on a transparent background')
    return '\n'.join([
        f"Draw a CHARACTER MODEL SHEET for {p['genre']}: the same character shown {len(views)} times in one row "
        f"({', '.join(views)}), standing in a neutral pose, same scale, {bgline}. Below the row add a small strip of "
        f"flat colour swatches of the character's palette and three face/expression close-ups (neutral, happy, sad). "
        f"No text or labels.",
        '', 'CHARACTER: ' + character_block(p), '', rules_block(p)])

REF_NOTE = ("Optional first line when you have an approved reference image attached: "
            "\"Use the attached image as the exact character model; match its design, colours and proportions in every frame.\"")

def cmd_prompts(p, out):
    d = out / 'prompts'
    d.mkdir(parents=True, exist_ok=True)
    allp = [f"# Prompts for {p['title']}\n",
            "Generate `00_reference` first, approve it, then attach it (or the best frame) to every row prompt. "
            "Paste ONE prompt at a time in the SAME chat.\n", REF_NOTE + "\n"]
    pieces = [('00_reference', 'Character model sheet (do first)', reference_prompt(p))]
    for ri, row in enumerate(p['rows']):
        pieces.append((f"row_{ri:02d}_{row['id']}", f"Row {ri}: {row['title']}", row_prompt(p, row)))
    for name, title, text in pieces:
        (d / f'{name}.md').write_text(f"# {title}\n\n{REF_NOTE}\n\n```\n{text}\n```\n", encoding='utf-8')
        allp.append(f"## {title}\n\n```\n{text}\n```\n")
    (out / 'all_prompts.md').write_text('\n'.join(allp), encoding='utf-8')
    print(f"  prompts: {len(pieces)} files in {d} + all_prompts.md")

# ----------------------------------------------------------------- guide + spec
def cmd_guide(p, out):
    fw, fh, cols = p['frame']['w'], p['frame']['h'], p['cols']
    cw = 150
    ch = round(cw * fh / fw)
    lab, ban = 38, 24
    rows = p['rows']
    W = cols * cw
    H = len(rows) * (ban + ch + lab)
    im = Image.new('RGB', (W, H), 'white')
    d = ImageDraw.Draw(im)
    f1, f2 = font(13), font(11)
    ax, ay = p['anchor']['x'] / fw, p['anchor']['y'] / fh
    y = 0
    for ri, row in enumerate(rows):
        d.rectangle([0, y, W, y + ban], fill=(38, 58, 94))
        d.text((6, y + 5), f"ROW {ri}: {row['title']}   {row.get('note', '')}"[:int(W / 7)], fill='white', font=f1)
        y += ban
        for c in range(cols):
            x = c * cw
            if c < len(row['frames']):
                fr = row['frames'][c]
                d.rectangle([x, y, x + cw - 1, y + ch - 1], fill=(237, 242, 251), outline=(110, 150, 210))
                gy = y + int(ay * ch)
                d.line([x + 4, gy, x + cw - 5, gy], fill=(170, 180, 200))
                d.ellipse([x + int(ax * cw) - 3, gy - 3, x + int(ax * cw) + 3, gy + 3], outline=(200, 80, 80))
                d.text((x + 4, y + ch + 2), f"r{ri} c{c}", fill=(120, 120, 120), font=f2)
                d.text((x + 4, y + ch + 16), fr['name'][:22], fill='black', font=f1)
            else:
                d.rectangle([x, y, x + cw - 1, y + ch - 1], fill=(222, 225, 230), outline=(190, 195, 205))
                d.text((x + 4, y + 3), 'unused', fill=(150, 150, 150), font=f2)
        y += ch + lab
    im.save(out / 'guide_blank.png')
    print(f"  guide: {out / 'guide_blank.png'}")

def cmd_spec(p, out):
    fw, fh, cols = p['frame']['w'], p['frame']['h'], p['cols']
    L = [f"# {p['title']} - sprite sheet spec", '',
         f"- Sheet: {cols} columns x {len(p['rows'])} rows of {fw} x {fh} px frames = {cols * fw} x {len(p['rows']) * fh} px, transparent.",
         f"- Ground anchor: x={p['anchor']['x']}, y={p['anchor']['y']}; margin {p['margin']} px"
         + (f"; standing height {p['standing_height']} px." if p.get('standing_height') else '.'),
         f"- Background asked from the image AI: {p['background']['mode']}"
         + (f" {p['background']['hex']}" if p['background']['mode'] == 'chroma' else '') + '.']
    if p.get('tint'):
        t = p['tint']
        L.append(f"- Tint colour {t['hue_ref']} (hue within {t['tolerance_deg']} deg, saturation > {t['min_sat']}) is shifted per player/team at load.")
    L += ['', '## Rows and frames', '', '| Row | Title | Frames (col: id) |', '|---|---|---|']
    for ri, row in enumerate(p['rows']):
        L.append(f"| {ri} | {row['title']} | " + ', '.join(f"{c}: {f['id']}" for c, f in enumerate(row['frames'])) + ' |')
    L += ['', '## Animations', '']
    for k, a in auto_animations(p).items():
        L.append(f"- `{k}`: {', '.join(a['frames'])} ({a['fps']} fps, {'loop' if a.get('loop', True) else 'once'})")
    L += ['', '## Frame descriptions', '']
    for ri, row in enumerate(p['rows']):
        L.append(f"### Row {ri}: {row['title']}")
        for f in row['frames']:
            L.append(f"- **{f['id']}** ({f['anchor']}): {f['desc']}")
        L.append('')
    (out / 'SPEC.md').write_text('\n'.join(L), encoding='utf-8')
    print(f"  spec: {out / 'SPEC.md'}")

def cmd_build(p, out):
    out.mkdir(parents=True, exist_ok=True)
    cmd_prompts(p, out); cmd_guide(p, out); cmd_spec(p, out)

# ----------------------------------------------------------------- matting + segmentation
def matte(img, lo=30, hi=75):
    """Return (rgb float 0-255, alpha float 0-1). Uses real alpha if present, else removes the flat border colour."""
    a = np.asarray(img.convert('RGBA')).astype(np.float32)
    rgb, a0 = a[..., :3], a[..., 3] / 255.0
    if (a0 < 0.98).mean() > 0.02:
        return rgb, a0
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    bg = np.median(border, axis=0)
    dist = np.linalg.norm(rgb - bg, axis=2)
    alpha = np.clip((dist - lo) / (hi - lo), 0, 1)
    af = np.maximum(alpha, 1e-3)[..., None]
    c = np.clip((rgb - (1 - af) * bg) / af, 0, 255)   # un-mix background from soft edges
    c[alpha <= 0.001] = 0
    return c, alpha

def segment(alpha, n, equal=False):
    """Split a strip into n (x0, x1) column ranges. Gap-based if possible, else equal slices."""
    H, W = alpha.shape
    if not equal:
        occ = (alpha > 0.5).sum(axis=0) > 0
        runs, s = [], None
        for x, v in enumerate(occ):
            if v and s is None: s = x
            if not v and s is not None: runs.append([s, x - 1]); s = None
        if s is not None: runs.append([s, W - 1])
        runs = [r for r in runs if r[1] - r[0] >= max(3, W * 0.002)]
        if len(runs) >= n:
            while len(runs) > n:                      # merge across the smallest gap (detached sparkles etc.)
                gaps = [runs[i + 1][0] - runs[i][1] for i in range(len(runs) - 1)]
                i = int(np.argmin(gaps))
                runs[i:i + 2] = [[runs[i][0], runs[i + 1][1]]]
            return [tuple(r) for r in runs], 'gaps'
    # equal slicing over the first n cells of an assumed cell width = image height... or W/n when full strip
    return None, 'equal'

def equal_ranges(alpha, n, cell_w):
    H, W = alpha.shape
    return [(int(i * cell_w), int(min(W, (i + 1) * cell_w) - 1)) for i in range(n)]

# ----------------------------------------------------------------- assemble
def find_row_image(rows_dir, ri, row):
    for name in (f'row_{ri:02d}', f'row_{ri}', f"row_{row['id']}", f'row_{ri:02d}_{row["id"]}'):
        for ext in ('.png', '.webp', '.jpg', '.jpeg'):
            f = rows_dir / (name + ext)
            if f.exists():
                return f
    return None

def extract_row(p, ri, row, rows_dir, equal):
    path = find_row_image(rows_dir, ri, row)
    n = len(row['frames'])
    if path is None:
        warn(f"row {ri} ({row['title']}): no image found (expected row_{ri:02d}.png)")
        return None
    rgb, alpha = matte(Image.open(path))
    H, W = alpha.shape
    ranges, mode = segment(alpha, n, equal)
    if ranges is None:
        # strips often hold n frames in a full-width image, or up to `cols` cells with empties
        cell_w = W / (p['cols'] if (W / H) > n * 1.3 else n)
        ranges = equal_ranges(alpha, n, cell_w)
        if not equal:
            warn(f"row {ri} ({row['title']}): found fewer than {n} separate frames; using equal slices - check the result")
    frames = []
    for (x0, x1) in ranges:
        sub = alpha[:, x0:x1 + 1]
        ys = np.where((sub > 0.1).any(axis=1))[0]
        xs = np.where((sub > 0.1).any(axis=0))[0]
        if len(ys) == 0:
            frames.append(None); continue
        y0, y1, xx0, xx1 = ys[0], ys[-1], x0 + xs[0], x0 + xs[-1]
        rgba = np.dstack([rgb[y0:y1 + 1, xx0:xx1 + 1], alpha[y0:y1 + 1, xx0:xx1 + 1] * 255]).astype(np.uint8)
        frames.append(Image.fromarray(rgba, 'RGBA'))
    return {'H': H, 'frames': frames, 'path': path, 'mode': mode}

def place(p, im, scale, fdef, row_ratio_note=None):
    """Scale a cropped frame and place it on a frame-size canvas using the anchor rules."""
    fw, fh, m = p['frame']['w'], p['frame']['h'], p['margin']
    ax, ay = p['anchor']['x'], p['anchor']['y']
    w, h = max(1, round(im.width * scale)), max(1, round(im.height * scale))
    fit = min(1.0, (fw - 2 * m) / w, (ay - m) / h if fdef['anchor'] == 'ground' else (fh - 2 * m) / h)
    shrunk = fit < 0.999
    if shrunk:
        w, h = max(1, round(w * fit)), max(1, round(h * fit))
    prem = im.convert('RGBa').resize((w, h), Image.LANCZOS).convert('RGBA')
    a = np.asarray(prem)[..., 3].astype(np.float32)
    if p['align_x'] == 'centroid' and a.sum() > 0:
        cx = float((a.sum(axis=0) * np.arange(w)).sum() / a.sum())
    else:
        cx = w / 2
    x = int(round(ax - cx))
    x = min(max(x, m), fw - m - w)
    if fdef['anchor'] == 'air':
        if 'air_center' in p:
            y = int(round(p['air_center']['y'] - h / 2))
        else:
            y = ay - h - fdef.get('lift', 10)
        y = min(max(y, m), fh - m - h)
    else:
        y = ay - h
    canvas = Image.new('RGBA', (fw, fh), (0, 0, 0, 0))
    canvas.alpha_composite(prem, (x, y))
    return canvas, shrunk

def cmd_assemble(p, rows_dir, out, args):
    out.mkdir(parents=True, exist_ok=True)
    (out / 'frames').mkdir(exist_ok=True)
    fw, fh, cols = p['frame']['w'], p['frame']['h'], p['cols']
    sh = p.get('standing_height')
    data = [extract_row(p, ri, row, rows_dir, args.equal) for ri, row in enumerate(p['rows'])]
    # scale: s_cell maps strip height -> frame height; rows with a 'stand' frame are tied to standing_height
    s_cell, s_stand = {}, {}
    for ri, (row, dat) in enumerate(zip(p['rows'], data)):
        if not dat: continue
        s_cell[ri] = fh / dat['H']
        hs = [dat['frames'][i].height for i, f in enumerate(row['frames']) if f.get('stand') and dat['frames'][i] is not None]
        if sh and hs and args.auto_scale:
            s_stand[ri] = sh / float(np.median(hs))
    ratio = float(np.median([s_stand[r] / s_cell[r] for r in s_stand])) if s_stand else 1.0
    overrides = dict(kv.split('=') for kv in (args.row_scale or []))
    sheet = Image.new('RGBA', (cols * fw, len(p['rows']) * fh), (0, 0, 0, 0))
    atlas = {'name': p['name'], 'image': f"{p['name']}_master.png", 'frame': {'w': fw, 'h': fh},
             'anchor': p['anchor'], 'frames': {}, 'animations': auto_animations(p), 'rows': []}
    if p.get('tint'): atlas['tint'] = p['tint']
    for ri, (row, dat) in enumerate(zip(p['rows'], data)):
        atlas['rows'].append({'id': row['id'], 'title': row['title'], 'frames': [f['id'] for f in row['frames']]})
        if not dat: continue
        s = s_stand.get(ri, s_cell[ri] * ratio)
        s *= float(overrides.get(row['id'], overrides.get(str(ri), 1.0)))
        print(f"  row {ri} {row['id']}: {len(row['frames'])} frames via {dat['mode']}, scale {s:.3f}")
        for ci, (fdef, im) in enumerate(zip(row['frames'], dat['frames'])):
            if im is None:
                warn(f"{fdef['id']}: empty cell"); continue
            canvas, shrunk = place(p, im, s, fdef)
            if shrunk: warn(f"{fdef['id']}: too big for the frame, shrunk to fit (check scale of row {ri})")
            sheet.alpha_composite(canvas, (ci * fw, ri * fh))
            canvas.save(out / 'frames' / f"{fdef['id']}.png")
            atlas['frames'][fdef['id']] = {'x': ci * fw, 'y': ri * fh, 'w': fw, 'h': fh, 'row': ri, 'col': ci}
    sheet.save(out / atlas['image'])
    (out / 'atlas.json').write_text(json.dumps(atlas, indent=1), encoding='utf-8')
    # preview: dark checker background with cell borders
    bgc = Image.new('RGBA', sheet.size, (52, 56, 66, 255))
    d = ImageDraw.Draw(bgc)
    for r in range(len(p['rows']) + 1): d.line([0, r * fh, sheet.width, r * fh], fill=(90, 96, 110))
    for c in range(cols + 1): d.line([c * fw, 0, c * fw, sheet.height], fill=(90, 96, 110))
    for r in range(len(p['rows'])):
        d.line([0, r * fh + p['anchor']['y'], sheet.width, r * fh + p['anchor']['y']], fill=(70, 110, 160))
    bgc.alpha_composite(sheet)
    bgc.save(out / 'preview.png')
    print(f"  wrote {out / atlas['image']}, atlas.json, preview.png, frames/")

# ----------------------------------------------------------------- check
def cols_w(p):
    return p['cols'] * p['frame']['w']

def cmd_check(p, out, args):
    sheet_path = Path(args.sheet) if args.sheet else out / f"{p['name']}_master.png"
    sheet = Image.open(sheet_path).convert('RGBA')
    fw, fh, m = p['frame']['w'], p['frame']['h'], p['margin']
    sh = p.get('standing_height')
    bg = p['background']
    key = np.array(hex2rgb(bg['hex']), dtype=np.float32) if bg['mode'] == 'chroma' else None
    issues, stand_h = 0, []
    for ri, row in enumerate(p['rows']):
        rowarr = np.asarray(sheet.crop((0, ri * fh, cols_w(p), (ri + 1) * fh)))
        if (rowarr[..., 3] > 25).sum() < 40:
            print(f"  EMPTY   row {ri} ({row['title']}): not assembled yet"); issues += 1; continue
        for ci, f in enumerate(row['frames']):
            cell = np.asarray(sheet.crop((ci * fw, ri * fh, (ci + 1) * fw, (ri + 1) * fh))).astype(np.float32)
            a = cell[..., 3] / 255
            tag = f"r{ri}c{ci} {f['id']}"
            if (a > 0.5).sum() < 40:
                print(f"  EMPTY   {tag}"); issues += 1; continue
            ys, xs = np.where(a > 0.1)
            if xs.min() < m - 1 or ys.min() < m - 1 or xs.max() > fw - m or ys.max() > fh - m:
                print(f"  EDGE    {tag}: content within {m}px of the frame edge (cropped?)"); issues += 1
            if key is not None:
                near = (np.linalg.norm(cell[..., :3] - key, axis=2) < 60) & (a > 0.5)
                if near.sum() > 12:
                    print(f"  KEYLEFT {tag}: {int(near.sum())} opaque pixels close to the background colour"); issues += 1
            if f.get('stand'):
                stand_h.append((tag, ys.max() - ys.min() + 1))
    if sh and stand_h:
        for tag, h in stand_h:
            if abs(h - sh) > 0.12 * sh:
                print(f"  SCALE   {tag}: standing height {h}px, expected about {sh}px"); issues += 1
    print(f"  check done: {issues} issue(s)")
    return issues

# ----------------------------------------------------------------- recolor
def rgb2hsv(c):
    r, g, b = c[..., 0], c[..., 1], c[..., 2]
    mx, mn = c.max(-1), c.min(-1)
    d = mx - mn
    h = np.zeros_like(mx)
    nz = d > 1e-6
    rc = (mx == r) & nz; gc = (mx == g) & nz & ~rc; bc = nz & ~rc & ~gc
    h[rc] = ((g - b)[rc] / d[rc]) % 6
    h[gc] = (b - r)[gc] / d[gc] + 2
    h[bc] = (r - g)[bc] / d[bc] + 4
    s = np.where(mx > 0, d / np.maximum(mx, 1e-6), 0)
    return h / 6.0, s, mx

def hsv2rgb(h, s, v):
    i = np.floor(h * 6).astype(int) % 6
    f = h * 6 - np.floor(h * 6)
    pp, q, t = v * (1 - s), v * (1 - f * s), v * (1 - (1 - f) * s)
    r = np.choose(i, [v, q, pp, pp, t, v]); g = np.choose(i, [t, v, v, q, pp, pp]); b = np.choose(i, [pp, pp, t, v, v, q])
    return np.stack([r, g, b], -1)

def recolor_image(img, tint, target_deg):
    a = np.asarray(img.convert('RGBA')).astype(np.float32)
    rgb = a[..., :3] / 255
    h, s, v = rgb2hsv(rgb)
    ref = colorsys.rgb_to_hsv(*[x / 255 for x in hex2rgb(tint['hue_ref'])])[0] * 360
    dh = np.abs(((h * 360 - ref) + 180) % 360 - 180)
    mask = (dh <= tint['tolerance_deg']) & (s >= tint['min_sat'])
    h2 = np.where(mask, (target_deg % 360) / 360.0, h)
    out = hsv2rgb(h2, s, v)
    res = np.dstack([out * 255, a[..., 3]]).clip(0, 255).astype(np.uint8)
    return Image.fromarray(res, 'RGBA')

def cmd_recolor(p, out, args):
    tint = p.get('tint')
    if not tint: raise SystemExit('project has no "tint" block')
    sheet_path = Path(args.sheet) if args.sheet else out / f"{p['name']}_master.png"
    sheet = Image.open(sheet_path)
    fw, fh = p['frame']['w'], p['frame']['h']
    pairs = [kv.split('=') for kv in args.hues]
    tiles = []
    for name, deg in pairs:
        rc = recolor_image(sheet, tint, float(deg))
        rc.save(out / f"{p['name']}_master_{name}.png")
        tiles.append((name, rc.crop((0, 0, fw, fh))))
    prev = Image.new('RGBA', (fw * len(tiles), fh), (52, 56, 66, 255))
    for i, (name, t) in enumerate(tiles): prev.alpha_composite(t, (i * fw, 0))
    prev.save(out / 'recolor_preview.png')
    print(f"  wrote {len(tiles)} recoloured sheets + recolor_preview.png (check for leftover red on non-red variants)")

# ----------------------------------------------------------------- cli
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('build', 'assemble', 'check', 'recolor'):
        s = sub.add_parser(name)
        s.add_argument('project')
        s.add_argument('-o', '--out', default=None, help='output folder (default out/<project name>)')
        if name == 'assemble':
            s.add_argument('rows_dir', help='folder with row_00.png, row_01.png ...')
            s.add_argument('--equal', action='store_true', help='force equal-width slicing instead of gap detection')
            s.add_argument('--no-auto-scale', dest='auto_scale', action='store_false', help='do not tie rows to standing_height')
            s.add_argument('--row-scale', action='append', metavar='ROW=FACTOR', help='e.g. run=1.1 or 3=0.9 (repeatable)')
        if name in ('check', 'recolor'):
            s.add_argument('--sheet', default=None)
        if name == 'recolor':
            s.add_argument('--hues', nargs='+', default=['red=356', 'blue=215', 'yellow=48', 'green=125'], metavar='NAME=DEG')
    a = ap.parse_args()
    p = load_project(a.project)
    out = Path(a.out) if a.out else Path('out') / p['name']
    if a.cmd == 'build': cmd_build(p, out)
    elif a.cmd == 'assemble': cmd_assemble(p, Path(a.rows_dir), out, a)
    elif a.cmd == 'check': sys.exit(1 if cmd_check(p, out, a) else 0)
    elif a.cmd == 'recolor': cmd_recolor(p, out, a)

if __name__ == '__main__':
    main()
