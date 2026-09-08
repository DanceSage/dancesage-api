// The 3D body viewer: the fitted meshes of a post, lit and shaded, turned by hand.
// One function: mountBody3D(container, files) where files = {mesh, meta, joints}
// (URLs, fetched with the page's own credentials). Needs three.js on the page.
window.mountBody3D = async function (root, files, opts = {}) {
  const COLOURS = [0x30E8DC, 0xEC48C8];
  root.innerHTML = `
    <canvas class="b3-stage"></canvas>
    <div class="b3-status">Loading the bodies…</div>
    <div class="b3-views"><button data-yaw="0">Front</button><button data-yaw="90">Side</button><button data-yaw="180">Back</button><button data-yaw="270">Other side</button></div>
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
  let yaw = 0, pitch = 0.1, zoom = 1, meshes = [], c = null;

  function resize() {
    const w = root.clientWidth, h = root.clientHeight;
    renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
  }
  new ResizeObserver(resize).observe(root); resize();

  const meta = await (await fetch(files.meta, { credentials: 'same-origin' })).json();
  const bytes = new Uint8Array(await (await fetch(files.mesh, { credentials: 'same-origin' })).arrayBuffer());
  const nf = meta.faces * 3;
  c = { m: meta, faces: new Uint32Array(bytes.buffer, bytes.byteOffset, nf),
        verts: new Uint16Array(bytes.buffer, bytes.byteOffset + nf * 4, meta.people * meta.frames * meta.verts * 3) };
  for (let p = 0; p < meta.people; p++) {
    const g = new THREE.BufferGeometry();
    g.setIndex(new THREE.BufferAttribute(c.faces, 1));
    g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(meta.verts * 3), 3));
    const mesh = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color: COLOURS[p % 2], roughness: 0.65, metalness: 0.05 }));
    rig.add(mesh); meshes.push(mesh);
  }
  const lo = meta.lo, hi = meta.hi; c.centre = [(lo[0]+hi[0])/2, (lo[1]+hi[1])/2, (lo[2]+hi[2])/2];
  c.size = Math.max(hi[1]-lo[1], (hi[0]-lo[0]) * root.clientHeight / Math.max(1, root.clientWidth));
  const grid = new THREE.GridHelper(4, 16, 0x2A2F37, 0x1E232A); grid.position.y = (lo[1]-c.centre[1]) - 0.02; rig.add(grid);
  $('.b3-scrub').max = meta.frames - 1; $('.b3-status').hidden = true;

  function setFrame() {
    const m = c.m, n = m.verts * 3, [cx, cy, cz] = c.centre;
    for (let p = 0; p < m.people; p++) {
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
  $$('.b3-views button').forEach(b => b.onclick = () => { yaw = b.dataset.yaw * Math.PI / 180; });
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
