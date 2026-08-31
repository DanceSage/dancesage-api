/* Skeleton player. Draws pose tracks the app produced — no video element anywhere,
   because at this stage the skeleton IS the content. */
const BONES = [[0,2],[0,5],[2,7],[5,8],[9,10],
 [11,12],[11,13],[13,15],[12,14],[14,16],
 [15,17],[15,19],[15,21],[17,19],[16,18],[16,20],[16,22],[18,20],
 [11,23],[12,24],[23,24],[23,25],[25,27],[24,26],[26,28],
 [27,29],[27,31],[29,31],[28,30],[28,32],[30,32]];
const BONES17 = [[0,1],[0,2],[1,3],[2,4],[5,6],[5,7],[7,9],[6,8],[8,10],
 [5,11],[6,12],[11,12],[11,13],[13,15],[12,14],[14,16]];
const DANCER = ['#3ad6b0', '#ff8fa3'];

class Skeleton {
  constructor(canvas, opts = {}) {
    this.c = canvas;
    this.x = canvas.getContext('2d');
    this.yaw = opts.yaw ?? 0.25;
    this.autoplay = opts.autoplay ?? true;
    this.loop = opts.loop ?? true;
    this.f = 0; this.playing = false; this.data = null; this.last = 0;
    this.onframe = opts.onframe || null;
    new ResizeObserver(() => this.fit()).observe(canvas);
  }
  fit() {
    const r = this.c.getBoundingClientRect(), d = devicePixelRatio || 1;
    this.c.width = Math.max(1, r.width * d);
    this.c.height = Math.max(1, r.height * d);
  }
  async load(url) {
    this.data = await (await fetch(url)).json();
    // A 2D track overlays video in pixel space; a 3D track is projected and rotatable.
    this.is2d = this.data.j[0][0][0].length === 2;
    this.bones = this.data.j[0][0].length === 17 ? BONES17 : BONES;
    if (!this.is2d) this.bounds();
    this.fit();
    if (this.autoplay) this.play();
    else this.draw();
    return this.data;
  }
  bounds() {
    let mn = [9,9,9], mx = [-9,-9,-9];
    for (const p of this.data.j) for (const fr of p) for (const j of fr)
      for (let k = 0; k < 3; k++) { if (j[k] < mn[k]) mn[k] = j[k]; if (j[k] > mx[k]) mx[k] = j[k]; }
    this.B = { c: [(mn[0]+mx[0])/2, (mn[1]+mx[1])/2, (mn[2]+mx[2])/2],
               s: Math.max(mx[0]-mn[0], mx[1]-mn[1], mx[2]-mn[2]) };
    // A track with no depth came from a phone camera, where y grows downward.
    // A fitted 3D track is metres with y up. Flipping the first puts the dancer
    // on their head, so which way is up has to be read from the data.
    this.flat = (mx[2] - mn[2]) < 0.01;
  }
  project(q) {
    if (this.is2d) return [q[0] * this.c.width, q[1] * this.c.height];
    const cy = Math.cos(this.yaw), sy = Math.sin(this.yaw);
    const x = q[0]-this.B.c[0], y = q[1]-this.B.c[1], z = q[2]-this.B.c[2];
    const sc = Math.min(this.c.width, this.c.height) / (this.B.s * 1.45);
    // Flat tracks are already screen-oriented: no yaw to apply, and no flip.
    if (this.flat) return [this.c.width/2 + x*sc, this.c.height/2 + y*sc];
    return [this.c.width/2 + (x*cy + z*sy)*sc, this.c.height/2 - y*sc];
  }
  poseAt(J) {
    const n = J.length, i = Math.floor(this.f) % n, k = (i+1) % n, u = this.f - Math.floor(this.f);
    const A = J[i], C = J[k], out = new Array(A.length);
    for (let m = 0; m < A.length; m++) {
      const p = [A[m][0]+(C[m][0]-A[m][0])*u, A[m][1]+(C[m][1]-A[m][1])*u];
      if (!this.is2d) p.push(A[m][2]+(C[m][2]-A[m][2])*u);
      out[m] = p;
    }
    return out;
  }
  draw() {
    if (!this.data) return;
    const x = this.x, dp = devicePixelRatio || 1;
    x.clearRect(0, 0, this.c.width, this.c.height);
    for (let p = 0; p < this.data.j.length; p++) {
      const pts = this.poseAt(this.data.j[p]).map(q => this.project(q));
      const col = DANCER[p % DANCER.length];
      x.lineCap = 'round';
      x.strokeStyle = col; x.globalAlpha = 0.22; x.lineWidth = 9*dp;
      for (const [a,b] of this.bones) { x.beginPath(); x.moveTo(pts[a][0],pts[a][1]); x.lineTo(pts[b][0],pts[b][1]); x.stroke(); }
      x.globalAlpha = 1; x.lineWidth = 3*dp;
      for (const [a,b] of this.bones) { x.beginPath(); x.moveTo(pts[a][0],pts[a][1]); x.lineTo(pts[b][0],pts[b][1]); x.stroke(); }
      x.fillStyle = '#ffe6a3';
      for (const q of pts) { x.beginPath(); x.arc(q[0], q[1], 2.2*dp, 0, 7); x.fill(); }
    }
    if (this.onframe) this.onframe(Math.floor(this.f), this.data.frames);
  }
  tick(ts) {
    if (!this.playing) return;
    if (this.last) this.f = (this.f + (ts - this.last)/1000 * this.data.fps) % this.data.frames;
    this.last = ts;
    this.draw();
    requestAnimationFrame(t => this.tick(t));
  }
  play()  { if (this.playing) return; this.playing = true; this.last = 0; requestAnimationFrame(t => this.tick(t)); }
  pause() { this.playing = false; }
  seek(f) { this.f = f; this.draw(); }
}
window.Skeleton = Skeleton;
