// July OpenClaw adapter. No reset/restore/patch, transcript writes or index updates.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL, fileURLToPath} from 'node:url';

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const PAGE_BYTES = 320 * 1024;
const CHUNK_BYTES = 96 * 1024;
const TTL_MS = 60 * 60 * 1000;
const fail = (code, message) => { throw Object.assign(new Error(message), {code}); };
const emit = value => process.stdout.write('AGENT_SEGMENT_RESULT:' + JSON.stringify(value) + '\n');
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
const scope = hash(JSON.stringify([input.root, input.sessionKey, input.sessionId]));
// Tokens are never authorization: every request independently checks segment membership,
// file identity and allowed media roots. Rotation of gateway auth also expires tokens.
const tokenKey = hash(JSON.stringify([input.root, input.config.gateway?.auth ?? {},
  process.env.AGENT_MANAGER_HISTORY_CURSOR_KEY ?? '']));
function token(value) {
  const body = Buffer.from(JSON.stringify({...value, scope, v: 1})).toString('base64url');
  return body + '.' + crypto.createHmac('sha256', tokenKey).update(body).digest('base64url');
}
function decode(value, kind) {
  try {
    const [body, signature, extra] = value.split('.');
    const expected = crypto.createHmac('sha256', tokenKey).update(body).digest('base64url');
    if (extra || typeof signature !== 'string' || signature.length !== expected.length ||
        !crypto.timingSafeEqual(Buffer.from(signature), Buffer.from(expected))) throw new Error();
    const data = JSON.parse(Buffer.from(body, 'base64url'));
    if (data.v !== 1 || data.scope !== scope || data.kind !== kind ||
        !Number.isSafeInteger(data.expires) || data.expires < Date.now()) throw new Error();
    return data;
  } catch { fail('CURSOR_EXPIRED', 'Reference expired or belongs to another instance, thread or segment'); }
}
async function native(stem, names) {
  const dir = path.join(input.package, 'dist');
  const files = fs.readdirSync(dir).filter(name => name.startsWith(stem + '-') && name.endsWith('.js'));
  for (const file of files) {
    const source = fs.readFileSync(path.join(dir, file), 'utf8');
    const exports = source.match(/export \{([^}]+)\}/s)?.[1];
    if (!exports) continue;
    const aliases = names.map(name => {
      const match = exports.split(',').map(s => s.trim()).find(s => s === name || s.startsWith(name + ' as '));
      return match ? match.split(' as ').at(-1) : null;
    });
    if (aliases.some(name => !name)) continue;
    const module = await import(pathToFileURL(path.join(dir, file)));
    return Object.fromEntries(names.map((name, i) => [name, module[aliases[i]]]));
  }
  fail('SEGMENT_READ_FAILED', 'Required July OpenClaw read-only exports are unavailable');
}
function inside(file, root) {
  const relative = path.relative(root, file);
  return relative === '' || (!relative.startsWith('..' + path.sep) && relative !== '..' && !path.isAbsolute(relative));
}
function safeFile(file, roots) {
  const absolute = path.resolve(file);
  roots = roots.flatMap(root => [path.resolve(root), ...fs.existsSync(root) ? [fs.realpathSync(root)] : []]);
  const root = roots.find(root => inside(absolute, root));
  if (!root) fail('FORBIDDEN', 'File is outside the allowed instance directory');
  // Do not follow user-controlled symlinks, including intermediate components.
  let current = root;
  for (const part of path.relative(root, absolute).split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    const stat = fs.lstatSync(current);
    if (stat.isSymbolicLink()) fail('FORBIDDEN', 'Symbolic links cannot be read');
  }
  const real = fs.realpathSync(absolute);
  if (!inside(real, fs.realpathSync(root)) || !fs.statSync(real).isFile()) fail('FORBIDDEN', 'Invalid file');
  return real;
}
function millis(value) {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  const time = typeof value === 'string' ? Date.parse(value) : NaN;
  return Number.isFinite(time) ? time : null;
}
function shortText(message) {
  const content = typeof message.content === 'string' ? message.content :
    Array.isArray(message.content) ? message.content.filter(b => b?.type === 'text').map(b => b.text ?? '').join('\n') : '';
  return Array.from(content).slice(0, 180).join('');
}

