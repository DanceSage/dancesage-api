// The phone's replay, on the web: teacher and student together — overlaid on
// one body, or side by side over their own videos — with Teacher / You /
// Video switches, one clock, one speed. The student's joints are graded
// against the teacher's the same way the app grades them.
//
// Expects the attempt's 2D track: j[0] the teacher as the camera saw them,
// j[1] the student as their camera saw them, both on the teacher's clock;
// t = teacher time per frame, ta = student's own time per frame.
(function () {
  const BONES = [[0,2],[0,5],[2,7],[5,8],[9,10],[11,12],[11,13],[13,15],[12,14],[14,16],
    [15,17],[15,19],[15,21],[17,19],[16,18],[16,20],[16,22],[18,20],
    [11,23],[12,24],[23,24],[23,25],[25,27],[24,26],[26,28],[27,29],[27,31],[29,31],[28,30],[28,32],[30,32]];
  const PAIRS = [[1,4],[2,5],[3,6],[7,8],[9,10],[11,12],[13,14],[15,16],[17,18],[19,20],[21,22],[23,24],[25,26],[27,28],[29,30],[31,32]];
  const ASPECT = 9 / 16;                 // portrait: an x unit is shorter than a y unit
  const TEACHER = ['#33f2eb', '#f04ceb'];   // bones, joints — the app's jewel palette
  const valid = p => p && p[0] >= 0 && p[1] >= 0;

  // ── geometry, as PoseFeedback does it ────────────────────────────────────
  function mirror(pose) {
    const out = pose.map(p => valid(p) ? [1 - p[0], p[1]] : p);
    for (const [l, r] of PAIRS) { const t = out[l]; out[l] = out[r]; out[r] = t; }
    return out;
  }
  const phys = p => [p[0] * ASPECT, p[1]];
  const unphys = p => [p[0] / ASPECT, p[1]];
  const mid = (a, b) => (valid(a) && valid(b)) ? [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2] : null;
  const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
  function align(att, ref) {
    if (!att || !ref || att.length !== 33 || ref.length !== 33) return att;
    const A = att.map(p => valid(p) ? phys(p) : p), R = ref.map(p => valid(p) ? phys(p) : p);
    const rh = mid(R[23], R[24]), rs = mid(R[11], R[12]), ah = mid(A[23], A[24]), as = mid(A[11], A[12]);
    if (!rh || !rs || !ah || !as) return att;
    const rt = dist(rs, rh), at = dist(as, ah);
    if (rt < 0.001 || at < 0.001) return att;
    const k = rt / at;
    return A.map(p => valid(p) ? unphys([(p[0] - ah[0]) * k + rh[0], (p[1] - ah[1]) * k + rh[1]]) : p);
  }
  function errors(ref, aligned) {
    if (!ref || !aligned) return null;
    const R = ref.map(p => valid(p) ? phys(p) : p), A = aligned.map(p => valid(p) ? phys(p) : p);
    const rh = mid(R[23], R[24]), rs = mid(R[11], R[12]);
    if (!rh || !rs) return null;
    const torso = dist(rs, rh); if (torso < 0.001) return null;
    return R.map((r, i) => (valid(r) && valid(A[i])) ? Math.min(1, Math.max(0, (dist(r, A[i]) / torso - 0.12) / 0.45)) : 0);
  }
  const grade = l => `rgb(${Math.round(Math.min(1, l * 2) * 255)},${Math.round(Math.min(1, (1 - l) * 2) * 255)},30)`;

  // ── the track ────────────────────────────────────────────────────────────
  function poseAt(track, dancer, time) {
    const T = track.t && track.t.length === track.frames ? track.t : null, J = track.j[dancer];
    if (!J || !J.length) return null;
    const timeOf = i => T ? T[i] : i / track.fps;
    let lo = 0, hi = J.length - 1;
    if (time > timeOf(0)) { while (lo < hi) { const m = (lo + hi + 1) >> 1; if (timeOf(m) <= time) lo = m; else hi = m - 1; } }
    const a = J[lo], b = J[Math.min(lo + 1, J.length - 1)];
    const span = timeOf(Math.min(lo + 1, J.length - 1)) - timeOf(lo);
    const u = (span > 0 && span <= 0.5 && time > timeOf(lo)) ? Math.min(1, (time - timeOf(lo)) / span) : 0;
    return a.map((p, i) => (valid(p) && valid(b[i])) ? [p[0] + (b[i][0] - p[0]) * u, p[1] + (b[i][1] - p[1]) * u] : p);
  }
  function focus(track, dancer) {
    let x0 = 1, y0 = 1, x1 = 0, y1 = 0, seen = false;
    const J = track.j[dancer] || [];
    for (let i = 0; i < J.length; i += 3) for (const p of J[i]) if (valid(p)) { seen = true; x0 = Math.min(x0, p[0]); x1 = Math.max(x1, p[0]); y0 = Math.min(y0, p[1]); y1 = Math.max(y1, p[1]); }
    if (!seen || x1 <= x0 || y1 <= y0) return null;
    const px = (x1 - x0) * 0.15, py = (y1 - y0) * 0.12;
    return { x: Math.max(0, x0 - px), y: Math.max(0, y0 - py), w: Math.min(1, x1 + px) - Math.max(0, x0 - px), h: Math.min(1, y1 + py) - Math.max(0, y0 - py) };
  }
  const zoomFor = f => (!f || f.w < 0.05 || f.h < 0.05) ? 1 : Math.min(2.6, Math.max(1, Math.min(1 / f.w, 1 / f.h)));

  // ── drawing ──────────────────────────────────────────────────────────────
  function draw(canvas, pose, errs, palette, view) {
    const x = canvas.getContext('2d'), dp = devicePixelRatio || 1;
    const W = canvas.clientWidth, H = canvas.clientHeight;
    if (canvas.width !== W * dp || canvas.height !== H * dp) { canvas.width = W * dp; canvas.height = H * dp; }
    x.setTransform(dp, 0, 0, dp, 0, 0);
    x.clearRect(0, 0, W, H);
    if (!pose) return;
    const { zoom, cx, cy } = view;
    const pt = p => [((p[0] - cx) * zoom + 0.5) * W, ((p[1] - cy) * zoom + 0.5) * H];
    x.lineCap = 'round';
    for (const [a, b] of BONES) {
      if (!valid(pose[a]) || !valid(pose[b])) continue;
      const A = pt(pose[a]), B = pt(pose[b]);
      let col = palette[0];
      if (errs) { const g = x.createLinearGradient(A[0], A[1], B[0], B[1]); g.addColorStop(0, grade(errs[a])); g.addColorStop(1, grade(errs[b])); col = g; }
      x.strokeStyle = col; x.globalAlpha = 0.25; x.lineWidth = 10; x.beginPath(); x.moveTo(A[0], A[1]); x.lineTo(B[0], B[1]); x.stroke();
      x.globalAlpha = 1; x.lineWidth = 3.5; x.beginPath(); x.moveTo(A[0], A[1]); x.lineTo(B[0], B[1]); x.stroke();
    }
    pose.forEach((p, i) => { if (!valid(p)) return; const P = pt(p); x.fillStyle = errs ? grade(errs[i]) : palette[1]; x.beginPath(); x.arc(P[0], P[1], 4, 0, 7); x.fill(); });
  }

  // ── the player ───────────────────────────────────────────────────────────
  window.startReplay = function (opts) {
    const root = document.getElementById(opts.root);
    const track = opts.track;
    if (!track || !track.j || track.j.length < 2 || !track.j[0].length) {
      root.textContent = 'The skeletons for this attempt are missing.';
      return;
    }
    const dur = Math.max(0.1, (track.t && track.t.length) ? track.t[track.t.length - 1] : track.frames / track.fps);
    const ta = (track.ta && track.ta.length === track.frames) ? track.ta : null;
    const state = { t: 0, playing: true, rate: 1, mode: 'overlaid', teacher: true, you: true, video: true };
    const refFocus = focus(track, 0), attFocus = focus(track, 1);

    root.innerHTML = `
      <div class="rp-pills">
        <span class="rp-group"><button data-mode="overlaid" class="on">Overlaid</button><button data-mode="side">Side by side</button></span>
        <span class="rp-group"><button data-layer="teacher" class="on"><i style="background:${TEACHER[0]}"></i>Teacher</button><button data-layer="you" class="on"><i style="background:#4ade80"></i>You</button><button data-layer="video" class="on"><i style="background:#fff"></i>Video</button></span>
      </div>
      <div class="rp-stage rp-overlaid"><div class="rp-panel"><canvas class="rp-c" data-side="both"></canvas></div></div>
      <div class="rp-stage rp-side" hidden>
        <div class="rp-panel">${opts.teacherVideo ? `<video class="rp-v" data-side="teacher" src="${opts.teacherVideo}" playsinline muted preload="auto"></video>` : ''}<canvas class="rp-c" data-side="teacher"></canvas><span class="rp-tag">Teacher</span>${opts.teacherVideo ? '' : '<span class="rp-none">no video</span>'}</div>
        <div class="rp-panel">${opts.youVideo ? `<video class="rp-v" data-side="you" src="${opts.youVideo}" playsinline muted preload="auto"></video>` : ''}<canvas class="rp-c" data-side="you"></canvas><span class="rp-tag">You</span>${opts.youVideo ? '' : '<span class="rp-none">no video</span>'}</div>
      </div>
      <div class="ctl" style="flex-wrap:nowrap">
        <button class="rp-play" title="Pause">&#10074;&#10074;</button>
        <input class="rp-scrub" type="range" min="0" max="${dur}" step="0.01" value="0">
        <span class="fno rp-time">0:00</span>
        <label class="lbl" title="Speed">&#9201; <input class="rp-speed" type="range" min="0.25" max="2" step="0.25" value="1" style="width:84px"></label>
      </div>`;

    const $ = s => root.querySelector(s), $$ = s => [...root.querySelectorAll(s)];
    const vT = $('video[data-side="teacher"]'), vY = $('video[data-side="you"]');
    const clock = t => Math.floor(t / 60) + ':' + String(Math.floor(t % 60)).padStart(2, '0');

    function paint() {
      const ref = poseAt(track, 0, state.t), raw = poseAt(track, 1, state.t);
      const att = raw ? align(state.mirrored ? mirror(raw) : raw, ref) : null;
      const errs = errors(ref, att);
      if (state.mode === 'overlaid') {
        const c = $('canvas[data-side="both"]'), view = { zoom: 1, cx: 0.5, cy: 0.5 };
        const x = c.getContext('2d');
        draw(c, state.teacher ? ref : null, null, TEACHER, view);
        if (state.you && att) {
          // draw on top without clearing: a second pass into the same canvas
          const save = c.getContext; drawOver(c, att, errs, view);
        }
      } else {
        const zoom = Math.min(zoomFor(refFocus), zoomFor(state.video && vY ? attFocus : refFocus));
        const tv = { zoom, cx: refFocus ? refFocus.x + refFocus.w / 2 : 0.5, cy: refFocus ? refFocus.y + refFocus.h / 2 : 0.5 };
        const youFocus = (state.video && vY) ? attFocus : refFocus;
        const yv = { zoom, cx: youFocus ? youFocus.x + youFocus.w / 2 : 0.5, cy: youFocus ? youFocus.y + youFocus.h / 2 : 0.5 };
        draw($('canvas[data-side="teacher"]'), state.teacher ? ref : null, null, TEACHER, tv);
        // over their own video the student stays in their own frame; without it, on the teacher's body
        const youPose = (state.video && vY) ? raw : att;
        let youErrs = errs;
        if (youErrs && state.video && vY && state.mirrored) { youErrs = youErrs.slice(); for (const [l, r] of PAIRS) { const t = youErrs[l]; youErrs[l] = youErrs[r]; youErrs[r] = t; } }
        draw($('canvas[data-side="you"]'), state.you ? youPose : null, youErrs, ['#4ade80', '#4ade80'], yv);
        for (const [v, view] of [[vT, tv], [vY, yv]]) if (v) {
          v.style.display = state.video ? '' : 'none';
          v.style.transform = `scale(${view.zoom}) translate(${(0.5 - view.cx) * 100}%, ${(0.5 - view.cy) * 100}%)`;
        }
      }
      $('.rp-scrub').value = state.t; $('.rp-time').textContent = clock(state.t);
    }
    function drawOver(canvas, pose, errs, view) {
      // same as draw(), but keeps what is already there
      const x = canvas.getContext('2d'), dp = devicePixelRatio || 1, W = canvas.clientWidth, H = canvas.clientHeight;
      x.setTransform(dp, 0, 0, dp, 0, 0);
      const pt = p => [((p[0] - view.cx) * view.zoom + 0.5) * W, ((p[1] - view.cy) * view.zoom + 0.5) * H];
      x.lineCap = 'round';
      for (const [a, b] of BONES) {
        if (!valid(pose[a]) || !valid(pose[b])) continue;
        const A = pt(pose[a]), B = pt(pose[b]);
        const g = x.createLinearGradient(A[0], A[1], B[0], B[1]); g.addColorStop(0, grade(errs ? errs[a] : 0)); g.addColorStop(1, grade(errs ? errs[b] : 0));
        x.strokeStyle = g; x.globalAlpha = 0.25; x.lineWidth = 10; x.beginPath(); x.moveTo(A[0], A[1]); x.lineTo(B[0], B[1]); x.stroke();
        x.globalAlpha = 1; x.lineWidth = 3.5; x.beginPath(); x.moveTo(A[0], A[1]); x.lineTo(B[0], B[1]); x.stroke();
      }
      pose.forEach((p, i) => { if (!valid(p)) return; const P = pt(p); x.fillStyle = grade(errs ? errs[i] : 0); x.beginPath(); x.arc(P[0], P[1], 4, 0, 7); x.fill(); });
    }

    // videos follow the one clock: the teacher's at t, the student's at their own time for that frame
    function syncVideo(v, target) {
      if (!v) return;
      if (state.mode !== 'side' || !state.video) { if (!v.paused) v.pause(); return; }
      if (Math.abs((v.currentTime || 0) - target) > (state.playing ? 0.15 : 0.04)) { try { v.currentTime = target; } catch (e) {} }
      v.playbackRate = state.rate;
      if (state.playing && v.paused) v.play().catch(() => {});
      if (!state.playing && !v.paused) v.pause();
    }
    function frameIndex(t) { const T = track.t; if (!T || !T.length) return Math.min(track.frames - 1, Math.floor(t * track.fps)); let lo = 0, hi = T.length - 1; while (lo < hi) { const m = (lo + hi + 1) >> 1; if (T[m] <= t) lo = m; else hi = m - 1; } return lo; }

    let last = performance.now();
    function tick(now) {
      const dt = (now - last) / 1000; last = now;
      if (state.playing) { state.t += dt * state.rate; if (state.t >= dur) state.t = 0; }
      syncVideo(vT, state.t);
      syncVideo(vY, ta ? ta[frameIndex(state.t)] : state.t);
      paint();
      requestAnimationFrame(tick);
    }
    state.mirrored = !!opts.mirrored;
    requestAnimationFrame(tick);

    // controls
    $$('button[data-mode]').forEach(b => b.onclick = () => {
      state.mode = b.dataset.mode; $$('button[data-mode]').forEach(x => x.classList.toggle('on', x === b));
      $('.rp-overlaid').hidden = state.mode !== 'overlaid'; $('.rp-side').hidden = state.mode !== 'side';
    });
    $$('button[data-layer]').forEach(b => b.onclick = () => { state[b.dataset.layer] = !state[b.dataset.layer]; b.classList.toggle('on', state[b.dataset.layer]); });
    $('.rp-play').onclick = e => { state.playing = !state.playing; e.currentTarget.innerHTML = state.playing ? '&#10074;&#10074;' : '&#9654;'; };
    $('.rp-scrub').oninput = e => { state.playing = false; $('.rp-play').innerHTML = '&#9654;'; state.t = +e.target.value; };
    $('.rp-speed').oninput = e => { state.rate = +e.target.value; };
    $('.rp-side').hidden = true;
  };
})();
