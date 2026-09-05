/* Matlack published viewer. Static: reads site.json, index.json and m/<id>.json,
   decrypting in the browser when the site was published with a passphrase.
   Routes: #/  #/m/<id>  #/m/<id>/summary */
(() => {
'use strict';
const app = document.getElementById('app');
const nav = document.getElementById('nav');
const PASS_KEY = 'matlack.pass';
let site = null, key = null, index = null;
const cache = {};

// --- helpers ---------------------------------------------------------------
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function fmt(ms) {
  if (ms == null) return '--:--';
  const s = Math.floor(ms / 1000), h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), sec = s % 60;
  return (h ? h + ':' + String(m).padStart(2, '0') : String(m).padStart(2, '0')) + ':' + String(sec).padStart(2, '0');
}
function timeAgo(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return 'just now';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  if (s < 86400 * 14) return Math.floor(s / 86400) + 'd ago';
  return new Date(ts * 1000).toLocaleDateString(undefined, {month: 'short', day: 'numeric', year: 'numeric'});
}
const b64 = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));
const mb = n => (n / 1048576).toFixed(n < 10 * 1048576 ? 1 : 0) + ' MB';
const colors = {};
function color(label) {
  if (!colors[label]) colors[label] = `var(--sp-${'ABCDEFGHIJKL'[Object.keys(colors).length % 12]})`;
  return colors[label];
}

// --- crypto ----------------------------------------------------------------
async function deriveKey(pass, saltB64, iter) {
  const base = await crypto.subtle.importKey('raw', new TextEncoder().encode(pass), 'PBKDF2', false, ['deriveKey']);
  return crypto.subtle.deriveKey({name: 'PBKDF2', salt: b64(saltB64), iterations: iter, hash: 'SHA-256'},
    base, {name: 'AES-GCM', length: 256}, false, ['decrypt']);
}
async function open(blob) {
  if (!blob || blob.enc !== 'aes-gcm') return blob;
  const raw = await crypto.subtle.decrypt({name: 'AES-GCM', iv: b64(blob.iv)}, key, b64(blob.data));
  return JSON.parse(new TextDecoder().decode(raw));
}
async function load(path) {
  if (cache[path]) return cache[path];
  const r = await fetch(path, {cache: 'no-cache'});
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  cache[path] = await open(await r.json());
  return cache[path];
}

// --- gate ------------------------------------------------------------------
function savedPass() {
  try { return sessionStorage.getItem(PASS_KEY) || localStorage.getItem(PASS_KEY) || ''; } catch { return ''; }
}
function remember(pass, persist) {
  try { sessionStorage.setItem(PASS_KEY, pass); if (persist) localStorage.setItem(PASS_KEY, pass); } catch {}
}
function forget() {
  try { sessionStorage.removeItem(PASS_KEY); localStorage.removeItem(PASS_KEY); } catch {}
  key = null; index = null; for (const k in cache) delete cache[k];
  location.hash = '#/'; boot();
}
async function tryPass(pass) {
  const k = await deriveKey(pass, site.salt, site.iter || 250000);
  const prev = key; key = k;
  try { const c = await open(site.check); if (!c || c.ok !== true) throw new Error(); return true; }
  catch { key = prev; return false; }
}
function renderGate(bad) {
  nav.innerHTML = '';
  app.innerHTML = `<div class="gate"><div class="gate-card">
    <div class="gate-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg></div>
    <h1>Transcripts</h1>
    <p>These pages are encrypted. Enter the passphrase to read them; nothing is sent anywhere, the unlocking happens in your browser.</p>
    <form id="gateForm">
      <input type="password" id="pass" placeholder="Passphrase" autocomplete="current-password" autofocus>
      <div class="row"><label><input type="checkbox" id="persist"> Remember on this device</label>
        <button class="primary" type="submit">Open</button></div>
      ${bad ? '<p class="bad">That passphrase didn’t work.</p>' : ''}
    </form>
  </div></div>`;
  document.getElementById('gateForm').onsubmit = async ev => {
    ev.preventDefault();
    const pass = document.getElementById('pass').value;
    const btn = ev.target.querySelector('button'); btn.disabled = true; btn.textContent = 'Checking…';
    if (await tryPass(pass)) { remember(pass, document.getElementById('persist').checked); route(); }
    else renderGate(true);
  };
}
function renderNav(extra = '') {
  const lock = site && site.enc ? `<button class="lock" id="lock" title="Forget the passphrase on this device"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0"/></svg>Lock</button>` : '';
  nav.innerHTML = `<a href="#/" ${extra ? '' : 'aria-current="page"'}>Meetings</a>${lock}`;
  const b = document.getElementById('lock'); if (b) b.onclick = forget;
}

