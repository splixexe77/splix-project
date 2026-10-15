/* =============================================================
   SPLIX X CHEAT  —  PLUS membership page  (js/plus.js)
   -------------------------------------------------------------
   Renders the membership cards and the comparison table from
   the configuration in js/config.js.

   >>> TO EDIT PLANS / PRICES / FEATURES, EDIT js/config.js <<<

   PAYMENT STATUS MAP  (plan.status in config.js)
   -------------------------------------------------------------
   'current' -> shown as the visitor's plan, button disabled
   'soon'    -> button disabled, clearly labelled "COMING SOON"
   'live'    -> reserved for a future payment provider; only
                becomes clickable when plan.payment.enabled is
                true AND a checkoutUrl is set.

   Nothing in this file collects, stores or transmits any
   payment information. No payment form exists anywhere.
   ============================================================= */

(function () {
  'use strict';

  var CFG = window.SPLIX;
  var UI = window.SPLIXUI;

  if (!CFG || !UI) {
    console.error('[SPLIX] config.js / main.js must load before plus.js.');
    return;
  }

  var $ = UI.$, el = UI.el, esc = UI.esc;
  var showToast = UI.showToast;

  /* ---------------------------------------------------------
     Determine how a plan's button should behave.
     --------------------------------------------------------- */
  function resolvePlan(plan) {
    var pay = plan.payment || {};

    /* 'live' only counts when explicitly enabled with a real URL */
    if (plan.status === 'live') {
      if (pay.enabled === true && pay.checkoutUrl) {
        return { mode: 'link', href: pay.checkoutUrl };
      }
      /* Fall back to a safe, clearly-disabled state */
      return { mode: 'soon' };
    }
    if (plan.status === 'current') return { mode: 'current' };
    return { mode: 'soon' };
  }

  /* ---------------------------------------------------------
     Build one plan card
     --------------------------------------------------------- */
  function buildPlan(plan, index) {
    var action = resolvePlan(plan);
    var featured = !!plan.featured;

    var card = el('article', 'plan reveal' + (featured ? ' plan--featured' : ''));
    card.setAttribute('data-delay', String(Math.min(index, 3)));
    card.id = 'plan-' + plan.id;

    /* --- Header --- */
    var html = '';
    if (plan.badge) {
      html += '<span class="plan-badge">' + esc(plan.badge) + '</span>';
    }
    html += '<h3 class="plan-tier">' + esc(plan.name) + '</h3>';
    html += '<p class="plan-desc">SPLIX X CHEAT</p>';

    /* --- Price --- */
    var soonish = /coming soon/i.test(String(plan.price));
    html += '<div class="plan-price' + (soonish ? ' plan-price--soon' : '') + '">' +
              esc(plan.price) +
            '</div>';
    if (plan.period) {
      html += '<p class="plan-period">' + esc(plan.period) + '</p>';
    }

    /* --- Features --- */
    html += '<ul class="plan-features">';
    (plan.features || []).forEach(function (f) {
      html += '<li><i class="fa-solid fa-check"></i><span>' + esc(f) + '</span></li>';
    });
    html += '</ul>';

    card.innerHTML = html;

    /* --- Button --- */
    var btnWrap = el('div', null, '');
    btnWrap.style.marginTop = 'auto';

    var btn = el('button', 'btn btn-block');
    btn.type = 'button';

    if (action.mode === 'link') {
      /* Reserved for a future, real payment provider */
      btn.className = 'btn btn-primary btn-block';
      btn.innerHTML = '<i class="fa-solid fa-arrow-up-right-from-square"></i> ' + esc(plan.buttonText);
      btn.addEventListener('click', function () { window.open(action.href, '_blank', 'noopener'); });
    } else if (action.mode === 'current') {
      btn.className = 'btn btn-current btn-block';
      btn.innerHTML = '<i class="fa-solid fa-circle-check"></i> ' + esc(plan.buttonText);
      btn.disabled = true;
      btn.setAttribute('aria-disabled', 'true');
    } else {
      btn.className = 'btn btn-soon btn-block';
      btn.innerHTML = '<i class="fa-solid fa-hourglass-half"></i> ' + esc(plan.buttonText);
      btn.disabled = true;
      btn.setAttribute('aria-disabled', 'true');

      /* Explain the state instead of silently doing nothing */
      var explain = el('button', 'btn btn-soon btn-block');
      explain.type = 'button';
      explain.innerHTML = '<i class="fa-solid fa-hourglass-half"></i> ' + esc(plan.buttonText);
      explain.setAttribute('aria-label', plan.name + ' membership — coming soon');
      explain.addEventListener('click', function () {
        showToast(plan.name + ' membership is not available yet — pricing and launch date will be announced soon.');
      });
      btn = explain;
    }

    btnWrap.appendChild(btn);

    /* --- Honest note under the button --- */
    var note = el('p', 'plan-note');
    if (action.mode === 'current') {
      note.textContent = 'You are viewing the free tier.';
    } else if (action.mode === 'link') {
      note.textContent = 'You will be redirected to a secure checkout.';
    } else {
      note.textContent = 'No payment is processed on this site.';
    }
    btnWrap.appendChild(note);

    card.appendChild(btnWrap);
    return card;
  }

  /* ---------------------------------------------------------
     Render all plan cards
     --------------------------------------------------------- */
  function renderPlans() {
    var grid = $('#plansGrid');
    if (!grid) return;

    CFG.plans.forEach(function (plan, i) {
      grid.appendChild(buildPlan(plan, i));
    });
  }

  /* ---------------------------------------------------------
     Render the comparison table
     --------------------------------------------------------- */
  function cellMarkup(value) {
    var v = String(value).toLowerCase();
    if (v === 'yes') {
      return '<i class="fa-solid fa-check tick" aria-label="Included"></i>';
    }
    if (v === 'no') {
      return '<i class="fa-solid fa-minus cross" aria-label="Not included"></i>';
    }
    return esc(value); /* allow short custom text like "Basic" */
  }

  function renderComparison() {
    var table = $('#compareTable');
    if (!table) return;

    var cols = CFG.comparison.columns;
    var rows = CFG.comparison.rows;

    var highlight = {};
    CFG.plans.forEach(function (p) { if (p.featured) highlight[p.id] = true; });

    /* --- Head --- */
    var thead = el('thead');
    var headRow = el('tr');
    headRow.appendChild(el('th', null, 'Feature'));
    cols.forEach(function (c) {
      var th = el('th', highlight[c.id] ? 'is-plus' : null, esc(c.label));
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);

    /* --- Body --- */
    var tbody = el('tbody');
    rows.forEach(function (r) {
      var tr = el('tr');
      tr.appendChild(el('td', null, esc(r.label)));
      cols.forEach(function (c) {
        var value = (r[c.id] !== undefined) ? r[c.id] : 'no';
        tr.appendChild(el('td', null, cellMarkup(value)));
      });
      tbody.appendChild(tr);
    });

    table.appendChild(thead);
    table.appendChild(tbody);
  }

  /* ---------------------------------------------------------
     Payment notice band
     --------------------------------------------------------- */
  function renderNotice() {
    var node = $('#paymentNotice');
    if (node && CFG.payments && CFG.payments.notice) {
      node.textContent = CFG.payments.notice;
    }
  }

  /* ---------------------------------------------------------
     Boot
     --------------------------------------------------------- */
  function boot() {
    renderPlans();
    renderComparison();
    renderNotice();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