async function run() {
  const sdk = await import(pathToFileURL(path.join(input.package, 'dist/plugin-sdk/session-store-runtime.js')));
  const agentId = input.sessionKey.split(':')[1];
  const configured = input.config.agents?.list ?? [];
  const agent = configured.find(a => a.id === agentId);
  if (!agent && agentId !== 'main') fail('FORBIDDEN', 'Agent is not configured in this instance');
  const storePath = sdk.resolveStorePath(input.config.session?.store, {agentId, env: process.env});
  const entry = sdk.getSessionEntry({sessionKey: input.sessionKey, agentId, storePath,
    env: process.env, readConsistency: 'latest', hydrateSkillPromptRefs: false});
  if (!entry || ![entry.sessionId, ...entry.usageFamilySessionIds ?? []].includes(input.sessionId)) {
    fail('FORBIDDEN', 'Physical segment does not belong to this logical thread');
  }
  const files = await native('session-transcript-files.fs', ['resolveSessionTranscriptCandidates', 'resolveSessionTranscriptResetArchiveCandidatesAsync']);
  const tree = await native('transcript-tree', ['scanSessionTranscriptTree', 'selectSessionTranscriptTreePathNodes']);
  const sessionFile = input.sessionId === entry.sessionId ? entry.sessionFile :
    sdk.resolveSessionTranscriptPathInDir(input.sessionId, path.dirname(storePath),
      /:topic:([^:]+)$/.exec(input.sessionKey)?.[1]);
  const candidates = files.resolveSessionTranscriptCandidates(input.sessionId, storePath, sessionFile, agentId);
  const roots = [path.dirname(path.resolve(storePath)), path.join(input.root, 'sessions')]
    .map(root => fs.existsSync(root) ? fs.realpathSync(root) : path.resolve(root));
  const archives = await files.resolveSessionTranscriptResetArchiveCandidatesAsync(input.sessionId, storePath, sessionFile, agentId);
  const available = [...new Set([...candidates, ...archives])].filter(file => fs.existsSync(file) && roots.some(root => inside(path.resolve(file), root)));
  // Follow an already-read file across reset rename, without selecting another physical ID.
  const referenceKind = input.action === 'history' ? 'page' : input.action;
  const reference = input.cursor ? decode(input.cursor, input.action === 'history' ? 'page' : 'chunk') :
    input.ref ? decode(input.ref, referenceKind) : null;
  const snap = reference?.snapshot;
  if (snap) for (const base of candidates) {
    if (!roots.some(root => inside(path.resolve(base), root)) || !fs.existsSync(path.dirname(base))) continue;
    for (const name of fs.readdirSync(path.dirname(base))) if (name.startsWith(path.basename(base) + '.reset.')) {
      const file = path.join(path.dirname(base), name);
      if (!available.includes(file)) available.push(file);
    }
  }
  let file, stat;
  for (const candidate of available) {
    const checked = safeFile(candidate, roots);
    const candidateStat = fs.statSync(checked);
    if (snap && (String(candidateStat.dev) !== snap.dev || String(candidateStat.ino) !== snap.ino)) continue;
    file = checked; stat = candidateStat; break;
  }
  if (!file) fail(snap ? 'CURSOR_EXPIRED' : 'SEGMENT_NOT_FOUND', snap ? 'Transcript snapshot is no longer available' : 'Physical segment transcript is missing');
  const fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
  try {
    const opened = fs.fstatSync(fd);
    if (opened.ino !== stat.ino || opened.dev !== stat.dev) fail('CURSOR_EXPIRED', 'Transcript changed during open');
    const boundary = snap?.bytes ?? opened.size;
    if (!Number.isSafeInteger(boundary) || boundary < 0 || boundary > opened.size) fail('CURSOR_EXPIRED', 'Transcript was truncated');
    const digest = crypto.createHash('sha256');
    const records = [];
    let carry = Buffer.alloc(0), position = 0, lineStart = 0, committed = 0;
    const block = Buffer.alloc(64 * 1024);
    function parseLine(line, start) {
      if (!line.toString('utf8').trim()) return;
      let raw;
      try { raw = JSON.parse(line.toString('utf8')); }
      catch { fail('SEGMENT_READ_FAILED', 'Transcript contains a malformed complete record'); }
      if (!raw || typeof raw !== 'object' || Array.isArray(raw)) fail('SEGMENT_READ_FAILED', 'Invalid transcript record');
      if (raw.type === 'session' && raw.id !== input.sessionId) fail('FORBIDDEN', 'Transcript header belongs to another physical segment');
      const meta = {type: raw.type, id: raw.id, parentId: raw.parentId, targetId: raw.targetId,
        appendMode: raw.appendMode, appendParentId: raw.appendParentId};
      // Absence of parentId has different meaning from an explicit null in the native tree.
      for (const key of Object.keys(meta)) if (meta[key] === undefined) delete meta[key];
      records.push({meta, start, length: line.length, timestamp: millis(raw.timestamp ?? raw.message?.timestamp),
        role: raw.message?.role, preview: raw.message ? shortText(raw.message) : '', hasMessage: !!raw.message});
    }
    while (position < boundary) {
      const read = fs.readSync(fd, block, 0, Math.min(block.length, boundary - position), position);
      if (!read) fail('CURSOR_EXPIRED', 'Transcript changed during read');
      position += read;
      carry = Buffer.concat([carry, block.subarray(0, read)]);
      let newline;
      while ((newline = carry.indexOf(10)) !== -1) {
        const line = carry.subarray(0, newline);
        parseLine(line, lineStart);
        digest.update(carry.subarray(0, newline + 1));
        committed = lineStart + newline + 1;
        lineStart = committed;
        carry = carry.subarray(newline + 1);
      }
    }
    // JSONL archives need not end with a newline. Only a genuinely incomplete writer
    // tail is outside the first snapshot; a complete final JSON record is retained.
    if (carry.length && carry.toString('utf8').trim()) {
      let complete = false;
      try { JSON.parse(carry.toString('utf8')); complete = true; } catch {}
      if (complete) { parseLine(carry, lineStart); digest.update(carry); committed = boundary; }
      else if (input.sessionId !== entry.sessionId || snap) fail('SEGMENT_READ_FAILED', 'Archive has an incomplete final record');
    }
    const fingerprint = digest.digest('hex');
    if (snap && (committed !== snap.bytes || fingerprint !== snap.hash)) fail('CURSOR_EXPIRED', 'Transcript snapshot changed');
    const snapshot = snap ?? {dev: String(opened.dev), ino: String(opened.ino), bytes: committed, hash: fingerprint};
    function finish(payload) {
      const verification = crypto.createHash('sha256');
      const buffer = Buffer.alloc(64 * 1024);
      for (let at = 0; at < committed;) {
        const count = fs.readSync(fd, buffer, 0, Math.min(buffer.length, committed - at), at);
        if (!count) fail('CURSOR_EXPIRED', 'Transcript changed before response');
        verification.update(buffer.subarray(0, count)); at += count;
      }
      if (verification.digest('hex') !== fingerprint) fail('CURSOR_EXPIRED', 'Transcript changed before response');
      const latest = sdk.getSessionEntry({sessionKey: input.sessionKey, agentId, storePath,
        env: process.env, readConsistency: 'latest', hydrateSkillPromptRefs: false});
      if (!latest || ![latest.sessionId, ...latest.usageFamilySessionIds ?? []].includes(input.sessionId)) {
        fail('FORBIDDEN', 'Segment membership changed during read');
      }
      if (payload.segment) payload.segment.isCurrent = latest.sessionId === input.sessionId;
      return payload;
    }
    const expires = reference?.expires ?? Date.now() + TTL_MS;
    const navigation = tree.scanSessionTranscriptTree(records.map(r => r.meta));
    if (navigation.hasInvalidLeafControl) fail('SEGMENT_READ_FAILED', 'Transcript contains an invalid branch selector');
    const selected = navigation.hasExplicitLeafUpdate ?
      tree.selectSessionTranscriptTreePathNodes(navigation, navigation.leafId).map(n => records[n.index]) : records;
    const rows = selected.filter(r => r.hasMessage || r.meta.type === 'compaction' || r.meta.type === 'custom_message');
    const id = row => row.meta.id || 'entry-' + row.start.toString(16);
    const ids = new Set();
    for (const row of rows) {
      if (ids.has(id(row))) fail('SEGMENT_READ_FAILED', 'Transcript contains duplicate message IDs');
      ids.add(id(row));
    }
    const readRaw = row => {
      const bytes = Buffer.alloc(row.length);
      if (fs.readSync(fd, bytes, 0, bytes.length, row.start) !== bytes.length) fail('CURSOR_EXPIRED', 'Message snapshot changed');
      return JSON.parse(bytes.toString('utf8'));
    };
    const refToken = (kind, values = {}) => token({kind, snapshot, expires, ...values});
    const messageRef = row => ({method: 'sessions.segments.message.get',
      params: {sessionKey: input.sessionKey, sessionId: input.sessionId, messageId: id(row), ref: refToken('message', {messageId: id(row)})}});
    function mediaRef(row, pointer, source) {
      return {method: 'sessions.segments.media.get', params: {sessionKey: input.sessionKey,
        sessionId: input.sessionId, ref: refToken('media', {messageId: id(row), pointer, source})}};
    }
    const extensions = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif',
      '.webp': 'image/webp', '.svg': 'image/svg+xml', '.mp3': 'audio/mpeg', '.wav': 'audio/wav',
      '.ogg': 'audio/ogg', '.opus': 'audio/opus', '.m4a': 'audio/mp4', '.flac': 'audio/flac', '.aac': 'audio/aac',
      '.webm': 'video/webm', '.mp4': 'video/mp4', '.avif': 'image/avif', '.heic': 'image/heic', '.pdf': 'application/pdf'};
    function localMedia(value) {
      return typeof value === 'string' && !/^https?:\/\//i.test(value) &&
        !!extensions[path.extname(value).toLowerCase()];
    }
    function materialize(row) {
      const raw = readRaw(row);
      const message = raw.message ? structuredClone(raw.message) : raw.type === 'custom_message' ?
        {role: 'system', content: raw.content, customType: raw.customType} :
        {role: 'system', content: [{type: 'text', text: 'Compaction'}], compaction: raw};
      message.id = id(row);
      message.timestamp = millis(message.timestamp) ?? row.timestamp;
      message.__openclaw = {...message.__openclaw, id: id(row),
        ...raw.type !== 'message' ? {kind: raw.type} : {}};
      const media = [];
      function visit(value, pointer, mediaArray = false) {
        if (!value || typeof value !== 'object') return;
        if (value.type === 'image' && typeof value.data === 'string') {
          const reference = mediaRef(row, [...pointer, 'data'], 'base64');
          delete value.data; value.mediaRef = reference; media.push(reference);
        } else if (value.type === 'base64' && typeof value.data === 'string') {
          const reference = mediaRef(row, [...pointer, 'data'], 'base64');
          delete value.data; value.mediaRef = reference; media.push(reference);
        }
        for (const [key, child] of Object.entries(value)) {
          if (key === 'mediaRef') continue;
          if (typeof child === 'string' && (mediaArray || ['url', 'path', 'filePath', 'MediaPath'].includes(key)) && localMedia(child)) {
            media.push(mediaRef(row, [...pointer, key], 'path'));
          } else if (typeof child === 'string' && ['text', 'content'].includes(key)) {
            for (const match of child.matchAll(/MEDIA:\s*([^\s"<>]+)/g)) if (localMedia(match[1])) {
              media.push(mediaRef(row, [...pointer, key], 'text:' + match[1]));
            }
          } else if (typeof child === 'object') visit(child, [...pointer, key],
            Array.isArray(child) && ['paths', 'savedPaths', 'mediaUrls', 'MediaPaths'].includes(key));
        }
      }
      visit(message, []);
      if (media.length) message.mediaRefs = media;
      return message;
    }
    function locate(messageId) {
      const row = rows.find(row => id(row) === messageId);
      if (!row) fail('MESSAGE_NOT_FOUND', 'Message is not present in this segment snapshot');
      return row;
    }
    function chunk(bytes, offset, extra) {
      if (!Number.isSafeInteger(offset) || offset < 0 || offset > bytes.length) fail('CURSOR_EXPIRED', 'Invalid chunk boundary');
      const end = Math.min(bytes.length, offset + CHUNK_BYTES);
      return {...extra, encoding: 'base64', data: bytes.subarray(offset, end).toString('base64'),
        offset, totalBytes: bytes.length, nextCursor: end < bytes.length ?
          refToken('chunk', {action: input.action, messageId: extra.messageId, offset: end,
            ...input.action === 'media' ? {pointer: reference.pointer, source: reference.source, mediaHash: extra.sha256} : {}}) : null};
    }
    if (input.action === 'message') {
      if (reference && (reference.messageId !== input.messageId || input.cursor && reference.action !== 'message')) fail('CURSOR_EXPIRED', 'Message reference mismatch');
      const row = locate(input.messageId);
      const message = materialize(row);
      const bytes = Buffer.from(JSON.stringify(message));
      const base = {sessionKey: input.sessionKey, sessionId: input.sessionId, messageId: id(row)};
      if (!input.cursor && Buffer.byteLength(JSON.stringify(message, null, 2)) < PAGE_BYTES) return finish({...base, message});
      return finish(chunk(bytes, reference?.offset ?? 0, {...base, contentType: 'application/json', sha256: hash(bytes)}));
    }
    if (input.action === 'media') {
      if (!reference || input.cursor && reference.action !== 'media') fail('CURSOR_EXPIRED', 'Invalid media reference');
      const row = locate(reference.messageId);
      const raw = readRaw(row);
      let value = raw.message ?? (raw.type === 'custom_message' ? {content: raw.content} : {});
      if (!Array.isArray(reference.pointer)) fail('CURSOR_EXPIRED', 'Invalid media pointer');
      for (const key of reference.pointer) value = value?.[key];
      if (typeof value !== 'string') fail('MEDIA_NOT_FOUND', 'Media reference is no longer present');
      let bytes, contentType;
      if (reference.source === 'base64') {
        bytes = Buffer.from(value, 'base64'); contentType = 'application/octet-stream';
        let parent = raw.message;
        for (const key of reference.pointer.slice(0, -1)) parent = parent?.[key];
        contentType = parent?.mimeType ?? parent?.media_type ?? parent?.mime_type ?? contentType;
      } else {
        const target = reference.source.startsWith('text:') ? reference.source.slice(5) : value;
        if (reference.source.startsWith('text:') && !value.includes('MEDIA:' + target) && !value.includes('MEDIA: ' + target)) fail('MEDIA_NOT_FOUND', 'Media marker changed');
        const workspace = agent?.workspace ?? input.config.agents?.defaults?.workspace ?? path.join(input.root, 'workspace');
        const mediaRoots = [path.resolve(workspace.replace(/^~(?=\/)/, process.env.HOME)), path.join(input.root, 'media')];
        const absolute = target.startsWith('file://') ? fileURLToPath(target) : path.resolve(mediaRoots[0], target);
        if (!localMedia(target)) fail('FORBIDDEN', 'Unsupported media reference');
        let mediaFile;
        try { mediaFile = safeFile(absolute, mediaRoots); }
        catch (error) { if (error.code === 'ENOENT') fail('MEDIA_NOT_FOUND', 'Media file is missing'); throw error; }
        const mediaFd = fs.openSync(mediaFile, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
        try { bytes = fs.readFileSync(mediaFd); } finally { fs.closeSync(mediaFd); }
        contentType = extensions[path.extname(mediaFile).toLowerCase()];
      }
      const sha256 = hash(bytes);
      if (reference.mediaHash && reference.mediaHash !== sha256) fail('CURSOR_EXPIRED', 'Media changed during chunk read');
      return finish(chunk(bytes, reference.offset ?? 0, {sessionKey: input.sessionKey, sessionId: input.sessionId,
        messageId: id(row), contentType, sha256}));
    }
    const end = reference ? reference.position : input.edge === 'start' ? 0 : rows.length;
    const forward = reference ? reference.direction === 'newer' : input.edge === 'start';
    if (!Number.isSafeInteger(end) || end < 0 || end > rows.length) fail('CURSOR_EXPIRED', 'Invalid page boundary');
    const messages = [];
    let start = end, stop = end, size = 0;
    while (messages.length < input.limit && (forward ? stop < rows.length : start > 0)) {
      const row = rows[forward ? stop : start - 1];
      let message = materialize(row);
      let bytes = Buffer.byteLength(JSON.stringify(message, null, 2));
      if (bytes > PAGE_BYTES / 2) {
        message = {id: id(row), role: row.role ?? 'system', timestamp: row.timestamp, content: [],
          __openclaw: {id: id(row), truncated: true, reason: 'full_message_reference'}, fullMessageRef: messageRef(row)};
        bytes = Buffer.byteLength(JSON.stringify(message, null, 2));
      }
      if (messages.length && size + bytes > PAGE_BYTES) break;
      size += bytes;
      if (forward) { messages.push(message); stop++; } else { messages.unshift(message); start--; }
    }
    const timestamps = records.map(r => r.timestamp).filter(t => t !== null);
    const preview = ['user', 'assistant'].map(role => [...rows].reverse().find(r => r.role === role)?.preview ?? '');
    return finish({sessionKey: input.sessionKey, sessionId: input.sessionId,
      segment: {startedAt: timestamps.length ? timestamps.reduce((a, b) => Math.min(a, b)) : null,
        endedAt: timestamps.length ? timestamps.reduce((a, b) => Math.max(a, b)) : null,
        isCurrent: entry.sessionId === input.sessionId, readable: true, preview}, messages,
      olderCursor: start > 0 ? refToken('page', {position: start, direction: 'older'}) : null,
      newerCursor: stop < rows.length ? refToken('page', {position: stop, direction: 'newer'}) : null});
  } finally { fs.closeSync(fd); }
}
try { emit({ok: true, payload: await run()}); }
catch (error) {
  emit({ok: false, error: {code: error.code && ['FORBIDDEN', 'CURSOR_EXPIRED', 'SEGMENT_NOT_FOUND',
    'SEGMENT_READ_FAILED', 'MESSAGE_NOT_FOUND', 'MEDIA_NOT_FOUND'].includes(error.code) ? error.code : 'SEGMENT_READ_FAILED',
    message: error.code && ['FORBIDDEN', 'CURSOR_EXPIRED', 'SEGMENT_NOT_FOUND', 'MESSAGE_NOT_FOUND', 'MEDIA_NOT_FOUND', 'SEGMENT_READ_FAILED'].includes(error.code) ? error.message : 'Could not read the stored transcript'}});
}