// --- views -----------------------------------------------------------------
async function viewList() {
  renderNav();
  index = index || await load('index.json');
  const ms = index.meetings || [];
  const rows = ms.map((m, i) => `<div class="row">
      <div class="cell"><div class="title-cell">
        <span class="avatar" style="--c:var(--sp-${'ABCDEFGHIJKL'[i % 12]})">${esc((m.title || '?')[0].toUpperCase())}</span>
        <div style="min-width:0"><a class="name" href="#/m/${esc(m.id)}">${esc(m.title)}</a>${m.has_summary ? `<a class="chip-sum" href="#/m/${esc(m.id)}/summary" title="Open summary"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16v16H4z"/><path d="M8 9h8M8 13h8M8 17h5"/></svg>Summary</a>` : ''}
          <div class="when">${m.created ? 'Recorded ' + esc(timeAgo(m.created)) : ''}</div></div>
      </div></div>
      <div class="cell who">${(m.speakers || []).slice(0, 4).map(n => `<span class="n">${esc(n)}</span>`).join('')}${m.speakers && m.speakers.length > 4 ? ` <span class="more">+${m.speakers.length - 4} more</span>` : ''}</div>
      <div class="cell len">${m.duration_ms ? fmt(m.duration_ms) : '—'}</div>
      <div class="cell stat"><span class="status ready">${m.has_summary ? 'summarized' : 'transcript'}</span></div>
      <div class="cell actions"></div>
    </div>`).join('');
  app.innerHTML = `<header class="top"><div>
      <div class="h1-row"><h1>Meetings</h1>${ms.length ? `<span class="count">${ms.length}</span>` : ''}</div>
      <p class="lede">Transcripts and summaries that have been reviewed and published. Names were confirmed by hand; summaries follow an interview guide.</p>
    </div></header>
    <main class="wrap">${ms.length ? `<div class="list-head"><h2>Published</h2><span class="sub">Newest first</span></div>
      <div class="list"><div class="cols" aria-hidden="true"><span>Meeting</span><span>Speakers</span><span>Length</span><span>Status</span><span></span></div>${rows}</div>`
      : '<p class="empty"><b>Nothing published yet.</b>Approved meetings will appear here.</p>'}
      ${index.generated ? `<p class="published-note">Last published ${esc(new Date(index.generated * 1000).toLocaleString())}.</p>` : ''}
    </main>`;
}

function meetingHeader(m, page) {
  const has = !!m.summary;
  const crumb = page === 'summary'
    ? `<a class="crumb" href="#/m/${esc(m.id)}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 6l-6 6 6 6"/></svg>Transcript</a>`
    : `<a class="crumb" href="#/"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 6l-6 6 6 6"/></svg>All meetings</a>`;
  const names = Object.keys(m.speakers).sort().map(l => m.speakers[l].name);
  const meta = page === 'summary'
    ? `<span class="item"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16v16H4z"/><path d="M8 9h8M8 13h8M8 17h5"/></svg><b>${esc(m.summary.guide_title || 'Summary')}</b></span>
       ${m.duration_ms ? `<span class="item"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg><b>${fmt(m.duration_ms)}</b></span>` : ''}
       <span class="item"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6"/><path d="M16 5a3 3 0 0 1 0 6M21 20c0-2.6-1.6-4.8-4-5.6"/></svg>${esc(names.join(', '))}</span>
       <span class="item" id="coverage"></span>`
    : `${m.duration_ms ? `<span class="item"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg><b>${fmt(m.duration_ms)}</b></span>` : ''}
       <span class="item"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h16M4 12h16M4 18h10"/></svg><b>${m.utterances.length}</b> lines</span>
       <span class="item"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6"/><path d="M16 5a3 3 0 0 1 0 6M21 20c0-2.6-1.6-4.8-4-5.6"/></svg><b>${names.length}</b> speakers</span>`;
  const action = page === 'summary'
    ? `<a class="btn primary" href="#/m/${esc(m.id)}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h16M4 12h16M4 18h10"/></svg>Transcript</a>`
    : has ? `<a class="btn primary" href="#/m/${esc(m.id)}/summary"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16v16H4z"/><path d="M8 9h8M8 13h8M8 17h5"/></svg>View summary</a>` : '';
  return `<header class="top"><div>${crumb}<div class="title-row"><h1>${esc(m.title)}</h1></div><div class="meta">${meta}</div></div>
    <div class="toolbar"><button class="btn" id="copyBtn" title="Copy as plain text"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg><span>Copy</span></button>${action}</div></header>`;
}

function transcriptText(m) {
  const out = [m.title, ''];
  for (const u of m.utterances) out.push(`[${fmt(u.start)}] ${m.speakers[u.speaker]?.name || 'Speaker ' + u.speaker}: ${u.text}`);
  return out.join('\n');
}
function summaryText(m) {
  const s = m.summary, out = [m.title + ' — ' + (s.guide_title || 'Summary'), ''];
  if (s.overview) out.push('OVERVIEW', '', s.overview, '');
  if (s.priorities?.length) out.push('TOP PRIORITIES', '', ...s.priorities.map((p, i) => `${i + 1}. ${p}`), '');
  for (const sec of s.sections || []) {
    out.push(sec.title.toUpperCase(), '', sec.question, '');
    if (!(sec.covered || sec.summary)) { out.push('Not discussed.', ''); continue; }
    if (sec.summary) out.push(sec.summary, '');
    for (const p of sec.points || []) out.push(`- ${p}`);
    if (sec.points?.length) out.push('');
    for (const q of sec.quotes || []) out.push(`> “${q.text}”${q.speaker ? ' — ' + q.speaker : ''}${q.time ? ` (${q.time})` : ''}`, '');
  }
  if (s.follow_ups?.length) out.push('FOLLOW-UPS', '', ...s.follow_ups.map(p => `- ${p}`), '');
  return out.join('\n');
}
function wireCopy(text) {
  const b = document.getElementById('copyBtn'); if (!b) return;
  b.onclick = async () => {
    try { await navigator.clipboard.writeText(text()); } catch { return; }
    const l = b.querySelector('span'); l.textContent = 'Copied'; b.classList.add('good');
    setTimeout(() => { l.textContent = 'Copy'; b.classList.remove('good'); }, 1400);
  };
}

async function viewMeeting(id, t) {
  renderNav('meeting');
  const m = await load(`m/${encodeURIComponent(id)}.json`);
  Object.keys(m.speakers).sort().forEach(color);
  const nameOf = l => m.speakers[l]?.name || `Speaker ${l}`;
  const audio = m.audio && m.audio.file ? m.audio : null;
  const counts = {}; m.utterances.forEach(u => counts[u.speaker] = (counts[u.speaker] || 0) + 1);
  const key_ = Object.keys(m.speakers).sort().map(l => {
    const sp = m.speakers[l], n = nameOf(l);
    const initial = (n.startsWith('Speaker ') ? l : n[0] || l).toUpperCase();
    return `<div class="sp" style="--c:${color(l)}"><div class="chip">${esc(initial)}</div>
      <div style="min-width:0"><div class="label">Speaker ${l}<span class="lines">${counts[l] || 0} line${counts[l] === 1 ? '' : 's'}</span></div>
      <div class="who ${sp.confirmed ? '' : 'unconfirmed'}" title="${sp.confirmed ? 'Confirmed' : 'Best guess, not confirmed'}">${esc(n)}</div></div></div>`;
  }).join('');
  app.innerHTML = meetingHeader(m, 'transcript') + `<main class="wrap"><div class="layout">
    <details class="key" id="key" open><summary class="key-head"><h2>Who's speaking</h2>
      <svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg></summary>
      <div class="key-body">${key_}</div>
      ${Object.values(m.speakers).some(s => !s.confirmed) ? '<p class="fine key-body">Amber names are best guesses that were not confirmed.</p>' : ''}
    </details>
    <div class="stream-wrap"><div class="stream-head"><h2>Transcript</h2><span class="n" id="lineCount"></span>
      <label class="find"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
        <input type="search" id="find" placeholder="Find in transcript" autocomplete="off"><span class="hits" id="hits"></span></label></div>
      <section class="stream" id="stream"></section></div>
  </div>${audio ? `<div class="player" id="player">
    <span class="ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 10v4M8 6v12M12 9v6M16 4v16M20 10v4"/></svg></span>
    <div class="meta-col"><b>${esc(m.title)}</b><span id="playerHint">${audio.enc ? 'Loads and unlocks in your browser' : 'Click a timestamp to jump'}</span></div>
    ${audio.enc ? `<button class="btn primary load" id="loadAudio"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v12M12 16l-4-4M12 16l4-4"/><path d="M4 18v1a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-1"/></svg><span>Load recording · ${mb(audio.size)}</span></button>` : ''}
    <audio controls preload="none" id="audio" ${audio.enc ? 'hidden' : `src="${esc(audio.file)}"`}></audio>
  </div>` : ''}</main>`;
  wireCopy(() => transcriptText(m));

  // Recording. In the clear it streams straight from the site; encrypted, it
  // is fetched whole, decrypted with the passphrase key and played from memory.
  let audioReady = !!audio && !audio.enc, loading = null;
  const ensureAudio = () => {
    if (!audio) return Promise.resolve(false);
    if (audioReady) return Promise.resolve(true);
    if (loading) return loading;
    const btn = document.getElementById('loadAudio'), label = btn.querySelector('span'), hint = document.getElementById('playerHint');
    btn.disabled = true; btn.classList.add('busy');
    loading = (async () => {
      const r = await fetch(audio.file);
      if (!r.ok) throw new Error(`recording ${r.status}`);
      const total = +r.headers.get('content-length') || audio.size;
      const chunks = []; let got = 0;
      const reader = r.body.getReader();
      for (;;) {
        const {done, value} = await reader.read(); if (done) break;
        chunks.push(value); got += value.length;
        label.textContent = `Loading ${mb(got)} of ${mb(total)}`;
      }
      const buf = new Uint8Array(got); let off = 0;
      for (const c of chunks) { buf.set(c, off); off += c.length; }
      label.textContent = 'Unlocking…';
      const plain = await crypto.subtle.decrypt({name: 'AES-GCM', iv: buf.subarray(0, 12)}, key, buf.subarray(12));
      const el = document.getElementById('audio');
      el.src = URL.createObjectURL(new Blob([plain], {type: audio.type}));
      el.hidden = false; btn.remove(); hint.textContent = 'Click a timestamp to jump';
      audioReady = true;
      return true;
    })().catch(e => {
      loading = null; btn.disabled = false; btn.classList.remove('busy');
      label.textContent = 'Try again'; hint.textContent = `Couldn't load the recording: ${e.message}`;
      return false;
    });
    return loading;
  };
  const seek = async ms => {
    if (!(await ensureAudio())) return;
    const el = document.getElementById('audio');
    el.currentTime = ms / 1000;
    try { await el.play(); } catch { document.getElementById('playerHint').textContent = 'Press play to listen from here'; }
  };
  if (audio && audio.enc) document.getElementById('loadAudio').onclick = () => ensureAudio();
  document.getElementById('stream').addEventListener('click', ev => {
    const a = ev.target.closest('a[data-seek]'); if (!a) return;
    ev.preventDefault(); seek(parseFloat(a.dataset.seek));
  });
  // Follow playback: mark the line being spoken, as the local page does.
  if (audio) {
    const el = document.getElementById('audio'); let cur = null;
    el.addEventListener('timeupdate', () => {
      const ms = el.currentTime * 1000; let next = null;
      for (const r of document.querySelectorAll('.u[data-start]')) { if (parseFloat(r.dataset.start) <= ms) next = r; else break; }
      if (next === cur) return;
      if (cur) cur.classList.remove('playing');
      cur = next; if (cur) cur.classList.add('playing');
    });
  }

  let query = '';
  const highlight = text => {
    if (!query) return esc(text);
    const q = query.toLowerCase(), lower = text.toLowerCase(); let out = '', i = 0, j;
    while ((j = lower.indexOf(q, i)) !== -1) { out += esc(text.slice(i, j)) + '<mark>' + esc(text.slice(j, j + q.length)) + '</mark>'; i = j + q.length; }
    return out + esc(text.slice(i));
  };
  const renderStream = () => {
    const q = query.toLowerCase(); let prev = null, shown = 0;
    const html = m.utterances.map((u, i) => {
      const same = u.speaker === prev && !q; prev = u.speaker;
      const hit = !q || u.text.toLowerCase().includes(q) || nameOf(u.speaker).toLowerCase().includes(q);
      if (hit) shown++;
      return `<div class="u ${same ? 'same' : ''} ${hit ? '' : 'hide'}" style="--c:${color(u.speaker)}" data-i="${i}" data-start="${u.start}">
        <div class="ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="3.2"/><path d="M5 20c0-3.5 3.5-6 7-6s7 2.5 7 6"/></svg></div>
        <div class="t">${audio ? `<a href="#" data-seek="${u.start}" title="Play from here">${fmt(u.start)}</a>` : fmt(u.start)}</div>
        <div class="body"><div class="name"><span>${esc(nameOf(u.speaker))}</span></div><div class="text">${highlight(u.text)}</div></div></div>`;
    }).join('');
    document.getElementById('stream').innerHTML = html + (shown ? '' : `<p class="none">No lines match “${esc(query)}”.</p>`);
    document.getElementById('lineCount').textContent = q ? `${shown} of ${m.utterances.length}` : `${m.utterances.length} lines`;
    document.getElementById('hits').textContent = q ? `${shown}` : '';
  };
  let timer;
  document.getElementById('find').oninput = ev => { clearTimeout(timer); timer = setTimeout(() => { query = ev.target.value.trim(); renderStream(); }, 120); };
  renderStream();
  if (t) {  // #/m/<id>?t=mm:ss from a summary quote
    const parts = t.split(':').map(Number), secs = parts.length === 3 ? parts[0] * 3600 + parts[1] * 60 + parts[2] : parts[0] * 60 + parts[1];
    let target = null;
    for (const r of document.querySelectorAll('.u[data-start]')) { if (parseFloat(r.dataset.start) <= secs * 1000 + 500) target = r; else break; }
    if (target) { target.classList.add('target'); target.scrollIntoView({block: 'center'}); }
  }
}

async function viewSummary(id) {
  renderNav('summary');
  const m = await load(`m/${encodeURIComponent(id)}.json`);
  if (!m.summary) { location.hash = `#/m/${id}`; return; }
  const s = m.summary, secs = s.sections || [], covered = secs.filter(x => x.covered).length;
  const toc = secs.map((x, i) => `<a href="#/m/${esc(id)}/summary?s=${esc(x.id)}" class="${x.covered || x.summary ? '' : 'off'}" data-sec="${esc(x.id)}"><span class="n">${i + 1}</span>${esc(x.title)}</a>`).join('');
  const cards = secs.map((x, i) => {
    const empty = !(x.covered || x.summary);
    return `<section class="card sec ${empty ? 'empty-sec' : ''}" id="s-${esc(x.id)}">
      <div class="sec-head"><span class="num">${i + 1}</span><div><h2 class="sec-title">${esc(x.title)}</h2><p class="q">${esc(x.question)}</p></div>
        ${empty ? '<span class="tag">Not discussed</span>' : !x.covered ? '<span class="tag">Not asked directly</span>' : ''}</div>
      ${empty ? '' : `<div class="prose">${esc(x.summary)}</div>
      ${x.points?.length ? `<ul class="points">${x.points.map(p => `<li>${esc(p)}</li>`).join('')}</ul>` : ''}
      ${x.quotes?.length ? `<div class="quotes">${x.quotes.map(q => `<blockquote><p>“${esc(q.text)}”</p><footer>${esc(q.speaker)}${q.time ? ` <a href="#/m/${esc(id)}?t=${esc(q.time)}" class="t" title="Open in transcript">${esc(q.time)}</a>` : ''}</footer></blockquote>`).join('')}</div>` : ''}`}
    </section>`;
  }).join('');
  const prio = s.priorities?.length ? `<section class="card accent"><h2 class="sec-title">Top priorities</h2><ol class="prio">${s.priorities.map(p => `<li>${esc(p)}</li>`).join('')}</ol></section>` : '';
  const fu = s.follow_ups?.length ? `<section class="card"><h2 class="sec-title">Follow-ups</h2><ul class="points">${s.follow_ups.map(p => `<li>${esc(p)}</li>`).join('')}</ul></section>` : '';
  app.innerHTML = meetingHeader(m, 'summary') + `<main class="wrap"><div class="sum-layout">
    <aside class="toc"><div class="toc-in"><h2>Sections</h2><nav>${toc}</nav></div></aside>
    <div class="sum-body">
      <section class="card lead"><h2 class="sec-title">Overview</h2><div class="prose big">${esc(s.overview)}</div></section>
      ${prio}${cards}${fu}
      <p class="fine">${s.words ? `About ${s.words} words. ` : ''}Generated by ${esc(s.model || 'Claude')}${s.created ? ' on ' + new Date(s.created * 1000).toLocaleDateString(undefined, {month: 'long', day: 'numeric', year: 'numeric'}) : ''}, then reviewed. Summaries can misfile or compress a point; check anything you plan to quote against the transcript.</p>
    </div></div></main>`;
  document.getElementById('coverage').innerHTML = `<b>${covered}</b> of ${secs.length} sections covered`;
  wireCopy(() => summaryText(m));
  document.querySelectorAll('.toc nav a').forEach(a => a.onclick = ev => { ev.preventDefault(); document.getElementById('s-' + a.dataset.sec)?.scrollIntoView({behavior: 'smooth', block: 'start'}); });
}

// --- router ----------------------------------------------------------------
async function route() {
  if (site.enc && !key) { renderGate(false); return; }
  const h = location.hash.replace(/^#\/?/, '');
  const [path, qs] = h.split('?');
  const q = new URLSearchParams(qs || '');
  const parts = path.split('/').filter(Boolean);
  try {
    if (parts[0] === 'm' && parts[1] && parts[2] === 'summary') await viewSummary(decodeURIComponent(parts[1]));
    else if (parts[0] === 'm' && parts[1]) await viewMeeting(decodeURIComponent(parts[1]), q.get('t'));
    else await viewList();
    window.scrollTo(0, 0);
  } catch (e) {
    app.innerHTML = `<main class="wrap"><div class="err" style="margin-top:28px">Couldn't load that: ${esc(e.message)}. <a href="#/">Back to the list.</a></div></main>`;
  }
}
async function boot() {
  try {
    const r = await fetch('site.json', {cache: 'no-cache'});
    if (!r.ok) throw new Error('site.json ' + r.status);
    site = await r.json();
  } catch (e) {
    app.innerHTML = `<main class="wrap"><p class="empty"><b>Nothing published yet.</b>${esc(e.message)}</p></main>`;
    return;
  }
  if (site.enc) {
    const saved = savedPass();
    if (saved && await tryPass(saved)) { /* unlocked */ }
    else { try { sessionStorage.removeItem(PASS_KEY); localStorage.removeItem(PASS_KEY); } catch {} }
  }
  route();
}
window.addEventListener('hashchange', route);
boot();
})();
