/* Word export for the published viewer.

   A .docx is a zip of XML files, so this writes both by hand and needs no
   library. Keeping the viewer free of third-party scripts matters here: the
   pages are decrypted in the browser, and nothing else should get to read
   them. Output mirrors transcriber/export.py so a download from the site
   looks like one made on the laptop.

   window.matlackDocx = { transcript(m), summary(m), download(name, bytes) } */
(() => {
'use strict';

// --- zip (stored, no compression) -----------------------------------------
const CRC = new Int32Array(256);
for (let n = 0; n < 256; n++) {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1;
  CRC[n] = c;
}
function crc32(b) {
  let c = -1;
  for (let i = 0; i < b.length; i++) c = CRC[(c ^ b[i]) & 0xff] ^ (c >>> 8);
  return (c ^ -1) >>> 0;
}
function zip(entries) {  // [[name, text]] -> Uint8Array
  const enc = new TextEncoder(), now = new Date();
  const dosTime = (now.getHours() << 11) | (now.getMinutes() << 5) | (now.getSeconds() >> 1);
  const dosDate = ((now.getFullYear() - 1980) << 9) | ((now.getMonth() + 1) << 5) | now.getDate();
  const parts = [], central = [];
  let offset = 0;
  for (const [name, text] of entries) {
    const n = enc.encode(name), d = enc.encode(text), crc = crc32(d);
    const lh = new DataView(new ArrayBuffer(30));
    lh.setUint32(0, 0x04034b50, true); lh.setUint16(4, 20, true); lh.setUint16(6, 0x0800, true);
    lh.setUint16(8, 0, true); lh.setUint16(10, dosTime, true); lh.setUint16(12, dosDate, true);
    lh.setUint32(14, crc, true); lh.setUint32(18, d.length, true); lh.setUint32(22, d.length, true);
    lh.setUint16(26, n.length, true); lh.setUint16(28, 0, true);
    parts.push(new Uint8Array(lh.buffer), n, d);
    const cd = new DataView(new ArrayBuffer(46));
    cd.setUint32(0, 0x02014b50, true); cd.setUint16(4, 20, true); cd.setUint16(6, 20, true);
    cd.setUint16(8, 0x0800, true); cd.setUint16(10, 0, true); cd.setUint16(12, dosTime, true);
    cd.setUint16(14, dosDate, true); cd.setUint32(16, crc, true); cd.setUint32(20, d.length, true);
    cd.setUint32(24, d.length, true); cd.setUint16(28, n.length, true); cd.setUint16(30, 0, true);
    cd.setUint16(32, 0, true); cd.setUint16(34, 0, true); cd.setUint16(36, 0, true);
    cd.setUint32(38, 0, true); cd.setUint32(42, offset, true);
    central.push(new Uint8Array(cd.buffer), n);
    offset += 30 + n.length + d.length;
  }
  const cdSize = central.reduce((a, b) => a + b.length, 0);
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true); end.setUint16(4, 0, true); end.setUint16(6, 0, true);
  end.setUint16(8, entries.length, true); end.setUint16(10, entries.length, true);
  end.setUint32(12, cdSize, true); end.setUint32(16, offset, true); end.setUint16(20, 0, true);
  const all = [...parts, ...central, new Uint8Array(end.buffer)];
  const out = new Uint8Array(all.reduce((a, b) => a + b.length, 0));
  let p = 0;
  for (const a of all) { out.set(a, p); p += a.length; }
  return out;
}

// --- WordprocessingML ------------------------------------------------------
const W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main';
const x = s => String(s ?? '')
  .replace(/[\x00-\x08\x0B\x0C\x0E-\x1F]/g, '')
  .replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const fmt = ms => {
  if (ms == null) return '--:--';
  const s = Math.floor(ms / 1000), h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), sec = s % 60;
  return (h ? h + ':' + String(m).padStart(2, '0') : String(m).padStart(2, '0')) + ':' + String(sec).padStart(2, '0');
};
const longDate = ts => ts ? new Date(ts * 1000).toLocaleDateString(undefined, {month: 'long', day: 'numeric', year: 'numeric'}) : '';

// A run: {t, b, i, sz (points), color}. Newlines inside t become line breaks.
function run(r) {
  if (typeof r === 'string') r = {t: r};
  const pr = [r.b ? '<w:b/>' : '', r.i ? '<w:i/>' : '', r.color ? `<w:color w:val="${r.color}"/>` : '',
              r.sz ? `<w:sz w:val="${r.sz * 2}"/><w:szCs w:val="${r.sz * 2}"/>` : ''].join('');
  const text = String(r.t ?? '').split('\n').map(s => `<w:t xml:space="preserve">${x(s)}</w:t>`).join('<w:br/>');
  return `<w:r>${pr ? `<w:rPr>${pr}</w:rPr>` : ''}${text}</w:r>`;
}
// A paragraph: runs plus {style, num} where num is 1 (bullet) or 2 (decimal).
function para(runs, o = {}) {
  const pr = [o.style ? `<w:pStyle w:val="${o.style}"/>` : '',
              o.num ? `<w:numPr><w:ilvl w:val="0"/><w:numId w:val="${o.num}"/></w:numPr>` : ''].join('');
  return `<w:p>${pr ? `<w:pPr>${pr}</w:pPr>` : ''}${(Array.isArray(runs) ? runs : [runs]).map(run).join('')}</w:p>`;
}
const heading = (t, lvl = 1) => para([{t}], {style: lvl === 0 ? 'Title' : 'Heading1'});
const bullet = t => para([{t}], {style: 'ListParagraph', num: 1});
const numbered = t => para([{t}], {style: 'ListParagraph', num: 2});

