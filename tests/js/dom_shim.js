// A tiny DOM so the page scripts run: one video menu, one thumbnail canvas.
// A 2D context that accepts anything and does nothing.
const ctx = new Proxy({}, { get: (t, k) => (k in t ? t[k] : () => ctx), set: (t, k, v) => (t[k] = v, true) });
const el = (extra = {}) => {
  const e = { onclick: null, oninput: null, innerHTML: '', value: '', hidden: false, style: {}, className: '',
    dataset: { video: '1', title: 'T', pose: '/pose/k.json' },
    classList: { add(){}, remove(){}, toggle(){}, contains(){ return false; } },
    addEventListener(){}, appendChild(){}, remove(){}, focus(){}, closest(){ return el(); },
    querySelector(){ return el(); }, querySelectorAll(){ return []; },
    getContext(){ return ctx; }, clientWidth: 360, clientHeight: 640, width: 0, height: 0,
    play(){ return Promise.resolve(); }, pause(){}, paused: true, currentTime: 0, ...extra };
  return e;
};
const menu = el();
const dialog = () => Object.assign(el(), { open: false, showModal(){ this.open = true; }, close(){ this.open = false; } });
global.document = {
  createElement(tag){ return tag === 'dialog' ? dialog() : el(); },
  body: el(),
  querySelectorAll(sel){ return sel === '.vmenu' ? [menu] : sel === 'canvas[data-pose]' ? [el()] : []; },
  querySelector(){ return el(); }, getElementById(){ return el(); }, addEventListener(){},
};
global.window = global; global.location = { reload(){}, href: '' };
// One frame, then stop — a real loop would never let the test finish.
let frames = 0; global.requestAnimationFrame = f => { if (frames++ < 2) setTimeout(() => f(performance.now()), 0); };
global.devicePixelRatio = 1;
global.startReplay = global.startReplay || (() => {});
global.fetch = async () => ({ ok: true, json: async () => ({ grants: [], groups: [], series: [], series_grants: [] }) });
// Enough of the renderer for the video page: a canvas, a clock, a loaded track.
global.Skeleton = class {
  constructor(){ this.c = el(); this.hidden = new Set(); this.data = { frames: 1, fps: 15, j: [[]] }; this.f = 0; this.playing = false; this.yaw = 0; }
  load(){ return Promise.resolve({ frames: 1, fps: 15 }); }
  play(){} pause(){} draw(){} seek(){} frameAtTime(){ return 0; } timeAtFrame(){ return 0; }
};
global.prompt = () => null; global.confirm = () => false; global.alert = () => {};
for (const f of process.argv.slice(2)) require(f);
setTimeout(() => console.log('scripts ran without throwing'), 50);
