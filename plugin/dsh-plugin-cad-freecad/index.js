// Host plugin: register the `cad_freecad` tool. It shells out to the bundled
// CadKit engine (python/entry.py) through a headless freecadcmd process and
// returns the single CADKIT>>{json} result line as structured output.
import { spawn, execFile } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import os from 'node:os';
import fs from 'node:fs';

const HERE = path.dirname(fileURLToPath(import.meta.url));

export const inject = ['tools'];

// Optional plugin config, passed verbatim to apply(); edit it in
// cordis.patch.yml `config:` (machine-specific values belong in the PROFILE's
// own patch layer, not in the shipped plugin):
//   freecadBin        — path to freecadcmd; `~` is expanded. Omit it and the
//                       plugin discovers the binary (see FREECAD_CANDIDATES).
//   cadRoot           — default CAD project tree root (contains projects/
//                       registry/); `~` is expanded. Omit it and the plugin
//                       prefers <session workspace>/cad, then ~/dsh-cad.
//   timeoutMs         — max wall time for one CadKit action (default 1200000)
//   memCapMb          — soft RSS cap for ONE freecadcmd process (default 2048).
//                       macOS cannot enforce rlimits, so this drives the
//                       engine's budget sizing + in-process watchdog and is
//                       enforced from here by killing the process group.
//   memFloorMb        — memory the OS should keep available (default 768)
//   probeMaxWorkers   — hard ceiling on parallel probe workers (default 2).
//                       The engine may choose fewer from the measured cost of
//                       the model and the memory actually free.
//   groupCapMb        — kill switch for the whole freecadcmd tree
//                       (default memCapMb * (probeMaxWorkers + 1))
//   memSampleMs       — group-RSS sampling period (default 2000)

const ACTIONS = [
  'new', 'build', 'validate', 'probe', 'export', 'package', 'dedupe', 'status',
  'memcheck', 'board',
];

const MEM_DEFAULTS = {
  memCapMb: 2048,
  memFloorMb: 768,
  probeMaxWorkers: 2,
  memSampleMs: 2000,
};

const num = (v, d) => (Number.isFinite(Number(v)) && Number(v) > 0 ? Number(v) : d);

// Rolling tail so a chatty freecadcmd can never grow the Host's heap: the
// only thing we parse back is the final CADKIT>> line.
function tailBuffer(limitBytes, onChunk) {
  let buf = '';
  return {
    push(chunk) {
      buf += chunk;
      if (buf.length > limitBytes) buf = buf.slice(-limitBytes);
      if (onChunk) onChunk(chunk);
    },
    get value() {
      return buf;
    },
  };
}

function sumRss(stdout) {
  let kb = 0;
  for (const line of String(stdout).split('\n')) {
    const v = parseInt(line.trim(), 10);
    if (Number.isFinite(v)) kb += v;
  }
  return kb / 1024;
}

function psRssMb(args) {
  // Best effort only: a denied /bin/ps (file sandbox, EPERM) must never take
  // the Host down with an unhandled child 'error' event.
  return new Promise((resolve) => {
    let done = false;
    const once = (v) => { if (!done) { done = true; resolve(v); } };
    try {
      const child = execFile('/bin/ps', args, { encoding: 'utf8' }, (err, stdout) =>
        once(err ? null : sumRss(stdout)));
      child.on('error', () => once(null));
    } catch {
      once(null);
    }
  });
}

/* ---------- portability: discover the two machine-specific inputs --------- */
/** `~/x` and `~` to real paths, so a shipped config never names a user. */
export function expandHome(value) {
  if (typeof value !== 'string' || value.length === 0) return undefined;
  if (value === '~') return os.homedir();
  if (value.startsWith('~/') || value.startsWith('~\\')) {
    return path.join(os.homedir(), value.slice(2));
  }
  return value;
}

function isFile(p) {
  try {
    return fs.statSync(p).isFile();
  } catch {
    return false;
  }
}