const STYLES = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="${W}">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri" w:eastAsia="Calibri"/><w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="en-US"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:after="60"/></w:pPr><w:rPr><w:sz w:val="52"/><w:szCs w:val="52"/><w:color w:val="1F2A30"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:after="200"/></w:pPr><w:rPr><w:i/><w:sz w:val="24"/><w:szCs w:val="24"/><w:color w:val="666E72"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="360" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="30"/><w:szCs w:val="30"/><w:color w:val="1F2A30"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:pBdr><w:left w:val="single" w:sz="12" w:space="12" w:color="C9B99A"/></w:pBdr><w:ind w:left="720" w:right="720"/><w:spacing w:before="120" w:after="200"/></w:pPr><w:rPr><w:i/><w:color w:val="444B50"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/><w:qFormat/><w:pPr><w:ind w:left="720"/><w:contextualSpacing/></w:pPr></w:style>
</w:styles>`;

const NUMBERING = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="${W}">
<w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="singleLevel"/><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/><w:lvlJc w:val="left"/><w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum>
<w:abstractNum w:abstractNumId="1"><w:multiLevelType w:val="singleLevel"/><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/><w:lvlJc w:val="left"/><w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum>
<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
<w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>
</w:numbering>`;

function pack(title, body) {
  const doc = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="${W}"><w:body>${body.join('')}<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="720" w:footer="720" w:gutter="0"/></w:sectPr></w:body></w:document>`;
  const stamp = new Date().toISOString().replace(/\.\d+Z$/, 'Z');
  return zip([
    ['[Content_Types].xml', `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
</Types>`],
    ['_rels/.rels', `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
</Relationships>`],
    ['docProps/core.xml', `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<dc:title>${x(title)}</dc:title><dc:creator>Matlack</dc:creator>
<dcterms:created xsi:type="dcterms:W3CDTF">${stamp}</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">${stamp}</dcterms:modified>
</cp:coreProperties>`],
    ['word/_rels/document.xml.rels', `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
</Relationships>`],
    ['word/styles.xml', STYLES],
    ['word/numbering.xml', NUMBERING],
    ['word/document.xml', doc],
  ]);
}

// --- documents (same shape as transcriber/export.py) -----------------------
const nameOf = (m, l) => m.speakers[l]?.name || `Speaker ${l}`;
function speakerKey(m) {
  return Object.keys(m.speakers || {}).sort().map(l => nameOf(m, l) + (m.speakers[l].confirmed ? '' : ' (not confirmed)'));
}
function blocks(m) {  // consecutive lines from one speaker become a paragraph
  const out = [];
  for (const u of m.utterances || []) {
    const name = nameOf(m, u.speaker);
    if (out.length && out[out.length - 1].name === name) out[out.length - 1].text += ' ' + u.text;
    else out.push({name, start: u.start, text: u.text});
  }
  return out;
}

function transcript(m) {
  const body = [heading(m.title, 1)];
  const key = speakerKey(m);
  if (key.length) body.push(para([{t: 'Speakers: ', b: true}, key.join('; ')]));
  for (const b of blocks(m)) body.push(para([{t: b.name + ' ', b: true}, {t: `(${fmt(b.start)})  `, sz: 9}, b.text]));
  return pack(m.title, body);
}

function summary(m) {
  const s = m.summary;
  if (!s) throw new Error('no summary');
  const body = [heading(m.title, 0), para([{t: s.guide_title || 'Summary'}], {style: 'Subtitle'})];
  const key = speakerKey(m);
  if (key.length) body.push(para([{t: 'Speakers: ', b: true}, key.join('; ')]));
  if (s.created) body.push(para('Summarized ' + longDate(s.created)));
  if (s.overview) body.push(heading('Overview'), para(s.overview));
  if (s.priorities?.length) body.push(heading('Top priorities'), ...s.priorities.map(numbered));
  for (const sec of s.sections || []) {
    body.push(heading(sec.title), para([{t: sec.question, i: true, color: '666E72'}]));
    if (!(sec.covered || sec.summary)) { body.push(para([{t: 'Not discussed.', i: true}])); continue; }
    if (sec.summary) body.push(para(sec.summary));
    for (const p of sec.points || []) body.push(bullet(p));
    for (const q of sec.quotes || []) {
      const tail = (q.speaker ? ` — ${q.speaker}` : '') + (q.time ? ` (${q.time})` : '');
      body.push(para([{t: `“${q.text}”`}, ...(tail ? [{t: tail, sz: 9}] : [])], {style: 'Quote'}));
    }
  }
  if (s.follow_ups?.length) body.push(heading('Follow-ups'), ...s.follow_ups.map(bullet));
  return pack(`${m.title} — ${s.guide_title || 'Summary'}`, body);
}

function download(name, bytes) {
  const blob = new Blob([bytes], {type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

globalThis.matlackDocx = {transcript, summary, download};
})();
