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
  // MHR70 (SAM 3D Body): 0 nose 1-2 eyes 3-4 ears 5-6 shoulders 7-8 elbows 9-10 hips 11-12 knees 13-14 ankles
  // 15-17 left toes/heel 18-20 right, 21-40 right hand (41 = right wrist), 42-61 left hand (62 = left wrist), 69 neck
  const BONES = {
    mhr70: [[13,11],[11,9],[14,12],[12,10],[9,10],[5,9],[6,10],[5,6],[5,7],[6,8],[7,62],[8,41],[0,69],[5,69],[6,69],
            [13,15],[13,16],[13,17],[14,18],[14,19],[14,20],
            [62,45],[45,44],[44,43],[43,42],[62,49],[49,48],[48,47],[47,46],[62,53],[53,52],[52,51],[51,50],[62,57],[57,56],[56,55],[55,54],[62,61],[61,60],[60,59],[59,58],
            [41,24],[24,23],[23,22],[22,21],[41,28],[28,27],[27,26],[26,25],[41,32],[32,31],[31,30],[30,29],[41,36],[36,35],[35,34],[34,33],[41,40],[40,39],[39,38],[38,37]],
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
      const dots = bones.flat().filter((v, i, a) => a.indexOf(v) === i).map(j => {
        const d = new THREE.Mesh(new THREE.SphereGeometry(j >= 21 && j <= 61 && j !== 41 ? 0.008 : 0.022, 10, 8), mat); d.userData.j = j; grp.add(d); return d; });
      const limbs = bones.map(() => { const hand = b => (b >= 21 && b <= 61 && b !== 41); const l = new THREE.Mesh(new THREE.CylinderGeometry(hand(bones[sk.length] ? 0 : 0) ? 0.005 : 0.011, 0.011, 1, 8), mat); grp.add(l); return l; });
      sk.push({ grp, dots, limbs });
    }
  } catch (e) { joints = null; skeleton = false; jointsWhy = e.message; }

  // The mesh brought its own bounds; a skeleton-only track is measured here, in
  // the same flipped frame the drawing uses, so the figure lands on the grid.
  let lo = meta.lo, hi = meta.hi;
  if (!lo || !hi) {
    lo = [Infinity, Infinity, Infinity]; hi = [-Infinity, -Infinity, -Infinity];
    for (const person of (joints ? joints.people : [])) {
      for (const f of person) {
        if (!f) continue;
        for (const j of f) {
          const v = [j[0], -j[1], -j[2]];
          for (let i = 0; i < 3; i++) { if (v[i] < lo[i]) lo[i] = v[i]; if (v[i] > hi[i]) hi[i] = v[i]; }
        }
      }
    }
    if (!isFinite(lo[0])) { lo = [-0.5, -0.9, -0.5]; hi = [0.5, 0.9, 0.5]; }
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
  if (!joints && !meshes.length) { fail('Nothing to draw — ' + (jointsWhy || 'no joints and no mesh')); return; }
  $('.b3-status').hidden = true;

  function setSkeleton() {
    const [cx, cy, cz] = c.centre, up = new THREE.Vector3(0, 1, 0);
    for (let p = 0; p < sk.length; p++) {
      const f = joints.people[p][frame], s = sk[p];
      s.grp.visible = skeleton && !!(f && f.length);
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
    if (playing) { pos = (pos + (now - last) / 1000 * c.m.fps * rate) % c.m.frames; }
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
  return { pause: () => { playing = false; }, play: () => { playing = true; } };
};
