/* =============================================================
   SPLIX X CHEAT  —  shared site behaviour  (js/main.js)
   -------------------------------------------------------------
   Used by BOTH index.html and plus.html.
   Everything here reads its content from js/config.js, so you
   never need to edit this file to change text or links.
   ============================================================= */

(function () {
  'use strict';

  /* Guard: config.js must load first */
  var CFG = window.SPLIX;
  if (!CFG) {
    console.error('[SPLIX] js/config.js is missing or failed to load.');
    return;
  }

  /* ---------------------------------------------------------
     Tiny DOM helpers
     --------------------------------------------------------- */
  function $(sel, ctx) { return (ctx || document).querySelector(sel); }
  function $$(sel, ctx) { return Array.prototype.slice.call((ctx || document).querySelectorAll(sel)); }
  function el(tag, cls, html) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }
  function esc(str) {
    return String(str)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  /* Expose helpers for plus.js */
  window.SPLIXUI = { $: $, $$: $$, el: el, esc: esc };

  /* =========================================================
     1. BRAND TEXT BINDING  (data-brand="key")
     ========================================================= */
  function bindBrand() {
    $$('[data-brand]').forEach(function (node) {
      var key = node.getAttribute('data-brand');
      if (Object.prototype.hasOwnProperty.call(CFG.brand, key)) {
        node.textContent = CFG.brand[key];
      }
    });
    var year = $('#year');
    if (year) year.textContent = new Date().getFullYear();
  }

  /* =========================================================
     2. PLACEHOLDER LINK HANDLING + TOAST
     Real URL  -> navigate normally.
     '#'       -> friendly "not configured yet" notice.
     ========================================================= */
  var toastEl, toastText, toastTimer;

  function showToast(msg) {
    if (!toastEl) return;
    toastText.textContent = msg;
    toastEl.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove('show'); }, 3800);
  }

  function initLinks() {
    toastEl = $('#toast');
    toastText = $('#toastText');

    var labels = {
      youtube: 'YouTube',
      discord: 'Discord',
      tiktok: 'TikTok',
    };

    /* Explicit link hooks: data-link="discord" | "youtube" */
    $$('.js-link').forEach(function (a) {
      a.addEventListener('click', function (e) {
        var key = a.getAttribute('data-link');
        var url = CFG.links[key];
        if (!url || url === '#') {
          e.preventDefault();
          showToast(labels[key] + ' link is not configured yet — set it in js/config.js (links.' + key + ').');
        }
      });
    });

    /* Community buttons that should open Discord */
    $$('.js-community').forEach(function (a) {
      a.addEventListener('click', function (e) {
        if (!CFG.links.discord || CFG.links.discord === '#') {
          e.preventDefault();
          showToast('Discord invite is not configured yet — set links.discord in js/config.js.');
        } else {
          e.currentTarget.setAttribute('target', '_blank');
          e.currentTarget.setAttribute('rel', 'noopener noreferrer');
        }
      });
    });

    /* Apply real hrefs once known */
    $$('.js-link').forEach(function (a) {
      var url = CFG.links[a.getAttribute('data-link')];
      if (url && url !== '#') {
        a.setAttribute('href', url);
        a.setAttribute('target', '_blank');
        a.setAttribute('rel', 'noopener noreferrer');
      }
    });
  }

  /* =========================================================
     3. NAVIGATION
     ========================================================= */
  function initNav() {
    var nav = $('#nav');
    var burger = $('#burger');
    var menu = $('#mobileMenu');

    function onScroll() {
      if (!nav) return;
      nav.classList.toggle('scrolled', window.scrollY > 24);
    }
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });

    if (burger && menu) {
      burger.addEventListener('click', function () {
        var open = menu.classList.toggle('open');
        burger.setAttribute('aria-expanded', open ? 'true' : 'false');
        burger.innerHTML = open
          ? '<i class="fa-solid fa-xmark"></i>'
          : '<i class="fa-solid fa-bars"></i>';
      });

      /* Close the mobile menu after choosing a link */
      $$('a', menu).forEach(function (a) {
        a.addEventListener('click', function () {
          menu.classList.remove('open');
          burger.setAttribute('aria-expanded', 'false');
          burger.innerHTML = '<i class="fa-solid fa-bars"></i>';
        });
      });
    }

    /* Smooth scrolling for same-page anchors */
    $$('a[href^="#"]').forEach(function (a) {
      a.addEventListener('click', function (e) {
        var href = a.getAttribute('href');
        if (!href || href === '#') return;
        var target = document.querySelector(href);
        if (!target) return;
        e.preventDefault();
        var offset = (window.innerWidth > 900 ? 74 : 64);
        var top = target.getBoundingClientRect().top + window.scrollY - offset + 1;
        window.scrollTo({ top: top, behavior: 'smooth' });
      });
    });

    /* Highlight the nav item for the section currently in view */
    var sections = $$('main section[id]');
    if (sections.length && 'IntersectionObserver' in window) {
      var links = $$('.nav-links .nav-link[href^="#"]');
      var spy = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          var id = entry.target.id;
          links.forEach(function (l) {
            l.classList.toggle('active', l.getAttribute('href') === '#' + id);
          });
        });
      }, { rootMargin: '-45% 0px -50% 0px' });
      sections.forEach(function (s) { spy.observe(s); });
    }
  }

  /* =========================================================
     4. SCROLL REVEAL
     ========================================================= */
  function initReveal() {
    var items = $$('.reveal');
    if (!items.length) return;

    /* Fallback: never let content stay invisible.
       If the observer produced nothing after a short delay
       (hidden tab, unusual engine, etc.) reveal everything. */
    function revealAll() {
      items.forEach(function (i) { i.classList.add('in'); });
    }

    if (!('IntersectionObserver' in window)) {
      revealAll();
      return;
    }

    var io = new IntersectionObserver(function (entries, obs) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add('in');
          obs.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -40px 0px' });

    items.forEach(function (i) { io.observe(i); });

    /* Safety net so nothing is ever permanently hidden */
    window.setTimeout(function () {
      if (!document.querySelector('.reveal.in')) revealAll();
    }, 1400);
  }

  /* =========================================================
     5. FAQ ACCORDION  (shared by both pages)
     ---------------------------------------------------------
     Markup produced:
       <div class="faq-item">
         <button class="faq-q" aria-expanded="false">…<span class="faq-ico">▾</span></button>
         <div class="faq-a"><div class="faq-a-inner">…</div></div>
       </div>
     ========================================================= */
  function buildAccordion(container, items) {
    if (!container) return;

    items.forEach(function (item) {
      var wrap = el('div', 'faq-item reveal');

      var btn = el('button', 'faq-q');
      btn.type = 'button';
      btn.setAttribute('aria-expanded', 'false');
      btn.innerHTML =
        '<span>' + esc(item.q) + '</span>' +
        '<span class="faq-ico"><i class="fa-solid fa-chevron-down"></i></span>';

      var panel = el('div', 'faq-a');
      panel.setAttribute('role', 'region');
      var inner = el('div', 'faq-a-inner', esc(item.a));
      panel.appendChild(inner);

      wrap.appendChild(btn);
      wrap.appendChild(panel);
      container.appendChild(wrap);

      btn.addEventListener('click', function () {
        var isOpen = wrap.classList.contains('open');

        /* Close every other open item in this accordion */
        $$('.faq-item.open', container).forEach(function (other) {
          if (other === wrap) return;
          other.classList.remove('open');
          $('.faq-q', other).setAttribute('aria-expanded', 'false');
          $('.faq-a', other).style.maxHeight = '0px';
        });

        if (isOpen) {
          wrap.classList.remove('open');
          btn.setAttribute('aria-expanded', 'false');
          panel.style.maxHeight = '0px';
        } else {
          wrap.classList.add('open');
          btn.setAttribute('aria-expanded', 'true');
          panel.style.maxHeight = panel.scrollHeight + 'px';
        }
      });
    });

    /* Keep open panels correctly sized if the layout changes */
    window.addEventListener('resize', function () {
      $$('.faq-item.open .faq-a', container).forEach(function (p) {
        p.style.maxHeight = p.scrollHeight + 'px';
      });
    });
  }

  /* =========================================================
     6. COUNTER ANIMATION (stats)
     Only runs when a stat value is a pure number + optional suffix.
     ========================================================= */
  function animateCount(node, raw) {
    var match = /^(\d+)([^\d]*)$/.exec(String(raw).trim());
    if (!match) { node.textContent = raw; return; }

    var target = parseInt(match[1], 10);
    var suffix = match[2] || '';
    var dur = 1500;
    var start = null;

    function step(ts) {
      if (start === null) start = ts;
      var p = Math.min((ts - start) / dur, 1);
      var eased = 1 - Math.pow(1 - p, 3);
      node.textContent = Math.round(target * eased) + suffix;
      if (p < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  function renderStats() {
    var wrap = $('#statsGrid');
    if (!wrap) return;

    CFG.stats.forEach(function (s, i) {
      var card = el('div', 'stat reveal');
      card.setAttribute('data-delay', String(Math.min(i, 4)));
      card.innerHTML =
        '<div class="stat-icon"><i class="' + esc(s.icon) + '"></i></div>' +
        '<div class="stat-value">' + esc(s.value) + '</div>' +
        '<div class="stat-label">' + esc(s.label) + '</div>';
      wrap.appendChild(card);

      /* Animate only if the placeholder value is numeric */
      if (/^\d+([^\d]*)$/.test(String(s.value).trim())) {
        var valueNode = $('.stat-value', card);
        if ('IntersectionObserver' in window) {
          var io = new IntersectionObserver(function (entries, obs) {
            entries.forEach(function (e) {
              if (e.isIntersecting) { animateCount(valueNode, s.value); obs.disconnect(); }
            });
          }, { threshold: 0.5 });
          io.observe(card);
        }
      }
    });
  }

  /* =========================================================
     7. HOME PAGE CONTENT
     ========================================================= */
  function renderFeatures() {
    var wrap = $('#featuresGrid');
    if (!wrap) return;

    CFG.features.forEach(function (f, i) {
      var card = el('div', 'card reveal');
      card.setAttribute('data-delay', String(i % 3));
      card.innerHTML =
        '<div class="card-icon"><i class="' + esc(f.icon) + '"></i></div>' +
        '<h3>' + esc(f.title) + '</h3>' +
        '<p>' + esc(f.text) + '</p>';
      wrap.appendChild(card);
    });
  }

  function renderTutorials() {
    var wrap = $('#tutorialsGrid');
    if (!wrap) return;

    CFG.tutorials.forEach(function (t, i) {
      var art = /^art-\d+$/.test(t.art) ? t.art : 'art-1';
      var isPlaceholder = !t.link || t.link === '#';

      var card = el('article', 'card tut-card reveal');
      card.setAttribute('data-delay', String(i % 3));
      card.innerHTML =
        '<div class="tut-thumb ' + esc(art) + '">' +
          '<span class="tut-badge">' + esc(t.category) + '</span>' +
          '<i class="' + esc(t.icon) + '"></i>' +
        '</div>' +
        '<div class="tut-body">' +
          '<h3>' + esc(t.title) + '</h3>' +
          '<p>' + esc(t.text) + '</p>' +
          '<a class="tut-more" href="' + esc(t.link || '#') + '">' +
            'READ MORE <i class="fa-solid fa-arrow-right"></i>' +
          '</a>' +
        '</div>';

      var link = $('.tut-more', card);
      if (isPlaceholder) {
        link.addEventListener('click', function (e) {
          e.preventDefault();
          showToast('This tutorial link is not configured yet — set it in js/config.js (tutorials).');
        });
      } else {
        link.setAttribute('target', '_blank');
        link.setAttribute('rel', 'noopener noreferrer');
      }

      wrap.appendChild(card);
    });
  }

  function renderCommunity() {
    var list = $('#communityList');
    if (!list) return;

    $$('[data-community]').forEach(function (node) {
      var key = node.getAttribute('data-community');
      if (CFG.community[key]) node.textContent = CFG.community[key];
    });

    CFG.community.bullets.forEach(function (b) {
      list.appendChild(el('li', null, '<i class="fa-solid fa-circle-check"></i><span>' + esc(b) + '</span>'));
    });
  }

  /* =========================================================
     7b. PANEL PRICING BANNER  (config: panelPricing)
     ========================================================= */
  function renderPanelPricing() {
    var wrap = $('#priceBoxes');
    var cfg = CFG.panelPricing;
    if (!wrap || !cfg) return;

    var titleEl = $('#panelTitle');
    if (titleEl && cfg.title) titleEl.textContent = cfg.title;

    cfg.plans.forEach(function (p, i) {
      var box = el('div', 'price-box' + (p.featured ? ' price-box--featured' : ''));
      var html = '';
      if (p.badge) html += '<span class="price-badge">' + esc(p.badge) + '</span>';
      html += '<div class="price-amount">' + esc(p.amount) + '</div>';
      html += '<div class="price-period">' + esc(p.period) + '</div>';
      if (p.note) html += '<div class="price-note">' + esc(p.note) + '</div>';
      box.innerHTML = html;
      wrap.appendChild(box);
    });

    var foot = $('#priceFootnote');
    if (foot && cfg.footnote) foot.textContent = cfg.footnote;
  }

  /* =========================================================
     7c. PAYMENT METHODS BANNER  (config: paymentMethods)
     ---------------------------------------------------------
     Honest by design: a method only becomes a real, clickable
     link when `configured === true` AND a `url` exists. Anything
     else renders a clear "NOT CONFIGURED" badge and is inert.
     ========================================================= */
  var PAY_ICONS = {
    /* Generic, non-official glyphs only — no imitation of
       trademarked logos. Colours are accent hints matched to the
       site's red theme; swap the `icon` string for a real logo
       <img> whenever you have a licensed asset. */
    cashplus: { icon: 'fa-solid fa-money-bill-transfer', color: '#ff7a1f' },
    wafacash: { icon: 'fa-solid fa-shop',                color: '#ffb02e' },
    bank:     { icon: 'fa-solid fa-building-columns',    color: '#ff5f4d' },
  };

  function renderPaymentMethods() {
    var wrap = $('#payMethods');
    if (!wrap || !CFG.paymentMethods) return;

    var titleEl = $('#paymentTitle');
    if (titleEl && CFG.paymentSection && CFG.paymentSection.title) {
      titleEl.textContent = CFG.paymentSection.title;
    }

    var noticeEl = $('#paymentBannerNotice');
    if (noticeEl && CFG.paymentSection && CFG.paymentSection.notice) {
      noticeEl.textContent = CFG.paymentSection.notice;
    }

    CFG.paymentMethods.forEach(function (m) {
      var live = m.configured === true && !!m.url;
      var glyph = PAY_ICONS[m.icon] || { icon: 'fa-solid fa-wallet', color: 'var(--red-bright)' };

      var card = el('div', 'pay-method');
      var html = '';
      html += '<span class="pay-icon" style="color:' + esc(glyph.color) + '">' +
                '<i class="' + esc(glyph.icon) + '"></i></span>';
      html += '<span class="pay-name">' + esc(m.name) + '</span>';
      html += '<span class="pay-status' + (live ? ' pay-status--live' : '') + '">' +
                esc(live ? 'AVAILABLE' : (m.status || 'NOT CONFIGURED')) + '</span>';
      card.innerHTML = html;

      if (live) {
        var a = el('a', null, '');
        a.href = m.url;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        a.setAttribute('aria-label', m.name + ' payment — opens in a new tab');
        a.style.display = 'contents';
        a.appendChild(card);
        wrap.appendChild(a);
      } else {
        card.setAttribute('aria-label', m.name + ' — not configured yet');
        wrap.appendChild(card);
      }
    });
  }

  /* =========================================================
     8. BOOT
     ========================================================= */
  function boot() {
    bindBrand();
    initNav();
    initLinks();

    renderStats();
    renderFeatures();
    renderTutorials();
    renderCommunity();
    renderPanelPricing();
    renderPaymentMethods();

    /* FAQ works on both pages — each page supplies its own list */
    buildAccordion($('#faqList'), CFG.faq);
    buildAccordion($('#plusFaqList'), CFG.plusFaq);

    initReveal();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  /* Expose accordion builder for plus.js */
  window.SPLIXUI.buildAccordion = buildAccordion;
  window.SPLIXUI.showToast = showToast;
})();
