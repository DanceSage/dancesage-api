// A tiny DOM so the page scripts run: one video menu, one thumbnail canvas.
const el = (extra = {}) => {
  const e = { onclick: null, oninput: null, innerHTML: '', value: '', hidden: false, style: {}, className: '',
    dataset: { video: '1', title: 'T', pose: '/pose/k.json' },
    classList: { add(){}, remove(){}, toggle(){}, contains(){ return false; } },
    addEventListener(){}, appendChild(){}, remove(){}, focus(){}, closest(){ return el(); },
    querySelector(){ return el(); }, querySelectorAll(){ return []; }, ...extra };
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
global.fetch = async () => ({ ok: true, json: async () => ({ grants: [], groups: [] }) });
// Enough of the renderer for the video page: a canvas, a clock, a loaded track.
global.Skeleton = class {
  constructor(){ this.c = el(); this.hidden = new Set(); this.data = { frames: 1, fps: 15, j: [[]] }; this.f = 0; this.playing = false; this.yaw = 0; }
  load(){ return Promise.resolve({ frames: 1, fps: 15 }); }
  play(){} pause(){} draw(){} seek(){} frameAtTime(){ return 0; } timeAtFrame(){ return 0; }
};
global.prompt = () => null; global.confirm = () => false; global.alert = () => {};
for (const f of process.argv.slice(2)) require(f);
setTimeout(() => console.log('scripts ran without throwing'), 50);
