/* =============================================================
   SPLIX X CHEAT  —  ambient particles  (js/particles.js)
   -------------------------------------------------------------
   Very light, self-tuning canvas background: slow drifting
   dots with occasional soft links between neighbours.
   Respects prefers-reduced-motion and pauses when the tab is
   hidden so it never costs battery or jank.

   Tune the look via the CONFIG block below.
   ============================================================= */

(function () {
  'use strict';

  var canvas = document.getElementById('particles');

  /* No canvas element on this page -> nothing to do */
  if (!canvas || !canvas.getContext) return;

  /* Never animate for users who asked for reduced motion */
  var reduce = window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduce) {
    canvas.style.display = 'none';
    return;
  }

  var ctx = canvas.getContext('2d');
  if (!ctx) return;

  /* ---------- CONFIG ---------- */
  var CONFIG = {
    density: 13000,     /* px^2 of canvas per particle */
    minCount: 26,
    maxCount: 90,
    minRadius: 0.7,
    maxRadius: 2.1,
    speed: 0.22,
    linkDistance: 128,
    linkAlpha: 0.16,
    colors: ['255,45,58', '255,95,77', '158,15,35'] /* red / warm red / deep red */
  };

  var dpr = Math.min(window.devicePixelRatio || 1, 2);
  var W = 0, H = 0;
  var particles = [];
  var raf = null;
  var running = false;

  /* ---------- Helpers ---------- */
  function rand(min, max) { return Math.random() * (max - min) + min; }

  function makeParticle() {
    var color = CONFIG.colors[(Math.random() * CONFIG.colors.length) | 0];
    return {
      x: Math.random() * W,
      y: Math.random() * H,
      r: rand(CONFIG.minRadius, CONFIG.maxRadius),
      vx: rand(-CONFIG.speed, CONFIG.speed),
      vy: rand(-CONFIG.speed, CONFIG.speed),
      a: rand(0.18, 0.62),
      color: color
    };
  }

  function resize() {
    W = window.innerWidth;
    H = window.innerHeight;

    canvas.width = Math.floor(W * dpr);
    canvas.height = Math.floor(H * dpr);
    canvas.style.width = W + 'px';
    canvas.style.height = H + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    /* Re-balance particle count for the new area */
    var target = Math.round(W * H / CONFIG.density);
    target = Math.max(CONFIG.minCount, Math.min(CONFIG.maxCount, target));

    while (particles.length < target) particles.push(makeParticle());
    if (particles.length > target) particles.length = target;
  }

  /* ---------- Draw loop ---------- */
  function draw() {
    ctx.clearRect(0, 0, W, H);

    /* Links first, so the dots sit on top */
    ctx.lineWidth = 1;
    for (var i = 0; i < particles.length; i++) {
      var p = particles[i];
      for (var j = i + 1; j < particles.length; j++) {
        var q = particles[j];
        var dx = p.x - q.x;
        var dy = p.y - q.y;
        var distSq = dx * dx + dy * dy;
        var max = CONFIG.linkDistance;
        if (distSq > max * max) continue;

        var dist = Math.sqrt(distSq);
        var alpha = (1 - dist / max) * CONFIG.linkAlpha;
        ctx.strokeStyle = 'rgba(' + p.color + ',' + alpha.toFixed(3) + ')';
        ctx.beginPath();
        ctx.moveTo(p.x, p.y);
        ctx.lineTo(q.x, q.y);
        ctx.stroke();
      }
    }

    /* Dots */
    for (var k = 0; k < particles.length; k++) {
      var a = particles[k];

      a.x += a.vx;
      a.y += a.vy;

      /* Soft wrap around the edges */
      if (a.x < -12) a.x = W + 12;
      else if (a.x > W + 12) a.x = -12;
      if (a.y < -12) a.y = H + 12;
      else if (a.y > H + 12) a.y = -12;

      ctx.beginPath();
      ctx.arc(a.x, a.y, a.r, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(' + a.color + ',' + a.a.toFixed(3) + ')';
      ctx.fill();
    }
  }

  function loop() {
    draw();
    raf = window.requestAnimationFrame(loop);
  }

  function start() {
    if (running) return;
    running = true;
    raf = window.requestAnimationFrame(loop);
  }

  function stop() {
    running = false;
    if (raf) { window.cancelAnimationFrame(raf); raf = null; }
  }

  /* ---------- Wire up ---------- */
  var resizeTimer = null;
  window.addEventListener('resize', function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(resize, 160);
  });

  /* Only animate while the tab is visible */
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) stop();
    else start();
  });

  resize();
  draw();   /* paint one static frame immediately */
  start();
})();
