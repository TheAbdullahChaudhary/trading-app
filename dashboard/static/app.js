/* ═══════════════════════════════════════════════
   MEXC AI Trading Bot — Dashboard JS
   ═══════════════════════════════════════════════ */

const socket   = io();
const prevPrices = {};
let pnlHistory   = [];
let signalCounts = { BUY: 0, SELL: 0, HOLD: 0 };
let lastScores   = {};  // sym -> {long, short, conf, signal}

// ══════════════════ CHARTS ══════════════════

const pnlChart = new Chart(document.getElementById('pnlChart').getContext('2d'), {
  type: 'line',
  data: {
    labels: [],
    datasets: [{
      label: 'Cumulative PnL (USDT)',
      data: [],
      borderColor: '#3b82f6',
      backgroundColor: 'rgba(59,130,246,0.08)',
      fill: true, tension: 0.4,
      pointRadius: 3, pointHoverRadius: 6, borderWidth: 2,
    }]
  },
  options: {
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      x: { ticks: { color: '#4b6080', font:{size:10} }, grid: { color: '#1a2a47' } },
      y: { ticks: { color: '#4b6080', font:{size:10} }, grid: { color: '#1a2a47' } }
    }
  }
});

const signalChart = new Chart(document.getElementById('signalChart').getContext('2d'), {
  type: 'doughnut',
  data: {
    labels: ['BUY', 'SELL', 'HOLD'],
    datasets: [{ data: [0,0,0],
      backgroundColor: ['#10b981','#ef4444','#4b6080'],
      borderColor: '#0f1828', borderWidth: 3 }]
  },
  options: {
    responsive: true, maintainAspectRatio: false,
    plugins: {
      legend: { position:'bottom', labels:{ color:'#94a3b8', padding:14, font:{size:11} } }
    }
  }
});

// ══════════════════ SOCKET EVENTS ══════════════════

socket.on('connect',      () => { fetchAll(); });
socket.on('prices',       updatePrices);
socket.on('positions',    renderPositions);
socket.on('stats',        updateStats);
socket.on('bot_status',   updateBotStatus);
socket.on('ai_insights',  (list) => list.forEach(addInsightToFeed));
socket.on('ai_insight',   addInsightToFeed);
socket.on('ai_regime',    updateRegimeBadges);
socket.on('ai_alert',     (d) => showNotif('⚠️ ' + d.content, 'warn'));

socket.on('trade_opened', data => {
  showNotif(`📈 Opened ${data.side} ${data.symbol} @ ${data.entry}`, data.side.toLowerCase());
  fetchAll();
});

socket.on('trade_closed', data => {
  const pnl  = parseFloat(data.pnl);
  const sign = pnl >= 0 ? '+' : '';
  showNotif(`✅ Closed ${data.symbol} [${data.reason}] PnL: ${sign}${pnl.toFixed(2)} USDT`,
            pnl >= 0 ? 'buy' : 'sell');
  const now     = new Date().toLocaleTimeString();
  const lastPnl = pnlHistory.length ? pnlHistory[pnlHistory.length-1] : 0;
  pnlHistory.push(+(lastPnl + pnl).toFixed(4));
  pnlChart.data.labels.push(now);
  pnlChart.data.datasets[0].data = [...pnlHistory];
  if (pnlChart.data.labels.length > 60) { pnlChart.data.labels.shift(); pnlHistory.shift(); }
  pnlChart.update();
  if (data.side) { const k=data.side.toUpperCase(); if (k in signalCounts) signalCounts[k]++; }
  signalChart.data.datasets[0].data = [signalCounts.BUY, signalCounts.SELL, signalCounts.HOLD];
  signalChart.update();
  fetchAll();
});

// ══════════════════ FETCH ══════════════════

function fetchAll() {
  fetch('/api/prices').then(r=>r.json()).then(updatePrices);
  fetch('/api/positions').then(r=>r.json()).then(renderPositions);
  fetch('/api/trades').then(r=>r.json()).then(renderTrades);
  fetch('/api/stats').then(r=>r.json()).then(updateStats);
  fetch('/api/ai/regime').then(r=>r.json()).then(updateRegimeBadges);
  fetch('/api/ai/status').then(r=>r.json()).then(updateAIMode);
  fetch('/api/ai/insights?limit=20').then(r=>r.json()).then(list => {
    const feed = document.getElementById('aiFeed');
    feed.innerHTML = '';
    list.reverse().forEach(addInsightToFeed);
  });
}

// ══════════════════ PRICES ══════════════════

