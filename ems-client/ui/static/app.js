// EMS Local Dashboard — WebSocket + Pull-to-Refresh
(function() {
  'use strict';

  let ws = null;
  let reconnectTimer = null;

  function connect() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${proto}//${location.host}/ws`);

    ws.onopen = function() {
      console.log('WS connected');
      if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    };

    ws.onmessage = function(evt) {
      try {
        const state = JSON.parse(evt.data);
        updateUI(state);
      } catch(e) { console.error('WS parse error:', e); }
    };

    ws.onclose = function() {
      console.log('WS disconnected — reconnect in 3s');
      reconnectTimer = setTimeout(connect, 3000);
    };

    ws.onerror = function() { ws.close(); };
  }

  function fmt(w) {
    const abs = Math.abs(w);
    if (abs >= 1000) return (abs / 1000).toFixed(1) + ' kW';
    return Math.round(abs) + ' W';
  }

  function updateUI(state) {
    document.querySelectorAll('[data-bind]').forEach(function(el) {
      const key = el.dataset.bind;
      const val = key.split('.').reduce(function(o, k) { return o && o[k]; }, state);
      if (val !== undefined) el.textContent = typeof val === 'number' ? fmt(val) : val;
    });

    (state.loadpoints || []).forEach(function(lp) {
      document.querySelectorAll('[data-lp="' + lp.id + '"] [data-mode]').forEach(function(btn) {
        if (btn.dataset.mode === lp.mode) {
          btn.className = btn.className.replace('text-gray-400 hover:text-white hover:bg-white/5', 'bg-white text-black');
        } else {
          btn.className = btn.className.replace('bg-white text-black', 'text-gray-400 hover:text-white hover:bg-white/5');
        }
      });
    });
  }

  // Mode button handler
  window.setMode = function(lpId, mode) {
    fetch('/api/loadpoint/' + lpId + '/mode', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mode: mode})
    }).then(function(r) { return r.json(); })
      .then(function(data) {
        if (data.ok) {
          location.reload();
        } else {
          alert(data.error || 'Fehler');
        }
      });
  };

  window.setTargetSoc = function(lpId, soc) {
    fetch('/api/loadpoint/' + lpId + '/target-soc', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({soc: soc})
    });
  };

  window.toggleBoost = function(lpId, enable) {
    fetch('/api/loadpoint/' + lpId + '/battery-boost', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({enable: enable})
    }).then(function(r) { return r.json(); })
      .then(function(data) { if (data.ok) location.reload(); });
  };

  window.setMaxCurrent = function(lpId, current) {
    fetch('/api/loadpoint/' + lpId + '/current', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({current: current})
    });
  };

  // ── Pull-to-Refresh (Smartphone) ──────────────────────────────────────────
  (function initPullToRefresh() {
    let startY = 0;
    let pulling = false;
    let indicator = null;

    // Indicator erstellen
    indicator = document.createElement('div');
    indicator.id = 'pull-indicator';
    indicator.innerHTML = '↓ Aktualisieren';
    indicator.style.cssText = 'position:fixed;top:-50px;left:50%;transform:translateX(-50%);' +
      'background:#1a2332;color:#8b91a5;padding:8px 20px;border-radius:20px;font-size:12px;' +
      'font-weight:600;z-index:9999;transition:top 0.2s ease;border:1px solid #2e3345;';
    document.body.appendChild(indicator);

    document.addEventListener('touchstart', function(e) {
      if (window.scrollY === 0) {
        startY = e.touches[0].clientY;
        pulling = true;
      }
    }, { passive: true });

    document.addEventListener('touchmove', function(e) {
      if (!pulling) return;
      var dy = e.touches[0].clientY - startY;
      if (dy > 10 && dy < 150) {
        indicator.style.top = Math.min(dy - 50, 20) + 'px';
        if (dy > 80) {
          indicator.innerHTML = '↑ Loslassen zum Aktualisieren';
          indicator.style.color = '#22c55e';
        } else {
          indicator.innerHTML = '↓ Aktualisieren';
          indicator.style.color = '#8b91a5';
        }
      }
    }, { passive: true });

    document.addEventListener('touchend', function() {
      if (!pulling) return;
      pulling = false;
      var wasReady = indicator.style.color === 'rgb(34, 197, 94)';
      indicator.style.top = '-50px';
      indicator.style.color = '#8b91a5';

      if (wasReady) {
        // Seite neu laden
        indicator.innerHTML = '⟳ Laden...';
        indicator.style.top = '10px';
        indicator.style.color = '#3b82f6';
        location.reload();
      }
    }, { passive: true });
  })();

  // Auto-refresh every 10s as fallback
  setInterval(function() {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      fetch('/api/state').then(function(r) { return r.json(); }).then(updateUI);
    }
  }, 10000);

  // Start WebSocket
  connect();
})();
