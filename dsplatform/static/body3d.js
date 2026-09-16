// The 3D body viewer: the fitted meshes of a post, lit and shaded, turned by hand.
// One function: mountBody3D(container, files) where files = {mesh, meta, joints}
// (URLs, fetched with the page's own credentials). Needs three.js on the page.
window.mountBody3D = async function (root, files, opts = {}) {
  const COLOURS = [0x30E8DC, 0xEC48C8];
  root.innerHTML = `
    <canvas class="b3-stage"></canvas>
    <div class="b3-status">Loading the bodies…</div>
    <div class="b3-views"><button data-yaw="0">Front</button><button data-yaw="90">Side</button><button data-yaw="180">Back</button><button data-yaw="270">Other side</button><button class="b3-mode" title="Skeleton or body">Body</button></div>
    <div class="b3-bar">
      <button class="b3-play" title="Pause">&#10074;&#10074;</button>
      <input class="b3-scrub" type="range" min="0" max="1" step="1" value="0">
      <span class="b3-time">0:00.0 / 0:00.0</span>
      <label title="Speed">&#9201; <input class="b3-speed" type="range" min="0.25" max="2" step="0.25" value="1"></label>
    </div>`;
  const $ = s => root.querySelector(s), $$ = s => [...root.querySelectorAll(s)];
  const cv = $('.b3-stage');
  const renderer = new THREE.WebGLRenderer({ canvas: cv, antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  const scene = new THREE.Scene(); scene.background = new THREE.Color(0x0F1114);
  const camera = new THREE.PerspectiveCamera(40, 1, 0.05, 50);
  const rig = new THREE.Group(); scene.add(rig);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x223344, 0.55));
  const key = new THREE.DirectionalLight(0xffffff, 0.9); key.position.set(1.5, 3, 2.5); scene.add(key);
  const fill = new THREE.DirectionalLight(0xffffff, 0.35); fill.position.set(-2, 1, -1.5); scene.add(fill);
  let pos = 0, frame = -1, playing = true, rate = 1, last = performance.now();
  let yaw = 0, pitch = 0.1, zoom = 1, meshes = [], c = null, jointsWhy = '';
  // The skeleton: the joints file alone, lines and dots, no surface. What the score uses.
  let skeleton = true, joints = null, bones = null, sk = [];
  // Which dancers are lit. A couple is the reason the 3D lane exists, and the
  // first thing anybody asks of a couple fit is to see one of them without the
  // other in the way. Empty until the fit says how many there are.
  let on = [];
  // MHR70 (SAM 3D Body): 0 nose 1-2 eyes 3-4 ears 5-6 shoulders 7-8 elbows 9-10 hips 11-12 knees 13-14 ankles
  // 15-17 left toes/heel 18-20 right, 21-40 right hand (41 = right wrist), 42-61 left hand (62 = left wrist), 69 neck
  const BONES = {
    // MHR70 drawn as a dancer: spine and limbs heavy, one line per finger, feet as
    // blades. The R&D viewer's list, because it is the one that reads as a body.
    // Two differences, both of which were making this look worse: the toes were
    // three loose spikes off the ankle and are now closed into a triangle, so a
    // foot is a foot; and each finger was drawn as its full four-segment chain,
    // twenty segments a hand, which at any visible thickness is a club rather
    // than a hand. One line per finger says the same thing and says it cleanly.
    // 9/10 hips, 11/12 knees, 13/14 ankles, 5/6 shoulders, 7/8 elbows, 62/41 wrists, 69 neck, 0 nose.
    mhr70: [[9,10],[9,5],[10,6],[5,6],[5,69],[6,69],[69,0],[13,11],[11,9],[14,12],[12,10],[5,7],[7,62],[6,8],[8,41],
            [13,15],[13,16],[13,17],[15,16],[17,15],[17,16],
            [14,18],[14,19],[14,20],[18,19],[20,18],[20,19],
            [62,45],[45,43],[62,49],[49,47],[62,53],[53,51],[62,57],[57,55],[62,61],[61,59],
            [41,24],[24,22],[41,28],[28,26],[41,32],[32,30],[41,36],[36,34],[41,40],[40,38]],
    smplx: [[0,1],[0,2],[1,4],[2,5],[4,7],[5,8],[7,10],[8,11],[0,3],[3,6],[6,9],[9,12],[12,15],[9,13],[9,14],[13,16],[14,17],[16,18],[17,19],[18,20],[19,21]]
  };


  function resize() {
    const w = root.clientWidth, h = root.clientHeight;
    renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
  }
  new ResizeObserver(resize).observe(root); resize();

  // Anything that goes wrong from here shows on the page. "Loading the bodies…"
  // sitting there for ever, with the reason only in a console nobody had open,
  // is how an evening goes.
  const fail = (why) => { const el = $('.b3-status'); if (el) { el.hidden = false; el.textContent = why; } };
  const grab = async (url, what) => {
    let r;
    try { r = await fetch(url, { credentials: 'same-origin' }); }
    catch (e) { throw new Error(what + ': could not be fetched (' + e.message + ')'); }
    if (!r.ok) throw new Error(what + ': ' + r.status + ' ' + r.statusText);
    try { return await r.json(); }
    catch (e) { throw new Error(what + ': came back as something other than JSON'); }
  };

  const meta = await grab(files.meta, 'meta.json');
  // The surface is optional now. Since the mesh was cut a track is joints alone,
  // and its meta carries no faces, verts or bounds — so everything the viewer
  // needs about size and centre comes from the joints themselves below.
  c = { m: meta };
  if (files.mesh && meta.verts) {
    const bytes = new Uint8Array(await (await fetch(files.mesh, { credentials: 'same-origin' })).arrayBuffer());
    const nf = meta.faces * 3;
    c.faces = new Uint32Array(bytes.buffer, bytes.byteOffset, nf);
    c.verts = new Uint16Array(bytes.buffer, bytes.byteOffset + nf * 4, meta.people * meta.frames * meta.verts * 3);
    for (let p = 0; p < meta.people; p++) {
      const g = new THREE.BufferGeometry();
      g.setIndex(new THREE.BufferAttribute(c.faces, 1));
      g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(meta.verts * 3), 3));
      const mesh = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color: COLOURS[p % 2], roughness: 0.65, metalness: 0.05 }));
      rig.add(mesh); meshes.push(mesh);
    }
  }
  try {
    joints = await grab(files.joints, 'joints.json');
    const first = joints.people.flat().find(f => f && f.length);
    bones = first && first.length >= 70 ? BONES.mhr70 : BONES.smplx;     // MHR70 (SAM 3D Body) or SMPL-X
    for (let p = 0; p < joints.people.length; p++) {
      const grp = new THREE.Group(); rig.add(grp);
      const mat = new THREE.MeshStandardMaterial({ color: COLOURS[p % 2], roughness: 0.5 });
      // Sized like a body, which is the R&D viewer's sizing and the reason its
      // skeleton reads as a person: the head is a head. Here every joint but a
      // finger was one 22 mm bead, so joint 0 — the head — came out the size of a
      // knee and the figure looked pinned rather than alive.
      const finger = j => (j >= 21 && j <= 61 && j !== 41);
      const foot = j => (j >= 15 && j <= 20);
      const size = j => finger(j) ? 0.007 : foot(j) ? 0.014 : (j === 0 ? 0.088 : j === 69 ? 0.032 : 0.024);
      const dots = bones.flat().filter((v, i, a) => a.indexOf(v) === i).map(j => {
        const d = new THREE.Mesh(new THREE.SphereGeometry(size(j), 12, 10), mat); d.userData.j = j; grp.add(d); return d; });
      // Per bone, from the joints it actually joins. What was here asked
      // hand(bones[sk.length] ? 0 : 0), which is hand(0) whatever the bone is —
      // always false — so every finger was drawn as thick as a thigh, and the two
      // ends of each cylinder disagreed about their radius.
      const limbs = bones.map(b => {
        const r = (finger(b[0]) || finger(b[1])) ? 0.006
                : (foot(b[0]) || foot(b[1])) ? 0.011
                : (b.includes(69) || b.includes(0)) ? 0.026 : 0.016;
        const l = new THREE.Mesh(new THREE.CylinderGeometry(r, r, 1, 10), mat); grp.add(l); return l; });
      sk.push({ grp, dots, limbs });
    }
    // Only for a couple. One dancer needs no chooser, and an empty one would sit
    // on every styling clip in the app.
    // Two dancers, two toggles, both lit. There is no "Both" button because both
    // is the resting state, and a chooser whose first option is "leave it alone"
    // is a button that exists to be ignored.
    //
    // Skipped when the page already has a dancer chooser of its own. The video
    // page has one for the 2D skeleton, and drawing a second pair inside the
    // canvas gave a reader two identical controls a few centimetres apart, only
    // one of which reached the thing they were looking at. One control, whatever
    // layer is on top: the page keeps its own and drives this through setHidden.
    on = sk.map(() => true);
    if (sk.length > 1 && opts.chips !== false) {
      const views = $('.b3-views'), mode = $('.b3-mode');
      sk.forEach((_, p) => {
        const b = document.createElement('button');
        b.textContent = 'Dancer ' + (p + 1);
        b.style.color = ['#30E8DC', '#EC48C8'][p % 2];
        b.style.borderColor = 'currentColor';
        b.onclick = () => {
          // Never all off: an empty stage reads as a broken viewer, not a choice,
          // so the last one lit refuses to go out.
          if (on[p] && on.filter(Boolean).length === 1) return;
          on[p] = !on[p];
          b.style.opacity = on[p] ? '1' : '.35';
          b.style.borderColor = on[p] ? 'currentColor' : 'transparent';
          if (joints) setSkeleton();
        };
        views.insertBefore(b, mode);
      });
    }
  } catch (e) { joints = null; skeleton = false; jointsWhy = e.message; }

  // The mesh brought its own bounds; a skeleton-only track is measured here, in
  // the same flipped frame the drawing uses, so the figure lands on the grid.
  let lo = meta.lo, hi = meta.hi;
  if (!lo || !hi) {
    // Percentiles, not min and max. A fit that loses the dancer for a moment puts
    // a joint metres away, and framing to the extremes then pulls the camera back
    // until the person is a speck — which is exactly when somebody most wants to
    // look at the body and work out what went wrong.
    const axes = [[], [], []];
    for (const person of (joints ? joints.people : [])) {
      for (const f of person) {
        if (!f) continue;
        for (const j of f) {
          const v = [j[0], -j[1], -j[2]];
          for (let i = 0; i < 3; i++) if (Number.isFinite(v[i])) axes[i].push(v[i]);
        }
      }
    }
    const at = (arr, q) => arr[Math.min(arr.length - 1, Math.max(0, Math.round(q * (arr.length - 1))))];
    if (axes[0].length) {
      axes.forEach(a => a.sort((x, y) => x - y));
      lo = axes.map(a => at(a, 0.02));
      hi = axes.map(a => at(a, 0.98));
      for (let i = 0; i < 3; i++) if (hi[i] - lo[i] < 0.2) { const m = (hi[i] + lo[i]) / 2; lo[i] = m - 0.1; hi[i] = m + 0.1; }
    } else { lo = [-0.5, -0.9, -0.5]; hi = [0.5, 0.9, 0.5]; }
    // These bounds were measured after the same y/z flip the drawing applies, so
    // they are already in the drawn frame and the centre is taken from them
    // exactly as the mesh path takes it from meta.
    c.centre = [(lo[0]+hi[0])/2, (lo[1]+hi[1])/2, (lo[2]+hi[2])/2];
    c.size = Math.max(hi[1]-lo[1], (hi[0]-lo[0]) * root.clientHeight / Math.max(1, root.clientWidth));
    c.m = Object.assign({ people: joints ? joints.people.length : 1 }, meta, { lo, hi });
  } else {
    c.centre = [(lo[0]+hi[0])/2, (lo[1]+hi[1])/2, (lo[2]+hi[2])/2];
    c.size = Math.max(hi[1]-lo[1], (hi[0]-lo[0]) * root.clientHeight / Math.max(1, root.clientWidth));
  }
  const grid = new THREE.GridHelper(4, 16, 0x2A2F37, 0x1E232A); grid.position.y = (lo[1]-c.centre[1]) - 0.02; rig.add(grid);
  $('.b3-scrub').max = meta.frames - 1;
  // Embedded in the video page, the clip is the clock. Two players in one box —
  // one of them looping on its own animation loop while the video stopped — was
  // never going to look like anything but a bug.
  // style.display, not .hidden: the video page styles "#body3d .b3-bar" with
  // display:flex, and an author rule beats the browser's own [hidden] rule — so
  // the bar stayed on screen and the page carried two sets of transport controls.
  if (opts.clock) { const b = $('.b3-bar'); b.hidden = true; b.style.display = 'none'; }
  if (!joints && !meshes.length) { fail('Nothing to draw — ' + (jointsWhy || 'no joints and no mesh')); return; }
  $('.b3-status').hidden = true;

  function setSkeleton() {
    const [cx, cy, cz] = c.centre, up = new THREE.Vector3(0, 1, 0);
    for (let p = 0; p < sk.length; p++) {
      const f = joints.people[p][frame], s = sk[p];
      s.grp.visible = skeleton && !!(f && f.length) && (on[p] !== false);
      if (!s.grp.visible) continue;
      // joints are in the camera frame (y down, z away); the meshes were flipped to y up, so flip the same way
      const P = j => new THREE.Vector3(f[j][0] - cx, -f[j][1] - cy, -f[j][2] - cz);
      s.dots.forEach(d => d.position.copy(P(d.userData.j)));
      s.limbs.forEach((l, i) => {
        const a = P(bones[i][0]), b = P(bones[i][1]), d = b.clone().sub(a), len = d.length();
        l.position.copy(a).add(d.multiplyScalar(0.5)); l.scale.set(1, Math.max(len, 1e-3), 1);
        l.quaternion.setFromUnitVectors(up, d.normalize());
      });
    }
    meshes.forEach(m => m.visible = !skeleton);
  }

  function setFrame() {
    const m = c.m, n = m.verts * 3, [cx, cy, cz] = c.centre;
    if (joints) setSkeleton();
    for (let p = 0; p < meshes.length; p++) {
      if (skeleton) continue;
      const dst = meshes[p].geometry.attributes.position.array, off = (p * m.frames + frame) * n;
      for (let i = 0; i < n; i += 3) {
        dst[i]   = m.lo[0] + c.verts[off+i]   / 65535 * (m.hi[0]-m.lo[0]) - cx;
        dst[i+1] = m.lo[1] + c.verts[off+i+1] / 65535 * (m.hi[1]-m.lo[1]) - cy;
        dst[i+2] = m.lo[2] + c.verts[off+i+2] / 65535 * (m.hi[2]-m.lo[2]) - cz;
      }
      meshes[p].geometry.attributes.position.needsUpdate = true;
      meshes[p].geometry.computeVertexNormals();
    }
    const clock = t => Math.floor(t/60) + ':' + (t % 60).toFixed(1).padStart(4, '0');
    $('.b3-time').textContent = clock(frame / m.fps) + ' / ' + clock((m.frames - 1) / m.fps); $('.b3-scrub').value = frame;
  }
  function tick(now) {
    if (!root.isConnected) return;
    // Hidden means hidden: keep the loop alive but draw nothing. A WebGL scene
    // rendering every frame behind a closed panel starves the 2D skeleton canvas
    // on the same page, which is exactly how this viewer made the flat one stutter.
    // Not offsetParent: it is null for any position:fixed element, and the
    // full-screen viewer is exactly that — so this guard decided the only thing
    // on the page was invisible and drew nothing at all. Size is the honest test.
    if (root.hidden || !root.clientWidth || !root.clientHeight) { last = now; requestAnimationFrame(tick); return; }
    if (opts.clock) pos = Math.max(0, Math.min(c.m.frames - 1, opts.clock() * c.m.fps));
    else if (playing) { pos = (pos + (now - last) / 1000 * c.m.fps * rate) % c.m.frames; }
    const f = Math.floor(pos); if (f !== frame) { frame = f; setFrame(); }
    rig.rotation.set(pitch, yaw, 0);
    const d = (c.size * 1.55 / Math.tan(THREE.MathUtils.degToRad(20))) / 2 / zoom;
    camera.position.set(0, 0.1, d); camera.lookAt(0, 0, 0);
    renderer.render(scene, camera);
    last = now; requestAnimationFrame(tick);
  }
  $$('.b3-views button[data-yaw]').forEach(b => b.onclick = () => { yaw = b.dataset.yaw * Math.PI / 180; });
  // Body/Skeleton only means something when both exist.
  const modeBtn = $('.b3-mode'); modeBtn.hidden = !joints || !meshes.length;
  const showMode = () => { modeBtn.textContent = skeleton ? 'Body' : 'Skeleton'; };
  modeBtn.onclick = () => { skeleton = !skeleton; showMode(); frame = -1; };
  if (!joints) skeleton = false;
  if (!meshes.length) skeleton = true;      // joints are all there is
  showMode();
  $('.b3-play').onclick = e => { playing = !playing; e.currentTarget.innerHTML = playing ? '&#10074;&#10074;' : '&#9654;'; };
  $('.b3-scrub').oninput = e => { playing = false; $('.b3-play').innerHTML = '&#9654;'; pos = +e.target.value; frame = -1; };
  $('.b3-speed').oninput = e => { rate = +e.target.value; };
  let drag = null;
  cv.addEventListener('pointerdown', e => { drag = [e.clientX, e.clientY]; cv.setPointerCapture(e.pointerId); });
  cv.addEventListener('pointermove', e => { if (!drag) return; yaw += (e.clientX - drag[0]) * 0.008; pitch = Math.max(-0.5, Math.min(1.0, pitch + (e.clientY - drag[1]) * 0.005)); drag = [e.clientX, e.clientY]; });
  cv.addEventListener('pointerup', () => { drag = null; });
  cv.addEventListener('wheel', e => { e.preventDefault(); zoom = Math.max(0.4, Math.min(3, zoom * (e.deltaY < 0 ? 1.08 : 0.93))); }, { passive: false });
  cv.addEventListener('dblclick', () => { yaw = 0; pitch = 0.1; zoom = 1; });
  requestAnimationFrame(tick);
  return {
    pause: () => { playing = false; },
    play: () => { playing = true; },
    // Which dancers to draw, driven from outside: a Set of the ones to hide, in
    // the same numbering the page's own chips use. Hiding everybody is ignored —
    // an empty stage reads as a broken viewer rather than a choice.
    setHidden: (hidden) => {
      if (!sk.length) return;
      const next = sk.map((_, p) => !hidden.has(p));
      if (!next.some(Boolean)) return;
      on = next;
      if (joints) setSkeleton();
    },
  };
};
