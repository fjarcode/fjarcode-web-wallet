(function () {
  var navToggle = document.querySelector('[data-nav-toggle]');
  var sideOverlay = document.querySelector('[data-side-overlay]');
  var sideNav = document.getElementById('side-nav');
  var langMenu = document.querySelector('[data-lang-menu]');
  var disconnectTriggers = Array.prototype.slice.call(document.querySelectorAll('[data-disconnect-trigger]'));
  var disconnectModal = document.querySelector('[data-disconnect-modal]');
  var disconnectCancels = Array.prototype.slice.call(document.querySelectorAll('[data-disconnect-cancel]'));
  var homeInfoModal = document.querySelector('[data-home-info-modal]');
  var homeInfoOpeners = Array.prototype.slice.call(document.querySelectorAll('[data-home-info-open]'));
  var homeInfoClosers = Array.prototype.slice.call(document.querySelectorAll('[data-home-info-close]'));
  var mobileMq = window.matchMedia('(max-width: 1200px)');

  function syncModalOpenClass() {
    var hasDisconnectOpen = !!(disconnectModal && !disconnectModal.hasAttribute('hidden'));
    var hasHomeInfoOpen = !!(homeInfoModal && !homeInfoModal.hasAttribute('hidden'));
    if (hasDisconnectOpen || hasHomeInfoOpen) {
      document.body.classList.add('modal-open');
    } else {
      document.body.classList.remove('modal-open');
    }
  }

  function closeDisconnectModal() {
    if (!disconnectModal) {
      return;
    }
    disconnectModal.setAttribute('hidden', '');
    syncModalOpenClass();
  }

  function openDisconnectModal() {
    if (!disconnectModal) {
      return;
    }
    disconnectModal.removeAttribute('hidden');
    syncModalOpenClass();
  }

  function closeHomeInfoModal() {
    if (!homeInfoModal) {
      return;
    }
    homeInfoModal.setAttribute('hidden', '');
    syncModalOpenClass();
  }

  function openHomeInfoModal() {
    if (!homeInfoModal) {
      return;
    }
    homeInfoModal.removeAttribute('hidden');
    syncModalOpenClass();
  }

  document.addEventListener('submit', function (event) {
    var form = event.target;
    if (!form || !(form instanceof HTMLFormElement)) {
      return;
    }
    if (!form.hasAttribute('data-confirm-submit')) {
      return;
    }

    var message = form.getAttribute('data-confirm-message') || 'Are you sure?';
    if (!window.confirm(message)) {
      event.preventDefault();
    }
  });

  function closeNav() {
    document.body.classList.remove('nav-open');
    if (navToggle) {
      navToggle.setAttribute('aria-expanded', 'false');
    }
  }

  function openNav() {
    document.body.classList.add('nav-open');
    if (navToggle) {
      navToggle.setAttribute('aria-expanded', 'true');
    }
  }

  function toggleNav() {
    if (document.body.classList.contains('nav-open')) {
      closeNav();
    } else {
      openNav();
    }
  }

  if (navToggle) {
    navToggle.addEventListener('click', toggleNav);
  }

  if (sideOverlay) {
    sideOverlay.addEventListener('click', closeNav);
  }

  if (sideNav) {
    sideNav.addEventListener('click', function (event) {
      var target = event.target;
      if (!target || !(target instanceof Element)) {
        return;
      }
      if (mobileMq.matches && target.closest('a')) {
        closeNav();
      }
    });
  }

  if (mobileMq && typeof mobileMq.addEventListener === 'function') {
    mobileMq.addEventListener('change', function (ev) {
      if (!ev.matches) {
        closeNav();
      }
    });
  }

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
      closeNav();
      closeDisconnectModal();
      closeHomeInfoModal();
      if (langMenu) {
        langMenu.removeAttribute('open');
      }
    }
  });

  disconnectTriggers.forEach(function (trigger) {
    trigger.addEventListener('click', function () {
      closeNav();
      openDisconnectModal();
    });
  });

  disconnectCancels.forEach(function (cancelEl) {
    cancelEl.addEventListener('click', function () {
      closeDisconnectModal();
    });
  });

  homeInfoOpeners.forEach(function (opener) {
    opener.addEventListener('click', function () {
      openHomeInfoModal();
    });
  });

  homeInfoClosers.forEach(function (closer) {
    closer.addEventListener('click', function () {
      closeHomeInfoModal();
    });
  });

  document.addEventListener('click', function (event) {
    if (!langMenu || !langMenu.hasAttribute('open')) {
      return;
    }
    var target = event.target;
    if (!target || !(target instanceof Element)) {
      return;
    }
    if (!langMenu.contains(target)) {
      langMenu.removeAttribute('open');
    }
  });

  document.addEventListener('click', function (event) {
    var target = event.target;
    if (!target || !(target instanceof Element)) {
      return;
    }

    var copyEl = target.closest('[data-copy-text]');
    if (!copyEl) {
      return;
    }

    var text = copyEl.getAttribute('data-copy-text');
    if (!text) {
      return;
    }

    function flashCopied() {
      copyEl.classList.add('is-copied');
      window.setTimeout(function () {
        copyEl.classList.remove('is-copied');
      }, 900);
    }

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(flashCopied).catch(function () {
        copyEl.classList.add('is-copied');
      });
      return;
    }

    // Fallback for older browsers
    var helper = document.createElement('textarea');
    helper.value = text;
    helper.setAttribute('readonly', '');
    helper.style.position = 'absolute';
    helper.style.left = '-9999px';
    document.body.appendChild(helper);
    helper.select();
    try {
      document.execCommand('copy');
      flashCopied();
    } catch (err) {
      copyEl.classList.add('is-copied');
    }
    document.body.removeChild(helper);
  });

  function getCookie(name) {
    var value = '; ' + document.cookie;
    var parts = value.split('; ' + name + '=');
    if (parts.length === 2) {
      return parts.pop().split(';').shift();
    }
    return '';
  }

  function b64urlToUint8Array(value) {
    var base64 = String(value || '').replace(/-/g, '+').replace(/_/g, '/');
    while (base64.length % 4) {
      base64 += '=';
    }
    var raw = window.atob(base64);
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i += 1) {
      out[i] = raw.charCodeAt(i);
    }
    return out;
  }

  function arrayBufferToB64url(buf) {
    var bytes = new Uint8Array(buf);
    var str = '';
    for (var i = 0; i < bytes.length; i += 1) {
      str += String.fromCharCode(bytes[i]);
    }
    return window.btoa(str).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
  }

  function withCsrfPost(url, payload) {
    var csrfInput = document.querySelector('input[name="csrfmiddlewaretoken"]');
    var csrfToken = (csrfInput && csrfInput.value) ? csrfInput.value : getCookie('csrftoken');
    return window.fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken || ''
      },
      body: JSON.stringify(payload || {})
    });
  }

  function parseJsonResponse(res) {
    var contentType = (res.headers.get('content-type') || '').toLowerCase();
    if (contentType.indexOf('application/json') !== -1) {
      return res.json();
    }
    return res.text().then(function (text) {
      var snippet = String(text || '').trim().slice(0, 120);
      throw new Error('Server did not return JSON: ' + snippet);
    });
  }

  function normalizeCreationOptions(options) {
    options.challenge = b64urlToUint8Array(options.challenge);
    if (options.user && options.user.id) {
      options.user.id = b64urlToUint8Array(options.user.id);
    }
    (options.excludeCredentials || []).forEach(function (item) {
      item.id = b64urlToUint8Array(item.id);
    });
    return options;
  }

  function normalizeRequestOptions(options) {
    options.challenge = b64urlToUint8Array(options.challenge);
    (options.allowCredentials || []).forEach(function (item) {
      item.id = b64urlToUint8Array(item.id);
    });
    return options;
  }

  function serializeRegisterCredential(cred) {
    return {
      id: cred.id,
      rawId: arrayBufferToB64url(cred.rawId),
      type: cred.type,
      response: {
        attestationObject: arrayBufferToB64url(cred.response.attestationObject),
        clientDataJSON: arrayBufferToB64url(cred.response.clientDataJSON)
      }
    };
  }

  function serializeAuthCredential(cred) {
    return {
      id: cred.id,
      rawId: arrayBufferToB64url(cred.rawId),
      type: cred.type,
      response: {
        authenticatorData: arrayBufferToB64url(cred.response.authenticatorData),
        clientDataJSON: arrayBufferToB64url(cred.response.clientDataJSON),
        signature: arrayBufferToB64url(cred.response.signature),
        userHandle: cred.response.userHandle ? arrayBufferToB64url(cred.response.userHandle) : null
      }
    };
  }

  var passkeyRegisterBtn = document.querySelector('[data-passkey-register]');
  var passkeyRegisterStatus = document.querySelector('[data-passkey-register-status]');
  if (passkeyRegisterBtn && window.PublicKeyCredential) {
    passkeyRegisterBtn.addEventListener('click', function () {
      var beginUrl = passkeyRegisterBtn.getAttribute('data-passkey-begin-url') || '';
      var finishUrl = passkeyRegisterBtn.getAttribute('data-passkey-finish-url') || '';
      if (!beginUrl || !finishUrl) {
        return;
      }
      if (passkeyRegisterStatus) {
        passkeyRegisterStatus.textContent = 'Preparing passkey...';
      }

      withCsrfPost(beginUrl, {})
        .then(parseJsonResponse)
        .then(function (options) {
          return window.navigator.credentials.create({ publicKey: normalizeCreationOptions(options) });
        })
        .then(function (cred) {
          return withCsrfPost(finishUrl, { credential: serializeRegisterCredential(cred) });
        })
        .then(parseJsonResponse)
        .then(function (result) {
          if (!result.ok) {
            throw new Error(result.error || 'Passkey registration failed');
          }
          if (passkeyRegisterStatus) {
            passkeyRegisterStatus.textContent = 'Passkey enabled for this device.';
          }
          window.setTimeout(function () {
            window.location.reload();
          }, 650);
        })
        .catch(function (err) {
          if (passkeyRegisterStatus) {
            passkeyRegisterStatus.textContent = String(err && err.message ? err.message : err);
          }
        });
    });
  }

  var passkeyAuthBtn = document.querySelector('[data-passkey-auth]');
  var passkeyAuthStatus = document.querySelector('[data-passkey-auth-status]');
  if (passkeyAuthBtn && window.PublicKeyCredential) {
    passkeyAuthBtn.addEventListener('click', function () {
      var beginUrl = passkeyAuthBtn.getAttribute('data-passkey-begin-url') || '';
      var finishUrl = passkeyAuthBtn.getAttribute('data-passkey-finish-url') || '';
      var nextTarget = passkeyAuthBtn.getAttribute('data-next-target') || 'wallet';
      var nextTab = passkeyAuthBtn.getAttribute('data-next-tab') || 'send';
      if (!beginUrl || !finishUrl) {
        return;
      }
      if (passkeyAuthStatus) {
        passkeyAuthStatus.textContent = 'Waiting for Face ID / Touch ID...';
      }

      withCsrfPost(beginUrl, {})
        .then(parseJsonResponse)
        .then(function (options) {
          return window.navigator.credentials.get({ publicKey: normalizeRequestOptions(options) });
        })
        .then(function (cred) {
          return withCsrfPost(finishUrl, {
            credential: serializeAuthCredential(cred),
            next_target: nextTarget,
            tab: nextTab
          });
        })
        .then(parseJsonResponse)
        .then(function (result) {
          if (!result.ok) {
            throw new Error(result.error || 'Passkey unlock failed');
          }
          window.location.href = result.redirect_url;
        })
        .catch(function (err) {
          if (passkeyAuthStatus) {
            passkeyAuthStatus.textContent = String(err && err.message ? err.message : err);
          }
        });
    });
  }

  var feeRadios = Array.prototype.slice.call(document.querySelectorAll('input[name="fee_level"]'));
  var feeEstimateEl = document.querySelector('[data-fee-estimate]');
  var maxAmountBtn = document.querySelector('[data-max-amount-btn]');
  var amountInput = document.querySelector('[data-amount-input]');
  var sourceAddressSelect = document.querySelector('[data-source-address-select]');

  function formatFjarFromSats(sats) {
    var value = Number(sats || 0) / 100000000;
    if (!Number.isFinite(value)) {
      return '0';
    }
    var fixed = value.toFixed(8);
    return fixed.replace(/\.0+$/, '').replace(/(\.\d*?)0+$/, '$1');
  }

  function parseFjarToSats(value) {
    var text = String(value || '').trim();
    if (!text) {
      return 0;
    }
    if (!/^\d+(\.\d{0,8})?$/.test(text)) {
      return 0;
    }

    var parts = text.split('.');
    var whole = parts[0] || '0';
    var frac = (parts[1] || '').padEnd(8, '0').slice(0, 8);
    var wholeNum = Number(whole);
    var fracNum = Number(frac);

    if (!Number.isFinite(wholeNum) || !Number.isFinite(fracNum)) {
      return 0;
    }

    return (wholeNum * 100000000) + fracNum;
  }

  function getSelectedFeeSats() {
    for (var i = 0; i < feeRadios.length; i += 1) {
      var radio = feeRadios[i];
      if (!radio.checked) {
        continue;
      }
      var option = radio.closest('[data-fee-option]');
      if (!option) {
        continue;
      }
      var sats = Number(option.getAttribute('data-fee-sats') || 0);
      if (Number.isFinite(sats) && sats > 0) {
        return Math.floor(sats);
      }
    }
    return 0;
  }

  function getMaxSafetyReserveSats(feeSats) {
    // Keep reserve for fee variance + dust floor so change does not become a dust output.
    return Math.max(feeSats + 800, 1200);
  }

  function updateFeeUi() {
    feeRadios.forEach(function (radio) {
      var option = radio.closest('[data-fee-option]');
      if (!option) {
        return;
      }
      option.classList.toggle('is-active', radio.checked);
      if (radio.checked && feeEstimateEl) {
        feeEstimateEl.textContent = formatFjarFromSats(option.getAttribute('data-fee-sats'));
      }
    });
  }

  feeRadios.forEach(function (radio) {
    radio.addEventListener('change', updateFeeUi);
  });

  updateFeeUi();

  if (maxAmountBtn && amountInput) {
    function syncMaxAmountFromSource() {
      if (!sourceAddressSelect) {
        return;
      }
      var selectedOption = sourceAddressSelect.options[sourceAddressSelect.selectedIndex];
      var selectedSpendable = selectedOption ? (selectedOption.getAttribute('data-spendable-fjar') || '') : '';
      var autoMax = maxAmountBtn.getAttribute('data-max-amount-auto') || '';
      var grossMax = selectedSpendable || autoMax;
      var grossSats = parseFjarToSats(grossMax);
      var feeSats = getSelectedFeeSats();
      var reserveSats = getMaxSafetyReserveSats(feeSats);
      var netSats = Math.max(grossSats - reserveSats, 0);
      maxAmountBtn.setAttribute('data-max-amount', formatFjarFromSats(netSats));
    }

    if (sourceAddressSelect) {
      sourceAddressSelect.addEventListener('change', syncMaxAmountFromSource);
      syncMaxAmountFromSource();
    }

    feeRadios.forEach(function (radio) {
      radio.addEventListener('change', syncMaxAmountFromSource);
    });

    maxAmountBtn.addEventListener('click', function () {
      var maxAmount = maxAmountBtn.getAttribute('data-max-amount') || '';
      if (!maxAmount) {
        return;
      }
      amountInput.value = maxAmount;
      amountInput.dispatchEvent(new Event('input', { bubbles: true }));
      amountInput.focus();
    });
  }

  var passwordToggles = Array.prototype.slice.call(document.querySelectorAll('[data-password-toggle]'));
  passwordToggles.forEach(function (toggleBtn) {
    toggleBtn.addEventListener('click', function () {
      var targetSelector = toggleBtn.getAttribute('data-password-target') || '';
      var showLabel = toggleBtn.getAttribute('data-label-show') || 'Show password';
      var hideLabel = toggleBtn.getAttribute('data-label-hide') || 'Hide password';
      if (!targetSelector) {
        return;
      }

      var input = document.querySelector(targetSelector);
      if (!input) {
        return;
      }

      var isVisible = input.getAttribute('type') === 'text';
      input.setAttribute('type', isVisible ? 'password' : 'text');
      toggleBtn.classList.toggle('is-visible', !isVisible);
      if (!isVisible) {
        toggleBtn.setAttribute('aria-label', hideLabel);
      } else {
        toggleBtn.setAttribute('aria-label', showLabel);
      }
    });
  });

  var electrumStatusEl = document.querySelector('.topbar-conn-state');
  if (electrumStatusEl) {
    var statusPollingBusy = false;
    var currentLang = (new URLSearchParams(window.location.search)).get('lang') || 'en';

    function pollElectrumStatus() {
      if (statusPollingBusy) {
        return;
      }
      statusPollingBusy = true;
      window.fetch('/api/electrum-connected/?lang=' + encodeURIComponent(currentLang), { cache: 'no-store' })
        .then(parseJsonResponse)
        .then(function (result) {
          if (!electrumStatusEl || !result) {
            return;
          }
          electrumStatusEl.textContent = result.label || (result.connected ? 'Connected' : 'Disconnected');
          electrumStatusEl.classList.toggle('is-connected', !!result.connected);
          electrumStatusEl.classList.toggle('is-disconnected', !result.connected);
        })
        .catch(function () {
          if (!electrumStatusEl) {
            return;
          }
          electrumStatusEl.classList.remove('is-connected');
          electrumStatusEl.classList.add('is-disconnected');
        })
        .finally(function () {
          statusPollingBusy = false;
        });
    }

    pollElectrumStatus();
    window.setInterval(pollElectrumStatus, 1000);
  }

  function isWalletPage() {
    return window.location.pathname.indexOf('/wallet') === 0;
  }

  function currentWalletTab() {
    var params = new URLSearchParams(window.location.search);
    return params.get('tab') || 'wallet';
  }

  function replaceNodeContent(selector, sourceDoc) {
    var currentNode = document.querySelector(selector);
    var freshNode = sourceDoc.querySelector(selector);
    if (!currentNode || !freshNode) {
      return;
    }
    currentNode.innerHTML = freshNode.innerHTML;
  }

  if (isWalletPage()) {
    var walletRefreshBusy = false;

    function refreshWalletLiveSections() {
      var tab = currentWalletTab();
      if (tab !== 'wallet' && tab !== 'transactions') {
        return;
      }
      if (walletRefreshBusy) {
        return;
      }

      walletRefreshBusy = true;
      window.fetch(window.location.pathname + window.location.search, { cache: 'no-store' })
        .then(function (res) { return res.text(); })
        .then(function (html) {
          var parser = new DOMParser();
          var freshDoc = parser.parseFromString(html, 'text/html');
          replaceNodeContent('.side-balance-value', freshDoc);

          if (tab === 'wallet') {
            replaceNodeContent('[data-live-balance-box]', freshDoc);
            replaceNodeContent('[data-live-wallet-recent-wrap]', freshDoc);
          } else if (tab === 'transactions') {
            replaceNodeContent('[data-live-transactions-wrap]', freshDoc);
          }
        })
        .catch(function () {
          // Silent fail: next poll will retry.
        })
        .finally(function () {
          walletRefreshBusy = false;
        });
    }

    window.setInterval(refreshWalletLiveSections, 5000);
  }
})();