function updatePrices(data) {
  for (const [sym, price] of Object.entries(data)) {
    const el = document.getElementById('tick-' + sym);
    if (!el) continue;
    const prev = prevPrices[sym] || price;
    const pct  = prev ? ((price - prev) / prev * 100).toFixed(2) : '0.00';
    el.textContent = formatPrice(sym, price);
    const chgEl = document.getElementById('chg-' + sym);
    if (chgEl) {
      chgEl.textContent = (pct >= 0 ? '+' : '') + pct + '%';
      chgEl.className   = 't-change ' + (pct >= 0 ? 'up' : 'down');
    }
    el.classList.remove('flash-up','flash-down');
    el.classList.add(price >= prev ? 'flash-up' : 'flash-down');
    setTimeout(() => el.classList.remove('flash-up','flash-down'), 700);
    prevPrices[sym] = price;
  }
}

function formatPrice(sym, price) {
  if (!price) return '—';
  const dp = sym.startsWith('BTC') ? 1 : 4;
  return parseFloat(price).toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

// ══════════════════ POSITIONS ══════════════════

function renderPositions(positions) {
  const tb = document.getElementById('positionsTable');
  document.getElementById('posCountBadge').textContent = positions.length;
  document.getElementById('statOpen').textContent      = positions.length;
  if (!positions.length) {
    tb.innerHTML = '<tr><td colspan="8" class="empty-row">No open positions</td></tr>';
    return;
  }
  tb.innerHTML = positions.map(p => {
    const pnl = p.unrealized_pnl || 0;
    return `<tr>
      <td><strong>${p.symbol}</strong></td>
      <td class="${p.side==='BUY'?'side-buy':'side-sell'}">${p.side}</td>
      <td>${p.volume}</td>
      <td>${fmtNum(p.entry_price)}</td>
      <td class="pnl-neg">${fmtNum(p.stop_loss)}</td>
      <td class="pnl-pos">${fmtNum(p.take_profit)}</td>
      <td class="${pnl>=0?'pnl-pos':'pnl-neg'}">${pnl>=0?'+':''}${fmtNum(pnl)} USDT</td>
      <td>${p.confidence ? (p.confidence*100).toFixed(0)+'%' : '—'}</td>
    </tr>`;
  }).join('');
}

// ══════════════════ TRADES ══════════════════

function renderTrades(trades) {
  const tb = document.getElementById('tradeTable');
  if (!trades.length) {
    tb.innerHTML = '<tr><td colspan="8" class="empty-row">No trades yet</td></tr>';
    return;
  }
  tb.innerHTML = trades.map(t => {
    const pnl = t.pnl != null ? parseFloat(t.pnl) : null;
    const pnlStr = pnl != null
      ? `<span class="${pnl>=0?'pnl-pos':'pnl-neg'}">${pnl>=0?'+':''}${fmtNum(pnl)}</span>`
      : '—';
    const time = t.opened_at ? new Date(t.opened_at).toLocaleString() : '—';
    const reason = (t.reason||'—').substring(0,60) + ((t.reason||'').length>60?'…':'');
    return `<tr>
      <td style="font-size:11px;color:var(--text3)">${time}</td>
      <td><strong>${t.symbol}</strong></td>
      <td class="${t.side==='BUY'?'side-buy':'side-sell'}">${t.side}</td>
      <td>${fmtNum(t.entry)}</td>
      <td>${t.exit ? fmtNum(t.exit) : '—'}</td>
      <td>${pnlStr}</td>
      <td style="font-size:10px;color:var(--text3);max-width:180px" title="${t.reason||''}">${reason}</td>
      <td><span class="${t.status==='open'?'status-open':'status-closed'}">${t.status}</span></td>
    </tr>`;
  }).join('');
  const buys = trades.filter(t=>t.side==='BUY').length;
  const sells = trades.filter(t=>t.side==='SELL').length;
  signalChart.data.datasets[0].data = [buys, sells, 0];
  signalChart.update();
}

// ══════════════════ STATS ══════════════════

function updateStats(s) {
  if (!s) return;
  document.getElementById('statTotal').textContent   = s.total_trades || 0;
  document.getElementById('statWinRate').textContent = (s.win_rate||0) + '%';
  
  const pnl   = s.total_pnl || 0;
  const pnlEl = document.getElementById('statPnl');
  pnlEl.textContent  = (pnl>=0?'+':'') + fmtNum(pnl) + ' USDT';
  pnlEl.className    = 'stat-value ' + (pnl>=0?'green':'red');
  
  const maxWin  = s.max_win || 0;
  const maxLoss = s.max_loss || 0;
  
  document.getElementById('statBest').textContent  = (maxWin > 0 ? '+' : '') + fmtNum(maxWin);
  // Using Math.abs to match user's example style "Worst Trade 4.76" (positive number for loss)
  document.getElementById('statWorst').textContent = fmtNum(Math.abs(maxLoss));
}

// ══════════════════ BOT STATUS ══════════════════

function updateBotStatus(s) {
  const dot  = document.getElementById('pulseDot');
  const text = document.getElementById('botStatusText');
  if (s.running && !s.paused) {
    text.textContent = 'LIVE TRADING'; dot.style.background = '#10b981';
  } else if (s.paused) {
    text.textContent = 'PAUSED';       dot.style.background = '#f59e0b';
  } else {
    text.textContent = 'STOPPED';      dot.style.background = '#ef4444';
  }
}

function control(cmd) {
  fetch(`/api/control/${cmd}`, { method:'POST' })
    .then(r=>r.json()).then(d=>updateBotStatus(d.state));
}

// ══════════════════ AI MODE BADGE ══════════════════

function updateAIMode(data) {
  const el = document.getElementById('aiModeText');
  if (data && data.gemini_active) {
    el.textContent = 'Gemini 2.0';
    el.parentElement.style.borderColor = 'rgba(168,85,247,0.5)';
  } else {
    el.textContent = 'Rule-Based AI';
    el.parentElement.style.borderColor = 'rgba(6,182,212,0.3)';
    el.parentElement.style.background  = 'rgba(6,182,212,0.08)';
    el.parentElement.style.color       = 'var(--cyan)';
  }
}

// ══════════════════ REGIME BADGES ══════════════════

const REGIME_MAP = {
  'TRENDING_UP':   { cls:'regime-up',       label:'▲ TRENDING UP'   },
  'TRENDING_DOWN': { cls:'regime-down',     label:'▼ TRENDING DOWN' },
  'CHOPPY':        { cls:'regime-choppy',   label:'↔ CHOPPY'        },
  'HIGH_VOL':      { cls:'regime-high-vol', label:'⚡ HIGH VOL'     },
  'UNKNOWN':       { cls:'regime-unknown',  label:'··· ?'           },
};
const SYM_SHORT = {
  'BTC_USDT':'BTC','ETH_USDT':'ETH','XAUT_USDT':'GOLD',
  'SILVER_USDT':'SILVER','USOIL_USDT':'OIL'
};

function updateRegimeBadges(regimes) {
  for (const [sym, regime] of Object.entries(regimes)) {
    const el = document.querySelector(`.regime-badge[data-sym="${sym}"]`);
    if (!el) continue;
    const info  = REGIME_MAP[regime] || REGIME_MAP['UNKNOWN'];
    const short = SYM_SHORT[sym] || sym;
    el.className    = 'regime-badge ' + info.cls;
    el.textContent  = short + ' ' + info.label;
  }
}

// ══════════════════ AI INSIGHT FEED ══════════════════

function addInsightToFeed(ins) {
  const feed = document.getElementById('aiFeed');
  const empty = feed.querySelector('.ai-feed-empty');
  if (empty) empty.remove();

  const riskCls = { LOW:'ai-risk-low', MEDIUM:'ai-risk-medium', HIGH:'ai-risk-high' }[ins.risk_level] || '';
  const typeCls = { trade:'type-trade', regime:'type-regime', risk:'type-risk', chat:'type-chat' }[ins.type] || '';
  const typeLabel = (ins.type||'').toUpperCase();
  const sym = ins.symbol || '';
  const content = (ins.content||'').replace(/</g,'&lt;').replace(/>/g,'&gt;');

  const el = document.createElement('div');
  el.className = `ai-insight-item ${riskCls}`;
  el.innerHTML = `
    <div class="ai-insight-header">
      <span class="ai-insight-sym">${sym}</span>
      <span class="ai-insight-type ${typeCls}">${typeLabel}</span>
      <span class="ai-insight-time">${ins.time_str || ''}</span>
    </div>
    <div class="ai-insight-content">${content}</div>`;

  feed.prepend(el);
  // Keep max 30
  while (feed.children.length > 30) feed.removeChild(feed.lastChild);
}

// ══════════════════ FEATURE IMPORTANCE ══════════════════

function loadFeatureImportance(sym) {
  document.getElementById('featureSymLabel').textContent = sym;
  fetch(`/api/ai/features/${sym}`)
    .then(r=>r.json())
    .then(renderFeatureImportance);
}

function renderFeatureImportance(features) {
  const el = document.getElementById('aiFeatures');
  if (!features || !features.length) {
    el.innerHTML = '<div class="ai-feed-empty">No feature data yet — model needs training first</div>';
    return;
  }
  const max = Math.max(...features.map(f=>f.score), 1);
  el.innerHTML = features.map(f => {
    const pct = Math.round((f.score / max) * 100);
    return `<div class="feat-row">
      <span class="feat-name">${f.name}</span>
      <div class="feat-bar-track"><div class="feat-bar-fill" style="width:${pct}%"></div></div>
      <span class="feat-score">${f.score.toFixed(0)}</span>
    </div>`;
  }).join('');
}

// ══════════════════ SIGNAL SCORE GAUGE ══════════════════

function updateGaugeDisplay() {
  const sym  = document.getElementById('scoreSym').value;
  const data = lastScores[sym] || {};
  const score = data.score || 0;
  const maxS  = 12;

  // Draw arc gauge
  const canvas = document.getElementById('scoreGauge');
  const ctx    = canvas.getContext('2d');
  const cx=80, cy=85, r=65, start=Math.PI*0.85, end=Math.PI*2.15;
  ctx.clearRect(0,0,160,100);
  // Background arc
  ctx.beginPath(); ctx.arc(cx,cy,r,start,end); ctx.strokeStyle='#1a2a47'; ctx.lineWidth=12; ctx.stroke();
  // Value arc
  const pct  = score / maxS;
  const fill = start + pct * (end - start);
  const color = score>=8 ? '#10b981' : score>=5 ? '#f59e0b' : '#4b6080';
  if (pct > 0) {
    ctx.beginPath(); ctx.arc(cx,cy,r,start,fill); ctx.strokeStyle=color; ctx.lineWidth=12;
    ctx.lineCap='round'; ctx.stroke();
  }
  // Score text
  ctx.fillStyle = color; ctx.font='bold 22px Inter'; ctx.textAlign='center';
  ctx.fillText(score, cx, cy-8);
  ctx.fillStyle='#4b6080'; ctx.font='11px Inter';
  ctx.fillText('/12', cx, cy+10);

  document.getElementById('gaugeLabel').textContent = `Score: ${score}/12`;
  document.getElementById('gaugeLabel').style.color  = color;

  // Bars
  const long  = data.long  || 0;
  const short = data.short || 0;
  const conf  = data.conf  || 0;
  document.getElementById('longBar').style.width  = (long/maxS*100) + '%';
  document.getElementById('shortBar').style.width = (short/maxS*100) + '%';
  document.getElementById('confBar').style.width  = (conf*100) + '%';
  document.getElementById('longVal').textContent  = long;
  document.getElementById('shortVal').textContent = short;
  document.getElementById('confVal').textContent  = conf ? (conf*100).toFixed(0)+'%' : '—';
}

// Periodically redraw gauge to keep it live
setInterval(updateGaugeDisplay, 5000);

// ══════════════════ AI CHAT ══════════════════

function sendChat() {
  const input = document.getElementById('chatInput');
  const q     = input.value.trim();
  if (!q) return;
  input.value = '';

  const msgs  = document.getElementById('chatMessages');
  const userEl = document.createElement('div');
  userEl.className = 'chat-bubble user-bubble';
  userEl.textContent = q;
  msgs.appendChild(userEl);

  const thinkEl = document.createElement('div');
  thinkEl.className = 'chat-bubble ai-bubble thinking';
  thinkEl.textContent = 'Analysing market conditions…';
  msgs.appendChild(thinkEl);
  msgs.scrollTop = msgs.scrollHeight;

  document.getElementById('chatSendBtn').disabled = true;

  fetch('/api/ai/ask', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ question: q })
  })
  .then(r=>r.json())
  .then(d => {
    thinkEl.remove();
    const aiEl = document.createElement('div');
    aiEl.className = 'chat-bubble ai-bubble';
    aiEl.textContent = d.answer || 'No response.';
    msgs.appendChild(aiEl);
    msgs.scrollTop = msgs.scrollHeight;
    // Add to insight feed as chat type
    addInsightToFeed({ symbol:'Chat', type:'chat', content: d.answer,
                       risk_level:'LOW', time_str: new Date().toLocaleTimeString() });
  })
  .catch(() => { thinkEl.textContent = 'Error contacting AI. Try again.'; })
  .finally(() => { document.getElementById('chatSendBtn').disabled = false; });
}

// ══════════════════ NOTIFICATIONS ══════════════════

function showNotif(msg, type='info') {
  const area = document.getElementById('notificationArea');
  const el   = document.createElement('div');
  el.className = `notif ${type}`;
  el.innerHTML = `<span>${msg}</span>`;
  area.appendChild(el);
  setTimeout(() => el.remove(), 6000);
}

// ══════════════════ UTILS ══════════════════

function fmtNum(n) {
  if (n==null) return '—';
  return parseFloat(n).toLocaleString('en-US', { minimumFractionDigits:2, maximumFractionDigits:4 });
}

// ══════════════════ INIT & POLL ══════════════════

fetchAll();
setInterval(fetchAll, 2000);

// Initial gauge draw
setTimeout(updateGaugeDisplay, 1000);

// Load feature importance for default symbol on startup
setTimeout(() => loadFeatureImportance('BTC_USDT'), 2000);
