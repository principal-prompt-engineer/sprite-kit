/* sprite_loader.js - tiny runtime for sheets made by sprite_kit.py. No dependencies.
 *
 *   const sheet = await Sprites.load(atlasObject, imageUrlOrDataUri);   // atlas = contents of atlas.json
 *   const blue  = sheet.recolor(215);                                   // hue in degrees; cached per hue
 *   blue.draw(ctx, 'idle_1', x, y);                                     // x,y = ground anchor point on screen
 *   blue.anim(ctx, 'idle', timeMs, x, y, { scale: 2, flip: false });   // loops by fps
 *
 * Single-file HTML: embed atlas.json as a JS object and the PNG as a data: URI.
 */
const Sprites = (() => {
  function hex2rgb(h) { h = h.replace('#', ''); return [0, 2, 4].map(i => parseInt(h.substr(i, 2), 16)); }
  function rgb2hsv(r, g, b) {
    r /= 255; g /= 255; b /= 255;
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
    let h = 0;
    if (d > 1e-6) {
      if (mx === r) h = ((g - b) / d) % 6; else if (mx === g) h = (b - r) / d + 2; else h = (r - g) / d + 4;
      h = (h * 60 + 360) % 360;
    }
    return [h, mx ? d / mx : 0, mx];
  }
  function hsv2rgb(h, s, v) {
    const c = v * s, x = c * (1 - Math.abs(((h / 60) % 2) - 1)), m = v - c;
    const [r, g, b] = h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x] : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x];
    return [(r + m) * 255, (g + m) * 255, (b + m) * 255];
  }
  class Sheet {
    constructor(atlas, img) { this.atlas = atlas; this.img = img; this._cache = {}; }
    recolor(deg) {
      const t = this.atlas.tint; if (!t) return this;
      if (this._cache[deg]) return this._cache[deg];
      const c = document.createElement('canvas'); c.width = this.img.width; c.height = this.img.height;
      const g = c.getContext('2d'); g.drawImage(this.img, 0, 0);
      const id = g.getImageData(0, 0, c.width, c.height), d = id.data;
      const ref = rgb2hsv(...hex2rgb(t.hue_ref))[0];
      for (let i = 0; i < d.length; i += 4) {
        if (!d[i + 3]) continue;
        const [h, s, v] = rgb2hsv(d[i], d[i + 1], d[i + 2]);
        const dh = Math.abs(((h - ref + 540) % 360) - 180);
        if (dh <= t.tolerance_deg && s >= t.min_sat) { const o = hsv2rgb(deg, s, v); d[i] = o[0]; d[i + 1] = o[1]; d[i + 2] = o[2]; }
      }
      g.putImageData(id, 0, 0);
      const s2 = new Sheet(this.atlas, c); s2._cache = this._cache; s2.base = this;
      return (this._cache[deg] = s2);
    }
    draw(ctx, id, x, y, o = {}) {
      const f = this.atlas.frames[id]; if (!f) return;
      const s = o.scale || 1, a = this.atlas.anchor;
      ctx.save(); ctx.translate(x, y); if (o.flip) ctx.scale(-1, 1);
      ctx.drawImage(this.img, f.x, f.y, f.w, f.h, -a.x * s, -a.y * s, f.w * s, f.h * s);
      ctx.restore();
    }
    anim(ctx, name, tMs, x, y, o = {}) {
      const a = this.atlas.animations[name]; if (!a) return;
      let i = Math.floor(tMs / 1000 * (o.fps || a.fps));
      i = a.loop === false ? Math.min(i, a.frames.length - 1) : i % a.frames.length;
      this.draw(ctx, a.frames[i], x, y, o);
    }
  }
  async function load(atlas, src) {
    const img = new Image(); img.src = src; await img.decode();
    return new Sheet(atlas, img);
  }
  return { load };
})();