function isRunnable(p) {
  if (!isFile(p)) return false;
  if (process.platform === 'win32') return true;
  try {
    fs.accessSync(p, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

/** A bare command name resolved through PATH, without spawning `which`. */
function fromPath(name) {
  const dirs = String(process.env.PATH || '').split(path.delimiter).filter(Boolean);
  for (const dir of dirs) {
    const candidate = path.join(dir, name);
    if (isRunnable(candidate)) return candidate;
  }
  return undefined;
}

// Packaged / distro locations tried in order when nothing is configured. The
// FreeCAD app bundle on macOS moved its CLI under Contents/Resources/bin, so
// both shapes are listed; each is checked, never assumed.
const FREECAD_CANDIDATES = [
  '/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd',
  '/Applications/FreeCAD.app/Contents/bin/freecadcmd',
  '/Applications/FreeCAD 1.0.app/Contents/Resources/bin/freecadcmd',
  '/Applications/FreeCAD 1.1.app/Contents/Resources/bin/freecadcmd',
  '/usr/local/bin/freecadcmd',
  '/usr/bin/freecadcmd',
  '/opt/freecad/bin/freecadcmd',
  '/opt/homebrew/bin/freecadcmd',
  'C:/Program Files/FreeCAD 1.0/bin/freecadcmd.exe',
  'C:/Program Files/FreeCAD 1.1/bin/freecadcmd.exe',
];

export function resolveBin(cfg = {}, env = process.env) {
  const configured = expandHome(cfg.freecadBin) || expandHome(env.CADKIT_FREECAD_BIN);
  if (configured) {
    return isRunnable(configured)
      ? { bin: configured, source: 'config' }
      : { bin: undefined, source: 'config-missing', tried: [configured] };
  }
  const tried = [];
  for (const candidate of FREECAD_CANDIDATES) {
    tried.push(candidate);
    if (isRunnable(candidate)) return { bin: candidate, source: 'discovered' };
  }
  const onPath = fromPath('freecadcmd');
  if (onPath) return { bin: onPath, source: 'discovered-path' };
  return { bin: undefined, source: 'not-found', tried };
}

/**
 * Session workspace, opportunistically. `exec.ctx` is the cordis context; the
 * sandbox policy is what names the workspace-write root. Nothing here is
 * declared as an injection (that would make the whole plugin fail to load if a
 * composition lacks the service), so every access is guarded and the caller
 * still has config/env/`root` to fall back on.
 */
function sessionWorkspace(exec) {
  const ctx = exec && exec.ctx;
  if (!ctx) return undefined;
  // `services` is the real cordis context (forwarded by apply()); the raw
  // ctx is probed too, in case a build already hands it over.
  const holders = [ctx, ctx.services].filter(Boolean);
  const probes = [
    (c) => c.sandboxPolicy && c.sandboxPolicy.workspaceRoot,
    (c) => c.sandboxPolicy && c.sandboxPolicy.resolve().workspaceRoot,
    (c) => c.workspace && (c.workspace.root || c.workspace.cwd),
    (c) => c.agent && c.agent.session && c.agent.session.cwd,
    (c) => c.workingDirectory && c.workingDirectory.current && c.workingDirectory.current(),
  ];
  for (const holder of holders) {
    for (const probe of probes) {
      try {
        const value = probe(holder);
        if (typeof value === 'string' && path.isAbsolute(value)) return value;
      } catch { /* this shape is not the one this build exposes */ }
    }
  }
  return undefined;
}

/**
 * Cad root precedence: explicit arg → env → configured (if it exists) →
 * <session workspace>/cad (if it exists) → configured (to create) → ~/dsh-cad.
 * Existence is checked before creation so a stale absolute path left in a
 * patch can never silently become the active project tree.
 */
export function resolveRoot(args = {}, cfg = {}, exec = {}, env = process.env) {
  const explicit = expandHome(args.root);
  if (explicit) return { root: path.resolve(explicit), source: 'arg' };
  const fromEnv = expandHome(env.CADKIT_ROOT);
  if (fromEnv) return { root: path.resolve(fromEnv), source: 'env' };
  const configured = expandHome(cfg.cadRoot);
  if (configured && isDir(configured)) return { root: configured, source: 'config' };
  const workspace = sessionWorkspace(exec);
  if (workspace) {
    const inside = path.join(workspace, 'cad');
    if (isDir(inside) || !configured) return { root: inside, source: 'session-workspace' };
  }
  if (configured) return { root: configured, source: 'config-create' };
  return { root: path.join(os.homedir(), 'dsh-cad'), source: 'home-default' };
}

function isDir(p) {
  try {
    return fs.statSync(p).isDirectory();
  } catch {
    return false;
  }
}

const TOOL = {
  name: 'cad_freecad',
  description:
    'Drive the CadKit/FreeCAD CAD pipeline for one part project. Actions: ' +
    '`new` scaffold a project; `build` run build.py into a feature-tree FCStd; ' +
    '`validate` run the machine gate checks (rebuild, constraints, valid solids, ' +
    'size, tidiness, step-count grade); `probe` perturb every driving parameter ' +
    'to prove the model is editably parametric; `export` write STEP AP214 plus 8 ' +
    'delivery renders and self-check geometry consistency; `package` verify the ' +
    'mandatory 6-part delivery checklist and emit the delivery folder + batch CSV ' +
    '(refuses to emit anything if a required artifact is missing); `dedupe` ' +
    'compare the geometry fingerprint against the registry; `status` report which ' +
    'artifacts exist; `memcheck` report the current memory budget and how the ' +
    'next export/probe would be sized; `board` rebuild the sidebar panel snapshot ' +
    '(<cadRoot>/mem/board.json) and return it. Every result carries a ' +
    '`mem` block: cap, live/peak RSS and any auto-degrade steps applied. Returns ' +
    'JSON; check `ok` and any `failed`/`checks` detail.',
  parameters: {
    type: 'object',
    additionalProperties: false,
    properties: {
      action: { type: 'string', enum: ACTIONS, description: 'Pipeline action to run.' },
      project: {
        type: 'string',
        description: 'Project id (folder name under <cadRoot>/projects). Omit for `new`.',
      },
      root: { type: 'string', description: 'Override CAD root for this call.' },
      opts: {
        type: 'object',
        description:
          'Action options, e.g. {"batch":"acme_2024"} for package, {"long_side":2400, ' +
          '"max_triangles":400000} for export, {"id":"motor_end_cap_80","title":"Motor end cap"} ' +
          'for new (id is required; optional unit, type, grade, author, source_url, license, series), ' +
          '{"limit":..,"delta":..,"workers":1} for probe. Memory: {"mem_cap_mb":1536} ' +
          "lowers this call's per-process cap below the configured one.",
        additionalProperties: true,
      },
    },
    required: ['action'],
  },
  output: {
    schema: {
      type: 'object',
      properties: {
        ok: { type: 'boolean' },
        action: { type: 'string' },
        result: { type: 'object', additionalProperties: true },
        error: { type: 'string' },
      },
      required: ['ok', 'action'],
    },
    render(_args, value) {
      return [{ type: 'text', text: JSON.stringify(value, null, 2) }];
    },
  },
  timeoutMs: 1500000, // probe of a full parameter sheet can take ~15 min
  isConcurrencySafe() {
    return false; // one FreeCAD project build/probe at a time (shared FCStd + registry)
  },
  async execute(args, exec) {
    const cfg = (exec.ctx && exec.ctx.config) || {};
    const action = args.action;
    if (!ACTIONS.includes(action)) {
      return { ok: false, action, error: 'unknown action ' + action };
    }
    const located = resolveRoot(args, cfg, exec);
    const found = resolveBin(cfg);
    if (!found.bin) {
      return {
        ok: false, action, cad_root: located.root,
        error: 'freecadcmd not found (looked at: ' + found.tried.join(', ') +
          '). Install FreeCAD, or point the plugin at it: config.freecadBin ' +
          '(a ~ path is fine) or env CADKIT_FREECAD_BIN. Put machine-specific ' +
          'values in the profile patch layer, not in the plugin.',
      };
    }
    const bin = found.bin;
    const root = located.root;
    // Fresh device: create the tree instead of spawning into a missing cwd.
    try {
      fs.mkdirSync(root, { recursive: true });
    } catch (e) {
      return { ok: false, action, error: 'cannot create cad root ' + root + ': ' + e.message };
    }

    // memory governor -------------------------------------------------------
    const opts = args.opts || {};
    const capMb = Math.min(num(cfg.memCapMb, MEM_DEFAULTS.memCapMb),
                           num(opts.mem_cap_mb, Infinity));
    const floorMb = num(cfg.memFloorMb, MEM_DEFAULTS.memFloorMb);
    const maxWorkers = num(cfg.probeMaxWorkers, MEM_DEFAULTS.probeMaxWorkers);
    const groupCapMb = num(cfg.groupCapMb, capMb * (maxWorkers + 1));
    const sampleMs = num(cfg.memSampleMs, MEM_DEFAULTS.memSampleMs);

    const entry = path.join(HERE, 'python', 'entry.py');
    const env = {
      ...process.env,
      CADKIT_ACTION: action,
      CADKIT_ROOT: path.resolve(root),
      CADKIT_OPTS: JSON.stringify(opts),
      CADKIT_MEM_MB: String(Math.round(capMb)),
      CADKIT_MEM_FLOOR_MB: String(Math.round(floorMb)),
      CADKIT_PROBE_MAX_WORKERS: String(Math.round(maxWorkers)),
    };
    if (args.project) env.CADKIT_PROJECT = args.project;

    const out = await new Promise((resolve) => {
      if (exec.signal && exec.signal.aborted) {
        return resolve({ ok: false, action, error: 'aborted before spawn' });
      }
      // Rolling tails: a runaway child can no longer grow the Host heap.
      const stdout = tailBuffer(4 << 20);
      const stderr = tailBuffer(64 << 10);
      let memKilled = false;
      let peakGroupMb = 0;
      // 'unknown' until a sample lands: a sandboxed host may not be allowed to
      // run /bin/ps, and then the backstop is simply not armed — say so.
      let sampler = 'unknown';

      // Own process group, so probe workers grandchildren die with the job:
      // killing only the direct child used to leave freecadcmd orphans
      // holding hundreds of MB each, long after the tool call returned.
      const child = spawn(bin, [entry], {
        env, cwd: path.resolve(root, '..'), detached: true,
      });

      const killGroup = (sig) => {
        try {
          process.kill(-child.pid, sig);
        } catch (e) {
          try { child.kill(sig); } catch { /* already gone */ }
        }
      };

      const timer = setTimeout(() => killGroup('SIGKILL'),
        num(cfg.timeoutMs, 1200000));
      const abort = () => killGroup('SIGKILL');
      exec.signal.addEventListener('abort', abort);

      let sampling = false;
      const watch = setInterval(async () => {
        if (sampling || child.exitCode !== null || child.signalCode) return;
        sampling = true;
        try {
          let rss = await psRssMb(['-o', 'rss=', '-g', String(child.pid)]);
          if (rss === null) {
            rss = await psRssMb(['-o', 'rss=', '-p', String(child.pid)]);
            if (rss !== null) sampler = 'single-process-only';
          }
          if (rss === null) {
            sampler = 'unavailable';          // cannot verify: no kill switch
          } else {
            if (sampler === 'unknown' || sampler === 'single-process-only') {
              if (sampler === 'unknown') sampler = 'process-group';
            }
            peakGroupMb = Math.max(peakGroupMb, rss);
            if (rss > groupCapMb) {
              memKilled = true;
              killGroup('SIGKILL');
            }
          }
        } finally {
          sampling = false;
        }
      }, sampleMs);

      child.stdout.on('data', (b) => stdout.push(String(b)));
      child.stderr.on('data', (b) => stderr.push(String(b)));
      child.on('error', (e) => {
        clearTimeout(timer); clearInterval(watch);
        exec.signal.removeEventListener('abort', abort);
        resolve({ ok: false, action, error: 'spawn failed: ' + e.message });
      });
      child.on('close', (code, signal) => {
        clearTimeout(timer); clearInterval(watch);
        exec.signal.removeEventListener('abort', abort);
        // reap anything that outlived the group leader
        killGroup('SIGKILL');
        let result = null;
        for (const line of stdout.value.split('\n')) {
          const i = line.indexOf('CADKIT>>');
          if (i >= 0) {
            try { result = JSON.parse(line.slice(i + 8)); } catch { /* keep last good */ }
          }
        }
        const memBlock = {
          cap_mb: Math.round(capMb),
          group_cap_mb: Math.round(groupCapMb),
          peak_group_mb: peakGroupMb ? Math.round(peakGroupMb) : null,
          probe_worker_ceiling: Math.round(maxWorkers),
          group_sampler: sampler,
        };
        if (memKilled) {
          resolve({
            ok: false, action, exit: code, signal: 'SIGKILL', mem: memBlock,
            memory_exceeded: true,
            error: 'killed: freecadcmd tree passed the ' +
              Math.round(groupCapMb) + 'MB cap (peak ' +
              Math.round(peakGroupMb) + 'MB; group sampler: ' + sampler +
              '). Lower config.memCapMb further or ' +
              'probe fewer parameters (opts {"limit":N}) / render smaller ' +
              '(opts {"long_side":2000}).',
            stderr: stderr.value.slice(-2000),
          });
          return;
        }
        if (result) {
          result.mem = Object.assign({}, memBlock, result.mem || {});
          // W15 (workflow audit): a parseable CADKIT line is NOT success when
          // the child died nonzero/signal — require clean exit 0.
          resolve({ ok: result.ok === true && code === 0, action, exit: code,
            result });
        } else {
          resolve({
            ok: false, action, exit: code, signal, mem: memBlock,
            error: 'no CADKIT result (exit ' + code + ')' +
              (code === 86 ? ' — engine watchdog aborted at the memory cap' : ''),
            stderr: stderr.value.slice(-2000),
            stdout: stdout.value.slice(-2000),
          });
        }
      });
    });
    // Every answer says which tree and which binary actually ran: on a new
    // device that is the first thing worth knowing.
    if (out && typeof out === 'object') {
      out.cad_root = root;
      out.cad_root_source = located.source;
      out.freecad_bin = bin;
      out.freecad_bin_source = found.source;
    }
    return out;
  },
};

export function apply(ctx, config) {
  // stash resolved config for execute() via a closure-scoped tool definition
  const resolved = config || {};
  const def = {
    ...TOOL,
    async execute(args, exec) {
      // `config` is the plugin's own view; `services` lets execute() ask the
      // context for the session workspace when no cadRoot is configured.
      return TOOL.execute(args, {
        ...exec,
        ctx: Object.assign({}, exec.ctx, { config: resolved, services: ctx }),
      });
    },
  };
  return ctx.tools.register(def);
}
