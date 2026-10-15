/* =============================================================
   SPLIX X CHEAT — admin panel controller  (js/admin.js))
   -------------------------------------------------------------
   Talks to the server-side admin API with an Authorization
   bearer token (never stored in a cookie, never sent to any
   third party, kept in sessionStorage for this tab only).
   ============================================================= */
(function () {
  'use strict';

  var TOKEN_KEY = 'splixAdminToken';
  var token = '';
  try { token = sessionStorage.getItem(TOKEN_KEY) || ''; } catch (e) { token = ''; }

  var links = [];
  var reviews = [];
  var events = [];
  var overview = null;
  var editingId = null;

  var $ = function (id) { return document.getElementById(id); };

  /* ------------------------------------------------ helpers */
  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function toast(message) {
    var el = $('toast');
    if (!el) return;
    $('toastText').textContent = message;
    el.classList.add('show');
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { el.classList.remove('show'); }, 3600);
  }

  function api(path, options) {
    options = options || {};
    options.headers = Object.assign({}, options.headers || {});
    if (token) options.headers['Authorization'] = 'Bearer ' + token;
    if (options.body && typeof options.body !== 'string') {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(options.body);
    }
    return fetch(path, options).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        data.__status = res.status;
        if (res.status === 401) {
          signOut();
          throw new Error(data.message || 'Invalid admin token.');
        }
        return data;
      });
    });
  }

  function showGate(message) {
    $('gate').hidden = false;
    $('panel').hidden = true;
    if (message) $('gateNote').innerHTML = message;
  }

  function showPanel() {
    $('gate').hidden = true;
    $('panel').hidden = false;
  }

  function signOut() {
    try { sessionStorage.removeItem(TOKEN_KEY); } catch (e) {}
    token = '';
    showGate('Your token was rejected. Paste the current <code class="adm-code">ADMIN_TOKEN</code> again.');
  }

  function fmtDate(iso) {
    if (!iso) return '—';
    return String(iso).replace('T', ' ').replace(/\.0+Z$/, 'Z').replace('Z', ' UTC');
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); resolve(); }
      catch (err) { reject(err); }
      finally { document.body.removeChild(ta); }
    });
  }

  /* ------------------------------------------------ loading */
  function refresh() {
    return Promise.all([
      api('/api/admin/overview'),
      api('/api/admin/links'),
      api('/api/admin/reviews'),
      api('/api/admin/settings'),
      api('/api/admin/events')
    ]).then(function (results) {
      overview = results[0];
      links = (results[1].links || []);
      reviews = (results[2].reviews || []);
      events = (results[4].events || []);
      renderOverview();
      renderLinks();
      renderReviews();
      renderEvents();
      fillSettings(results[3].settings || {});
    }).catch(function (err) {
      if (err && err.message && String(err.message).indexOf('Invalid') === 0) return;
      toast(err && err.message ? err.message : 'Could not load data from the server.');
    });
  }

  /* ------------------------------------------------ overview */
  function renderOverview() {
    if (!overview) return;
    var v = overview.verification || {};
    var totals = (overview.counts && overview.counts.totals) || {};
    $('statLinks').textContent = overview.counts ? overview.counts.links : links.length;
    $('statPending').textContent = overview.counts ? overview.counts.pending : 0;
    $('statSessions').textContent = overview.counts ? overview.counts.sessions : 0;
    $('statVisits').textContent = totals.visits || 0;
    $('statVerified').textContent = totals.verified || 0;
    $('statUnlocks').textContent = totals.unlocks || 0;
    $('statMode').textContent = v.mode === 'oauth' ? 'YOUTUBE API' : 'MANUAL';

    var banner = $('modeBanner');
    var html = '';

    if (v.mode === 'oauth') {
      html += '<span class="adm-banner-title">Verification</span>' +
        '<i class="fa-brands fa-youtube"></i>' +
        '<span>Automatic verification <b style="color:#3ddc97">active</b> — subscriptions are checked with the official YouTube Data API for channel ' +
        '<code class="adm-code">' + esc(v.channelId || '') + '</code>.</span>';
    } else {
      html += '<span class="adm-banner-title">Verification</span>' +
        '<i class="fa-solid fa-triangle-exclamation"></i>' +
        '<span>Automatic verification <b style="color:var(--ember)">not configured</b>. ' +
        'Missing: ' + (v.missing || []).map(function (m) {
          return '<code class="adm-code">' + esc(m) + '</code>';
        }).join(' ') +
        ' — until then each unlock requires your manual approval on the <b>Manual reviews</b> tab. ' +
        'Setup guide: <code class="adm-code">server/README.md</code></span>';
      if (v.redirectUri) {
        html += '<div class="adm-block" style="flex-basis:100%">Register this exact Redirect URI in Google Cloud → OAuth client:\n' +
          esc(v.redirectUri) + '</div>';
      }
    }

    if (overview.discord && overview.discord.configured) {
      html += '<span class="chip chip--on"><i class="fa-brands fa-discord"></i> Discord webhook ready</span>';
    } else {
      html += '<span class="chip chip--off">Discord webhook not set (optional)</span>';
    }

    banner.innerHTML = html;
    banner.hidden = false;
  }

  /* ------------------------------------------------ links */
  function renderLinks() {
    var rows = $('linkRows');
    var canPublish = overview && overview.discord && overview.discord.configured;
    $('linksEmpty').hidden = links.length > 0;
    $('linkHint').textContent = links.length + ' link(s) stored on the server';

    rows.innerHTML = links.map(function (link) {
      var status = link.enabled
        ? '<span class="chip chip--on">active</span>'
        : '<span class="chip chip--off">disabled</span>';
      var st = link.stats || {};
      var stats = '<span class="adm-statline" title="visits / verifications / unlocks">' +
        '<i class="fa-solid fa-eye"></i> ' + (st.visits || 0) +
        ' &nbsp;<i class="fa-solid fa-shield-halved"></i> ' + (st.verified || 0) +
        ' &nbsp;<i class="fa-solid fa-lock-open"></i> ' + (st.unlocks || 0) + '</span>';
      var actions =
        '<button class="btn btn-ghost btn-sm" data-act="copy" data-id="' + esc(link.id) + '" type="button"><i class="fa-regular fa-copy"></i> COPY</button>' +
        (canPublish
          ? '<button class="btn btn-ghost btn-sm" data-act="publish" data-id="' + esc(link.id) + '" type="button"><i class="fa-brands fa-discord"></i> PUBLISH</button>'
          : '') +
        '<button class="btn btn-ghost btn-sm" data-act="toggle" data-id="' + esc(link.id) + '" type="button">' +
        (link.enabled ? '<i class="fa-solid fa-pause"></i> DISABLE' : '<i class="fa-solid fa-play"></i> ENABLE') +
        '</button>' +
        '<button class="btn btn-ghost btn-sm" data-act="edit" data-id="' + esc(link.id) + '" type="button"><i class="fa-solid fa-pen"></i> EDIT</button>' +
        '<button class="btn btn-ghost btn-sm" data-act="delete" data-id="' + esc(link.id) + '" type="button"><i class="fa-solid fa-trash"></i></button>';

      return '<tr>' +
        '<td class="adm-title-cell"><b>' + esc(link.title) + '</b>' +
        '<span class="adm-url">' + esc(link.slug) + '</span></td>' +
        '<td><input class="adm-url" readonly value="' + esc(link.unlockUrl) + '" ' +
        'style="width:100%;min-width:210px" onclick="this.select()"></td>' +
        '<td>' + stepSummary(link) + '</td>' +
        '<td>' + stats + '</td>' +
        '<td>' + status + '</td>' +
        '<td><div class="adm-actions">' + actions + '</div></td>' +
        '</tr>';
    }).join('');

    Array.prototype.forEach.call(rows.querySelectorAll('button[data-act]'), function (btn) {
      btn.addEventListener('click', function () {
        var link = links.filter(function (l) { return l.id === btn.getAttribute('data-id'); })[0];
        if (!link) return;
        var act = btn.getAttribute('data-act');
        if (act === 'copy') {
          copyText(link.unlockUrl).then(function () { toast('Unlock URL copied to clipboard.'); })
            .catch(function () { toast('Could not copy — select the URL manually.'); });
        } else if (act === 'edit') {
          openForm(link);
        } else if (act === 'toggle') {
          api('/api/admin/links/' + link.id, { method: 'PUT', body: { enabled: !link.enabled } })
            .then(function (data) {
              if (!data.ok) { toast(data.message || 'Could not change the status.'); return; }
              toast(link.enabled ? 'Link disabled — the unlock page is offline.' : 'Link enabled.');
              refresh();
            }).catch(function () { toast('Network error.'); });
        } else if (act === 'delete') {
          if (window.confirm('Delete "' + link.title + '"?\n\nThe unlock page stops working immediately.')) {
            api('/api/admin/links/' + link.id, { method: 'DELETE' }).then(function (data) {
              if (!data.ok) { toast(data.message || 'Delete failed.'); return; }
              toast('Link deleted.');
              refresh();
            }).catch(function () { toast('Network error while deleting.'); });
          }
        } else if (act === 'publish') {
          btn.disabled = true;
          api('/api/admin/publish', { method: 'POST', body: { id: link.id } })
            .then(function (data) {
              btn.disabled = false;
              if (data.ok) toast('Unlock link posted to Discord.');
              else toast(data.message || 'Discord publish failed.');
            })
            .catch(function () { btn.disabled = false; toast('Network error while publishing.'); });
        }
      });
    });
  }

  /* Human summary of a link's configured tasks (never the destination). */
  function stepSummary(link) {
    if ((link.actionType || 'youtube_subscribe') === 'custom') {
      return '<span class="chip chip--on">custom</span> ' + esc(link.actionLabel || '');
    }
    var bits = [
      '<span class="chip chip--on"><i class="fa-brands fa-youtube"></i> subscribe</span>'
    ];
    if (link.videoUrl) {
      bits.push((link.likeMethod === 'honor'
        ? '<span class="chip chip--pending"><i class="fa-solid fa-heart"></i> like · honor</span>'
        : '<span class="chip chip--on"><i class="fa-solid fa-heart"></i> like · review</span>'));
    }
    return bits.join(' ');
  }

  /* ------------------------------------------------ link form */
  function openForm(link) {
    editingId = link ? link.id : null;
    $('linkFormWrap').hidden = false;
    $('formTitle').textContent = link ? 'Edit unlock link' : 'New unlock link';
    $('formNote').textContent = '';
    $('fTitle').value = link ? link.title : 'Complete Steps to Get Link';
    $('fSlug').value = link ? link.slug : '';
    $('fDestination').value = link ? link.destination : '';
    $('fActionLabel').value = link ? link.actionLabel : 'Subscribe Channel';
    $('fActionUrl').value = link ? link.actionUrl : '';
    $('fChannelUrl').value = link ? (link.channelUrl || '') : '';
    $('fVideoUrl').value = link ? (link.videoUrl || '') : '';
    $('fLikeMethod').value = link ? (link.likeMethod || 'manual') : 'manual';
    $('fDescription').value = link ? link.description : 'COMPLETE ALL THE STEPS BELOW';
    $('fType').value = link ? link.actionType : 'youtube_subscribe';
    $('fEnabled').checked = link ? !!link.enabled : true;
    $('linkFormWrap').scrollIntoView({ behavior: 'smooth', block: 'start' });
    $('fTitle').focus();
  }

  function closeForm() {
    editingId = null;
    $('linkFormWrap').hidden = true;
    $('linkForm').reset();
  }

  $('newLinkBtn').addEventListener('click', function () { openForm(null); });
  $('cancelLinkBtn').addEventListener('click', closeForm);

  $('linkForm').addEventListener('submit', function (event) {
    event.preventDefault();
    var payload = {
      title: $('fTitle').value.trim(),
      slug: $('fSlug').value.trim(),
      destination: $('fDestination').value.trim(),
      actionLabel: $('fActionLabel').value.trim() || 'Subscribe Channel',
      actionUrl: $('fActionUrl').value.trim(),
      channelUrl: $('fChannelUrl').value.trim(),
      videoUrl: $('fVideoUrl').value.trim(),
      likeMethod: $('fLikeMethod').value,
      description: $('fDescription').value.trim(),
      actionType: $('fType').value,
      enabled: $('fEnabled').checked
    };

    if (!payload.title) { $('formNote').textContent = 'Title is required.'; return; }
    if (!payload.destination) { $('formNote').textContent = 'Destination URL is required.'; return; }
    if (!payload.actionUrl) { $('formNote').textContent = 'Step URL is required.'; return; }
    if (payload.videoUrl && payload.actionType !== 'youtube_subscribe') {
      $('formNote').textContent = 'The Like task is only available for YouTube subscription links.';
      return;
    }

    var url = editingId ? '/api/admin/links/' + editingId : '/api/admin/links';
    var method = editingId ? 'PUT' : 'POST';

    api(url, { method: method, body: payload }).then(function (data) {
      if (!data.ok) { $('formNote').textContent = data.message || 'Save failed.'; return; }
      toast(editingId ? 'Link updated.' : 'Link created — copy its unlock URL.');
      closeForm();
      refresh();
    }).catch(function (err) {
      $('formNote').textContent = err && err.message ? err.message : 'Network error.';
    });
  });

  /* ------------------------------------------------ reviews */
  function renderReviews() {
    var rows = $('reviewRows');
    $('reviewsEmpty').hidden = reviews.length > 0;

    function linkTitle(slug) {
      var match = links.filter(function (l) { return l.slug === slug; })[0];
      return match ? match.title : slug;
    }

    rows.innerHTML = reviews.map(function (review) {
      var state = review.state || 'pending';
      var chipClass = state === 'approved' ? 'chip--ok'
        : state === 'rejected' ? 'chip--bad'
        : state === 'pending' ? 'chip--pending' : 'chip--off';
      var actions = state === 'pending'
        ? '<button class="btn btn-primary btn-sm" data-act="approve" data-id="' + esc(review.id) + '" type="button">APPROVE</button>' +
          '<button class="btn btn-ghost btn-sm" data-act="reject" data-id="' + esc(review.id) + '" type="button">REJECT</button>'
        : '<span class="adm-url">—</span>';

      return '<tr>' +
        '<td><code class="adm-code">' + esc(review.id) + '</code></td>' +
        '<td>' + esc(linkTitle(review.slug)) + '<span class="adm-url">/u/' + esc(review.slug) + '</span></td>' +
        '<td>' + esc(fmtDate(review.created)) + '</td>' +
        '<td><span class="chip ' + chipClass + '">' + esc(state) + '</span></td>' +
        '<td><div class="adm-actions">' + actions + '</div></td>' +
        '</tr>';
    }).join('');

    Array.prototype.forEach.call(rows.querySelectorAll('button[data-act]'), function (btn) {
      btn.addEventListener('click', function () {
        var act = btn.getAttribute('data-act');
        var id = btn.getAttribute('data-id');
        var action = act === 'approve' ? 'approve' : 'reject';

        if (act === 'approve' &&
            !window.confirm('Approve this request?\n\nOnly do this after you have personally confirmed that the user is subscribed to the channel. Approving unlocks their destination link.')) {
          return;
        }

        btn.disabled = true;
        api('/api/admin/reviews/' + id + '/' + action, { method: 'POST', body: {} })
          .then(function (data) {
            if (!data.ok) { btn.disabled = false; toast(data.message || 'Action failed.'); return; }
            toast(act === 'approve' ? 'Approved — the user\'s link is now unlocked.' : 'Request rejected.');
            refresh();
          })
          .catch(function () { btn.disabled = false; toast('Network error.'); });
      });
    });
  }

  /* ------------------------------------------------ events */
  var EVENT_ICONS = {
    'link.created': 'fa-solid fa-plus', 'link.updated': 'fa-solid fa-pen',
    'link.deleted': 'fa-solid fa-trash', 'link.published': 'fa-brands fa-discord',
    'publish.failed': 'fa-solid fa-triangle-exclamation',
    'review.approved': 'fa-solid fa-check', 'review.rejected': 'fa-solid fa-xmark',
    'task.confirmed': 'fa-solid fa-heart',
    'verify.ok': 'fa-solid fa-shield-halved', 'verify.failed': 'fa-solid fa-triangle-exclamation',
    'unlock.claimed': 'fa-solid fa-lock-open', 'settings.updated': 'fa-solid fa-gear'
  };

  function renderEvents() {
    var rows = $('eventRows');
    if (!rows) return;
    $('eventsEmpty').hidden = events.length > 0;

    function linkTitle(slug) {
      var match = links.filter(function (l) { return l.slug === slug; })[0];
      return match ? match.title : (slug || '—');
    }

    rows.innerHTML = events.map(function (ev) {
      var icon = EVENT_ICONS[ev.event] || 'fa-solid fa-circle-info';
      return '<tr>' +
        '<td class="adm-url">' + esc(fmtDate(ev.ts)) + '</td>' +
        '<td><i class="' + icon + '" style="margin-right:8px;"></i>' + esc(ev.event) + '</td>' +
        '<td>' + esc(linkTitle(ev.slug)) + '</td>' +
        '</tr>';
    }).join('');
  }

  /* ------------------------------------------------ settings */
  function fillSettings(settings) {
    $('sSiteName').value = settings.siteName || '';
    $('sDescription').value = settings.description || '';
    $('sLocation').value = settings.location || '';
    $('sEmail').value = settings.email || '';
    $('sPhone').value = settings.phone || '';
    $('sDisclaimer').value = settings.disclaimer || '';
    $('sChannel').value = settings.youtubeChannelId || '';
    var socials = settings.socials || {};
    $('sYoutube').value = socials.youtube || '';
    $('sDiscord').value = socials.discord || '';
    $('sTiktok').value = socials.tiktok || '';
    $('sX').value = socials.x || '';
    $('sFacebook').value = socials.facebook || '';
    $('sInstagram').value = socials.instagram || '';
    $('sLinkedin').value = socials.linkedin || '';
  }

  $('settingsForm').addEventListener('submit', function (event) {
    event.preventDefault();
    var payload = {
      siteName: $('sSiteName').value.trim(),
      description: $('sDescription').value.trim(),
      location: $('sLocation').value.trim(),
      email: $('sEmail').value.trim(),
      phone: $('sPhone').value.trim(),
      disclaimer: $('sDisclaimer').value.trim(),
      youtubeChannelId: $('sChannel').value.trim(),
      socials: {
        youtube: $('sYoutube').value.trim(),
        discord: $('sDiscord').value.trim(),
        tiktok: $('sTiktok').value.trim(),
        x: $('sX').value.trim(),
        facebook: $('sFacebook').value.trim(),
        instagram: $('sInstagram').value.trim(),
        linkedin: $('sLinkedin').value.trim()
      }
    };

    api('/api/admin/settings', { method: 'PUT', body: payload }).then(function (data) {
      if (!data.ok) { $('settingsNote').textContent = data.message || 'Save failed.'; return; }
      $('settingsNote').textContent = '';
      toast('Settings saved.');
      refresh(); // verification mode may have changed with the channel ID
    }).catch(function (err) {
      $('settingsNote').textContent = err && err.message ? err.message : 'Network error.';
    });
  });

  /* ------------------------------------------------ tabs */
  Array.prototype.forEach.call(document.querySelectorAll('.adm-tab'), function (tab) {
    tab.addEventListener('click', function () {
      Array.prototype.forEach.call(document.querySelectorAll('.adm-tab'), function (t) {
        t.classList.toggle('active', t === tab);
      });
      ['links', 'reviews', 'activity', 'settings'].forEach(function (name) {
        $('tab-' + name).hidden = (name !== tab.getAttribute('data-tab'));
      });
    });
  });

  /* ------------------------------------------------ gate */
  $('gateBtn').addEventListener('click', function () {
    var value = $('adminToken').value.trim();
    if (!value) { $('gateNote').textContent = 'Paste your admin token first.'; return; }
    token = value;
    $('gateNote').textContent = 'Checking…';
    api('/api/admin/overview').then(function (data) {
      if (!data.ok) {
        $('gateNote').textContent = data.message || 'Access denied.';
        token = '';
        return;
      }
      try { sessionStorage.setItem(TOKEN_KEY, value); } catch (e) {}
      $('gateNote').textContent = '';
      showPanel();
      refresh();
    }).catch(function (err) {
      token = '';
      if (err && err.message && err.message.indexOf('Invalid') === 0) return; // signOut handled it
      $('gateNote').textContent = (err && err.message) || 'Could not reach the server.';
    });
  });

  $('adminToken').addEventListener('keydown', function (event) {
    if (event.key === 'Enter') $('gateBtn').click();
  });

  /* ------------------------------------------------ nav + boot */
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

  initNav();
  if (token) {
    api('/api/admin/overview').then(function (data) {
      if (data.ok) { showPanel(); refresh(); }
    }).catch(function () { /* gate remains visible */ });
  }
})();
