/* =============================================================
   SPLIX X CHEAT — unlock page controller  (js/unlock.js)
   -------------------------------------------------------------
   The page is a *view only*: every security decision is made by
   the server.  This script fetches state, shows honest messages
   and never unlocks anything on its own — the destination URL is
   only ever returned by POST /api/u/<slug>/claim after the server
   has confirmed every configured requirement (Subscribe + Like).

   Task states come from the server: NOT STARTED / PENDING /
   MANUAL REVIEW / VERIFIED / CONFIRMED (honor) / FAILED.
   An honor-system confirmation is NEVER shown as "verified".
   ============================================================= */
(function () {
  'use strict';

  var body = document.body;
  var SLUG = body.getAttribute('data-slug') || '';
  var MODE = body.getAttribute('data-mode') || 'manual';
  var API = '/api/u/' + encodeURIComponent(SLUG);

  var $ = function (id) { return document.getElementById(id); };

  var els = {
    notice: $('unlockNotice'),
    stepBtn: $('stepBtn'),
    stepLabel: $('stepLabel'),
    stepCount: $('stepCount'),
    stepBar: $('stepBar'),
    stepFill: $('stepFill'),
    verifyBtn: $('verifyBtn'),
    verifyLabel: $('verifyLabel'),
    claimBtn: $('claimBtn'),
    claimLabel: $('claimLabel'),
    taskExtra: $('taskExtra'),
    likeActionWrap: $('likeActionWrap'),
    toast: $('toast'),
    toastText: $('toastText')
  };

  var verified = false;      // ALL server requirements satisfied
  var polling = null;
  var busy = false;          // an action request is in flight
  var cfg = null;            // last server config payload
  var TASKS = [];            // server task descriptors + states
  var uiTask = {};           // transient client display: id -> checking|failed
  var likeBtn = null;        // injected like-action button
  var likeLabel = null;

  var MESSAGES = {
    success: 'Subscription confirmed by the server — your link is unlocked.',
    all_done: 'Every required step was confirmed on the server — your link is unlocked.',
    not_subscribed: 'YouTube says this Google account is <b>not subscribed</b> to the channel yet. Subscribe first, then press Verify again.',
    denied: 'Google sign-in was cancelled or refused. Nothing was unlocked.',
    state_invalid: 'That verification link expired or was already used. Please verify again.',
    auth_expired: 'Your Google sign-in expired. Please verify again.',
    permission_denied: 'Google did not grant permission to read your YouTube subscriptions. Sign in again and approve the requested permission.',
    exchange_failed: 'Google rejected the verification response. Please try again.',
    api_unreachable: 'The verification service is unreachable right now — nothing was unlocked. Please try again shortly.',
    api_error: 'The YouTube API could not confirm your subscription right now — nothing was unlocked. Please try again.',
    not_configured: 'Automatic verification is not configured on this server yet.',
    unknown: 'Verification could not be completed, so the link stays locked. Please try again or contact a moderator.'
  };

  /* ------------------------------------------------ small helpers */
  function toast(message) {
    if (!els.toast) return;
    els.toastText.textContent = message;
    els.toast.classList.add('show');
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { els.toast.classList.remove('show'); }, 3600);
  }

  function notice(kind, html) {
    if (!els.notice) return;
    if (!html) { els.notice.hidden = true; els.notice.innerHTML = ''; return; }
    els.notice.hidden = false;
    els.notice.className = 'unlock-notice unlock-notice--' + kind;
    els.notice.innerHTML = html;
  }

  function request(url, options) {
    return fetch(url, options || {}).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        data.__status = res.status;
        return data;
      });
    });
  }

  function reviewStore(action, token) {
    var key = 'splixUnlockReview:' + SLUG;
    try {
      if (action === 'get') return sessionStorage.getItem(key);
      if (action === 'set') sessionStorage.setItem(key, token);
      if (action === 'clear') sessionStorage.removeItem(key);
    } catch (e) { /* storage disabled — polling simply restarts */ }
    return null;
  }

  function taskById(id) {
    for (var i = 0; i < TASKS.length; i++) {
      if (TASKS[i].id === id) return TASKS[i];
    }
    return null;
  }

  function isDone(task) {
    return !!task && task.state !== 'unverified' && !!task.state;
  }

  function doneCount() {
    var n = 0;
    for (var i = 0; i < TASKS.length; i++) if (isDone(TASKS[i])) n++;
    return n;
  }

  function subscribeTask() {
    return taskById('subscribe') || taskById('custom') || TASKS[0] || null;
  }

  function applyServerTasks(list) {
    if (!list || !list.length) return;
    TASKS = list;
  }

  /* ------------------------------------------------ state rendering */
  function setProgress(done, total) {
    var t = typeof total === 'number' ? total : (TASKS.length || 1);
    if (els.stepCount) els.stepCount.textContent = done + '/' + t;
    if (els.stepBar) {
      els.stepBar.setAttribute('aria-valuenow', String(done));
      els.stepBar.setAttribute('aria-valuemax', String(t));
      els.stepBar.classList.toggle('done', t > 0 && done >= t);
      if (els.stepFill) {
        els.stepFill.style.width = t > 0 ? Math.round((done / t) * 100) + '%' : '0%';
      }
    }
    if (els.stepBtn) els.stepBtn.classList.toggle('is-done', isDone(subscribeTask()));
  }

  /* Honest per-task chip — never claims more than the server says. */
  function chipFor(task) {
    if (!task) return { text: 'NOT STARTED', cls: 'is-notstarted' };
    if (uiTask[task.id] === 'checking') return { text: 'PENDING', cls: 'is-pending' };
    if (uiTask[task.id] === 'failed') return { text: 'FAILED', cls: 'is-failed' };
    if (task.state === 'verified') return { text: 'VERIFIED ✓', cls: 'is-verified' };
    if (task.state === 'manual_approved') return { text: 'VERIFIED ✓ (REVIEW)', cls: 'is-verified' };
    if (task.state === 'confirmed') return { text: 'CONFIRMED (HONOR)', cls: 'is-honor' };
    if (reviewStore('get')) return { text: 'MANUAL REVIEW', cls: 'is-manual' };
    return { text: 'NOT STARTED', cls: 'is-notstarted' };
  }

  function paintChip(el, task) {
    if (!el) return;
    var chip = chipFor(task);
    el.hidden = false;
    el.className = 'unlock-task-state ' + chip.cls;
    el.textContent = chip.text;
  }

  function refreshChips() {
    if (els.stepBtn) paintChip(els.stepBtn.querySelector('[data-task-state]'), TASKS[0]);
    var cards = document.querySelectorAll('[data-task-card]');
    Array.prototype.forEach.call(cards, function (card) {
      var id = card.getAttribute('data-task-card');
      paintChip(card.querySelector('[data-task-state]'), taskById(id));
      var done = isDone(taskById(id));
      card.classList.toggle('is-done', done);
    });
  }

  /* Inject the task cards after the server-rendered first card. */
  function renderExtraTasks() {
    if (!els.taskExtra) return;
    els.taskExtra.innerHTML = '';
    for (var i = 1; i < TASKS.length; i++) {
      (function (task) {
        var card = document.createElement('a');
        card.className = 'unlock-step unlock-step--extra';
        card.setAttribute('data-task-card', task.id);
        if (task.url) card.setAttribute('href', task.url);
        card.setAttribute('target', '_blank');
        card.setAttribute('rel', 'noopener noreferrer');

        var icon = document.createElement('i');
        icon.className = task.id === 'like' ? 'fa-brands fa-youtube' : 'fa-solid fa-bolt';
        icon.setAttribute('aria-hidden', 'true');

        var label = document.createElement('span');
        label.id = 'taskLabel_' + task.id;
        label.textContent = task.title || task.label || task.id;

        var chip = document.createElement('span');
        chip.setAttribute('data-task-state', task.id);

        var arrow = document.createElement('i');
        arrow.className = 'fa-solid fa-arrow-right';
        arrow.setAttribute('aria-hidden', 'true');

        card.appendChild(icon);
        card.appendChild(label);
        card.appendChild(chip);
        card.appendChild(arrow);
        els.taskExtra.appendChild(card);
      })(TASKS[i]);
    }
  }

  /* Like-step action button (honor confirm / like review) — injected. */
  function refreshLikeAction() {
    if (!els.likeActionWrap) return;
    var like = taskById('like');
    if (!like || isDone(like)) {
      els.likeActionWrap.innerHTML = '';
      likeBtn = null;
      likeLabel = null;
      return;
    }
    if (!likeBtn) {
      likeBtn = document.createElement('button');
      likeBtn.className = 'btn btn-ghost btn-block';
      likeBtn.id = 'likeBtn';
      likeBtn.type = 'button';
      var ic = document.createElement('i');
      ic.className = 'fa-brands fa-youtube';
      ic.setAttribute('aria-hidden', 'true');
      likeLabel = document.createElement('span');
      likeLabel.id = 'likeLabel';
      likeBtn.appendChild(ic);
      likeBtn.appendChild(likeLabel);
      likeBtn.addEventListener('click', onLikeAction);
      els.likeActionWrap.appendChild(likeBtn);
    }
    likeLabel = likeBtn.querySelector('span');

    var subDone = isDone(subscribeTask());
    var pending = !!reviewStore('get');
    if (like.method === 'honor') {
      likeBtn.hidden = false;
      if (!subDone) {
        likeLabel.textContent = 'LIKE STEP — VERIFY SUBSCRIBE FIRST';
        likeBtn.disabled = true;
      } else if (busy && uiTask.like === 'checking') {
        likeLabel.textContent = 'RECORDING YOUR CONFIRMATION…';
        likeBtn.disabled = true;
      } else {
        likeLabel.textContent = 'I LIKED THE VIDEO — CONFIRM (HONOR SYSTEM)';
        likeBtn.disabled = false;
      }
    } else if (MODE === 'oauth') {
      /* Automatic subscribe mode: the moderator review exists only for
         the like step, so offer it as its own action. */
      likeBtn.hidden = false;
      likeLabel.textContent = pending
        ? 'LIKE REVIEW PENDING — WAITING FOR APPROVAL'
        : 'I LIKED IT — REQUEST REVIEW';
      likeBtn.disabled = pending || busy;
    } else {
      /* Manual mode: one moderator review covers every step — the main
         VERIFY button already requests it, so no duplicate action here. */
      els.likeActionWrap.innerHTML = '';
      likeBtn = null;
      likeLabel = null;
    }
  }

  /* Main (subscribe / only) verification button label. */
  function refreshVerifyAction() {
    if (!els.verifyBtn || !els.verifyLabel) return;
    var sub = subscribeTask();
    if (verified) return;                    // setVerified() owns the label now
    if (isDone(sub)) {
      els.verifyLabel.textContent = 'THIS STEP IS DONE ✓';
      els.verifyBtn.disabled = true;
      els.verifyBtn.classList.remove('btn-ghost');
      els.verifyBtn.classList.add('btn-current');
      return;
    }
    els.verifyBtn.disabled = !!reviewStore('get') || busy;
    els.verifyBtn.classList.add('btn-ghost');
    els.verifyBtn.classList.remove('btn-current');

    if (reviewStore('get')) {
      els.verifyLabel.textContent = 'REVIEW PENDING — WAITING FOR A MODERATOR';
      return;
    }
    if (MODE === 'oauth' && sub && sub.auto) {
      els.verifyLabel.textContent = 'VERIFY WITH GOOGLE';
      return;
    }
    if (TASKS.length > 1) {
      els.verifyLabel.textContent = 'I\'VE COMPLETED BOTH STEPS — REQUEST REVIEW';
    } else if (sub && sub.id === 'custom') {
      els.verifyLabel.textContent = 'I\'VE DONE IT — REQUEST REVIEW';
    } else {
      els.verifyLabel.textContent = 'I\'VE SUBSCRIBED — REQUEST REVIEW';
    }
  }

  function refreshAll() {
    refreshChips();
    refreshVerifyAction();
    refreshLikeAction();
    setProgress(doneCount(), TASKS.length || 1);
  }

  function setVerified(sourceLabel) {
    var wasVerified = verified;
    verified = true;
    uiTask = {};

    if (els.verifyBtn) {
      els.verifyBtn.classList.remove('btn-ghost');
      els.verifyBtn.classList.add('btn-current');
      els.verifyBtn.disabled = true;
      els.verifyBtn.removeAttribute('style');
    }
    if (els.verifyLabel) {
      els.verifyLabel.textContent = TASKS.length > 1
        ? 'ALL STEPS VERIFIED ✓'
        : (cfg && cfg.verification && cfg.verification.ready
          ? 'SUBSCRIPTION VERIFIED'
          : 'STEP VERIFIED ✓');
    }

    if (els.claimBtn) {
      els.claimBtn.disabled = false;
      els.claimBtn.classList.remove('btn-soon');
      els.claimBtn.classList.add('btn-primary', 'unlock-claim-ready');
    }

    if (!wasVerified) {
      notice('success', '<b>Verified.</b> ' +
        (TASKS.length > 1 ? MESSAGES.all_done : MESSAGES.success) +
        (sourceLabel ? ' (' + sourceLabel + ')' : ''));
    }
    if (polling) { clearInterval(polling); polling = null; }
    stopReviewPoll();
    refreshAll();
  }

  function setUnverified() {
    verified = false;
    if (els.claimBtn) {
      els.claimBtn.disabled = true;
      els.claimBtn.classList.add('btn-soon');
      els.claimBtn.classList.remove('btn-primary', 'unlock-claim-ready');
    }
    if (els.verifyBtn) {
      els.verifyBtn.classList.remove('btn-current');
      els.verifyBtn.classList.add('btn-ghost');
      els.verifyBtn.disabled = false;
    }
    refreshAll();
  }

  function setPending(message) {
    if (verified) return;
    if (els.verifyLabel) els.verifyLabel.textContent = 'REVIEW REQUESTED — WAITING FOR APPROVAL';
    notice('info', message ||
      'Your review request was sent. A moderator must check every step by hand — nothing is unlocked until then.');
    refreshAll();
  }

  /* ------------------------------------------------ config load */
  function loadConfig() {
    return request(API).then(function (data) {
      if (!data.ok) {
        notice('error', data.message || 'This unlock page is unavailable.');
        if (els.verifyBtn) els.verifyBtn.disabled = true;
        if (els.claimBtn) els.claimBtn.disabled = true;
        return null;
      }
      cfg = data;
      if (data.mode) MODE = data.mode;

      if (data.tasks && data.tasks.length) {
        applyServerTasks(data.tasks);
      } else if (data.action) {
        /* Defensive fallback: single task from the legacy action shape. */
        TASKS = [{
          id: data.action.type === 'custom' ? 'custom' : 'subscribe',
          title: data.action.label || '',
          label: data.action.label || '',
          url: data.action.url || '',
          method: MODE === 'oauth' ? 'oauth' : 'manual',
          auto: MODE === 'oauth',
          state: data.state === 'verified' ? 'verified' : 'unverified'
        }];
      } else {
        TASKS = [];
      }

      var first = TASKS[0];
      if (first && els.stepBtn) {
        if (first.url) els.stepBtn.setAttribute('href', first.url);
        els.stepBtn.setAttribute('data-task-card', first.id);
        if (els.stepLabel) els.stepLabel.textContent = first.title || first.label || '';
      }
      renderExtraTasks();

      if (data.mode === 'manual') {
        var isCustom = data.action && data.action.type === 'custom';
        if (!isCustom && data.verification && data.verification.ready === false &&
            data.state !== 'verified' && data.mode === 'manual') {
          notice('warn', '<b>Automatic YouTube verification is not active on this server yet.</b> ' +
            'Until the Google API credentials are added, a moderator checks each subscription by hand. ' +
            'Nothing unlocks until a moderator approves it.');
        } else if (isCustom && data.state !== 'verified') {
          notice('warn', '<b>This step cannot be verified automatically.</b> ' +
            'The platform offers no official API to confirm it, so a moderator checks ' +
            'each request by hand. Nothing unlocks until a moderator approves it.');
        }
      }

      if (data.state === 'verified') {
        setVerified(data.stateMethod === 'manual'
          ? 'approved by a moderator'
          : 'verified by YouTube API');
      } else {
        verified = false;
        setUnverified();
        // Sync a previously stored manual-review request (in case an
        // administrator approved it while this page was closed).
        var stored = reviewStore('get');
        if (stored) checkReview(stored, true);
      }
      refreshAll();
    }).catch(function () {
      notice('error', 'Could not reach the unlock server. Refresh the page to try again.');
    });
  }

  /* ------------------------------------------------ URL results */
  function handleUrlResult() {
    var params = new URLSearchParams(window.location.search);
    var err = params.get('err');
    var ok = params.get('v');
    if (err) {
      verified = false;
      uiTask[(TASKS[0] && TASKS[0].id) || 'subscribe'] = 'failed';
      notice('error', MESSAGES[err] || MESSAGES.unknown);
      if (params.get('err') === 'api_error' || params.get('err') === 'api_unreachable' ||
          params.get('err') === 'exchange_failed') {
        notice('error', (MESSAGES[err] || MESSAGES.unknown) +
          ' If it keeps failing, use the review button below or contact a moderator.');
      }
      if (window.history && window.history.replaceState) {
        window.history.replaceState({}, '', window.location.pathname);
      }
    } else if (ok) {
      toast('Subscription verified by the YouTube API.');
      if (window.history && window.history.replaceState) {
        window.history.replaceState({}, '', window.location.pathname);
      }
    }
  }

  /* ------------------------------------------------ manual review */
  function checkReview(token, silent) {
    return request(API + '/review/status', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: token })
    }).then(function (data) {
      if (data.verified) {
        if (data.tasks && data.tasks.length) applyServerTasks(data.tasks);
        setVerified('approved by a moderator');
        loadConfig();                        // authoritative server sync
        return true;
      }
      if (data.state === 'approved') {
        // Approved, but the server says requirements remain (e.g. an
        // honor-system like confirmation) — never claim success here.
        if (data.tasks && data.tasks.length) applyServerTasks(data.tasks);
        notice('info', 'A moderator <b>approved</b> your review request — one step is still ' +
          'left before the link unlocks. Complete it below, then claim your link.');
        loadConfig();
        return false;
      }
      if (data.state === 'rejected') {
        reviewStore('clear');
        stopReviewPoll();
        notice('error', 'A moderator <b>rejected</b> this review request, so the link stays locked. ' +
          'Make sure you actually completed every step, then request a new review.');
        if (els.verifyLabel) els.verifyLabel.textContent = 'REQUEST A NEW REVIEW';
        refreshAll();
        return false;
      }
      if (data.state === 'expired' || data.state === 'unknown') {
        reviewStore('clear');
        stopReviewPoll();
        if (!silent) {
          notice('warn', 'Your previous review request expired. Complete the steps again, then request a new review.');
        }
        if (els.verifyLabel) els.verifyLabel.textContent = 'REQUEST A NEW REVIEW';
        refreshAll();
        return false;
      }
      if (data.state === 'pending') {
        setPending();
        startReviewPoll(token);
      }
      return false;
    }).catch(function () { return false; });
  }

  function startReviewPoll(token) {
    if (polling) return;
    polling = setInterval(function () { checkReview(token, true); }, 5000);
  }

  function stopReviewPoll() {
    if (polling) { clearInterval(polling); polling = null; }
  }

  function setBusy(on, labelEl) {
    busy = on;
    if (els.verifyBtn) {
      els.verifyBtn.disabled = on;
      els.verifyBtn.classList.toggle('is-busy', on);
    }
    if (likeBtn) {
      likeBtn.disabled = on;
      likeBtn.classList.toggle('is-busy', on);
    }
    if (labelEl && on) labelEl.textContent = 'CHECKING…';
    if (!on) refreshAll();
  }

  function requestReview(fromLike) {
    if (verified) return;
    var existing = reviewStore('get');
    if (existing) { checkReview(existing); return; }

    if (fromLike) {
      uiTask.like = 'checking';
      refreshAll();
      setBusy(true, likeLabel || els.verifyLabel);
    } else {
      setBusy(true, els.verifyLabel);
    }
    request(API + '/review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({})
    }).then(function (data) {
      if (fromLike) delete uiTask.like;
      setBusy(false);
      if (!data.ok) {
        notice('error', data.message || 'Could not send the review request.');
        return;
      }
      reviewStore('set', data.token);
      if (data.state === 'approved') {
        checkReview(data.token);
        return;
      }
      setPending();
      startReviewPoll(data.token);
      toast('Review request sent — waiting for moderator approval.');
    }).catch(function () {
      if (fromLike) delete uiTask.like;
      setBusy(false);
      notice('error', 'Could not reach the unlock server. Please try again.');
    });
  }

  /* ------------------------------------- like step (honor / review) */
  function onLikeAction() {
    if (busy || verified) return;
    var like = taskById('like');
    if (!like || isDone(like)) return;

    if (like.method === 'honor') {
      uiTask.like = 'checking';
      refreshAll();
      busy = true;
      notice('info', 'Recording your like confirmation on the server…');
      request(API + '/like', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ confirm: true })
      }).then(function (data) {
        busy = false;
        delete uiTask.like;
        if (data.ok) {
          if (data.tasks && data.tasks.length) applyServerTasks(data.tasks);
          notice('success', 'Like step <b>confirmed by you</b> on this server. ' +
            'Recorded as an <b>honor-system confirmation</b> — the YouTube API cannot ' +
            'verify individual likes, so this is not labelled as verified.');
          toast('Like confirmed (honor system).');
          refreshAll();
          loadConfig();                      // authoritative server sync
        } else {
          uiTask.like = 'failed';
          notice('error', data.message || 'The confirmation could not be recorded.');
          refreshAll();
        }
      }).catch(function () {
        busy = false;
        delete uiTask.like;
        uiTask.like = 'failed';
        notice('error', 'Could not reach the unlock server. Please try again.');
        refreshAll();
      });
      return;
    }
    requestReview(true);
  }

  /* ------------------------------------------------ claim the link */
  function claim() {
    if (!verified) {
      notice('error', 'Complete every required step first — the link is still locked.');
      return;
    }
    els.claimBtn.disabled = true;
    request(API + '/claim', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({})
    }).then(function (data) {
      if (data.ok && data.url) {
        if (els.claimLabel) els.claimLabel.textContent = 'OPENING YOUR LINK…';
        window.location.href = data.url;
        return;
      }
      els.claimBtn.disabled = false;
      if (data.error === 'not_verified') {
        notice('error', 'Your session expired before the link was opened. Please verify again.');
        setUnverified();
        if (els.verifyLabel) els.verifyLabel.textContent = 'VERIFY SUBSCRIPTION';
        loadConfig();
      } else if (data.error === 'tasks_incomplete') {
        notice('error', 'The server reports a step is still incomplete — every requirement ' +
          'must be satisfied before the link is released. Complete the remaining step, then try again.');
        setUnverified();
        loadConfig();
      } else {
        notice('error', data.message || 'The link could not be released right now.');
        els.claimBtn.disabled = false;
      }
    }).catch(function () {
      els.claimBtn.disabled = false;
      notice('error', 'Network error while requesting the link. Please try again.');
    });
  }

  /* ------------------------------------------------ nav behaviour */
  function initNav() {
    var nav = $('nav');
    var burger = $('burger');
    var menu = $('mobileMenu');
    if (nav) {
      var onScroll = function () { nav.classList.toggle('scrolled', window.scrollY > 10); };
      window.addEventListener('scroll', onScroll, { passive: true });
      onScroll();
    }
    if (burger && menu) {
      burger.addEventListener('click', function () {
        var open = menu.classList.toggle('open');
        burger.setAttribute('aria-expanded', open ? 'true' : 'false');
      });
      Array.prototype.forEach.call(menu.querySelectorAll('a'), function (a) {
        a.addEventListener('click', function () {
          menu.classList.remove('open');
          burger.setAttribute('aria-expanded', 'false');
        });
      });
    }
  }

  /* ------------------------------------------------ wiring */
  if (els.verifyBtn) {
    els.verifyBtn.addEventListener('click', function () {
      if (verified || busy) return;
      if (MODE === 'oauth') {
        var sub = subscribeTask();
        if (sub) uiTask[sub.id] = 'checking';
        refreshChips();
        window.location.href = API + '/oauth/start';
      } else {
        requestReview();
      }
    });
  }
  if (els.claimBtn) els.claimBtn.addEventListener('click', claim);

  document.addEventListener('visibilitychange', function () {
    if (!document.hidden && !verified) {
      // Coming back from YouTube: re-sync the REAL server state —
      // returning alone proves nothing, but a moderator may have
      // approved the review while the tab was in the background.
      loadConfig();
      var stored = reviewStore('get');
      if (stored) checkReview(stored, true);
    }
  });

  initNav();
  handleUrlResult();
  loadConfig();
})();
