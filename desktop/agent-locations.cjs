const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const platform = require('./platform.cjs');

/**
 * Where this computer keeps an AI agent's command line.
 *
 * Every installer picks its own place. npm shims land in %APPDATA%\npm, desktop
 * apps ship a CLI inside their install folder, the Microsoft Store hides a whole
 * package under C:\Program Files\WindowsApps, and people who keep apps on another
 * drive (or in a folder with a Chinese name) leave nothing on PATH at all — only a
 * desktop shortcut. Detection therefore reads four sources instead of guessing one:
 *
 *   1. the login PATH (what a shell would resolve),
 *   2. the folders npm, pnpm, bun, scoop and the Node version managers use,
 *   3. each installed app's own resources folder,
 *   4. the folders the desktop and Start Menu shortcuts point at.
 *
 * Nothing here runs a program; it only lists and stats files.
 */

/** Files a command line can be on Windows, most authoritative first. A bare name covers the sh-less scripts (codebuddy) and Node entry files. */
const WINDOWS_EXTENSIONS = ['.exe', '.cmd', '.bat', '', '.cjs', '.js', '.mjs'];

/** Inside a desktop app, a bundled CLI lives in one of these folders. */
const BUNDLED_CLI_RELATIVE = [
  ['resources', 'runtime', 'cli', 'bin'],
  ['resources', 'cli', 'bin'],
  ['resources', 'app.asar.unpacked', 'cli', 'bin'],
  ['resources', 'glm'],
  ['resources', 'bin'],
  ['resources'],
  ['bin'],
];

/** Store packages that carry an agent CLI. Matched against the package family name; everything else in WindowsApps is ignored. */
const STORE_PACKAGES = [/^OpenAI\.Codex_/i, /^OpenAI\.ChatGPT/i, /^Anthropic\.Claude/i, /^Zhipu/i, /^Moonshot/i, /^MiniMax/i];

const readdir = dir => { try { return fs.readdirSync(dir); } catch { return []; } };
const isDirectory = dir => { try { return fs.statSync(dir).isDirectory(); } catch { return false; } };

/** Nothing outside the person's own machine is read: only a single folder listing and the shortcut targets. */
function windowsRoots(env, home) {
  const local = env.LOCALAPPDATA || path.join(home, 'AppData', 'Local');
  const roaming = env.APPDATA || path.join(home, 'AppData', 'Roaming');
  const programData = env.ProgramData || env.ALLUSERSPROFILE || 'C:\\ProgramData';
  const programFiles = env.ProgramFiles || 'C:\\Program Files';
  return { local, roaming, programData, programFiles, home };
}

/** The ANSI target path inside a .lnk is written in the system code page, which is GBK on a Chinese Windows. */
function shortcutTarget(file) {
  let text;
  try { text = fs.readFileSync(file).toString('latin1'); } catch { return undefined; }
  const match = /[A-Za-z]:\\[\x20-\x7e\x80-\xff]{2,240}?\.(?:exe|cmd|bat)/.exec(text);
  if (!match) return undefined;
  let target = match[0];
  if (/[^\x00-\x7f]/.test(target)) {
    try { target = new TextDecoder('gbk').decode(Buffer.from(target, 'latin1')); } catch { return undefined; }
  }
  return target;
}

function shortcutFolders(depth, dir, found) {
  for (const name of readdir(dir)) {
    const file = path.join(dir, name);
    if (/\.lnk$/i.test(name)) {
      const target = shortcutTarget(file);
      if (target) found.push(path.dirname(target));
    } else if (depth > 1 && isDirectory(file)) shortcutFolders(depth - 1, file, found);
  }
  return found;
}

/** Folders the shortcuts on the desktop and in the Start Menu launch from. This is how an app on another drive is found. */
function shortcutAppFolders(env, home) {
  const { roaming, programData, local } = windowsRoots(env, home);
  const publicDesktop = env.PUBLIC || path.join(path.dirname(home), 'Public');
  const roots = [
    path.join(home, 'Desktop'),
    path.join(publicDesktop, 'Desktop'),
    path.join(roaming, 'Microsoft', 'Windows', 'Start Menu', 'Programs'),
    path.join(programData, 'Microsoft', 'Windows', 'Start Menu', 'Programs'),
    path.join(local, 'Microsoft', 'WindowsApps'),
  ];
  const found = [];
  for (const root of roots) shortcutFolders(2, root, found);
  return found;
}

/** Store apps install under a versioned package folder; the app itself sits in `app`. */
function storeAppFolders(env) {
  const { programFiles } = windowsRoots(env, os.homedir());
  const root = path.join(programFiles, 'WindowsApps');
  return readdir(root)
    .filter(name => STORE_PACKAGES.some(pattern => pattern.test(name)))
    .flatMap(name => [path.join(root, name, 'app'), path.join(root, name)]);
}

/** Apps installed under %LOCALAPPDATA%\Programs, one level deep, plus each app's bundled CLI folder. */
function programsAppFolders(env) {
  const { local } = windowsRoots(env, os.homedir());
  const root = path.join(local, 'Programs');
  return readdir(root).map(name => path.join(root, name)).filter(isDirectory);
}

function nodeRuntimeFolders(env, home) {
  const { local, roaming } = windowsRoots(env, home);
  const versionRoots = [
    path.join(home, '.workbuddy', 'binaries', 'node', 'versions'),
    path.join(home, '.codebuddy', 'binaries', 'node', 'versions'),
    path.join(roaming, 'nvm'),
    path.join(local, 'nvs'),
    path.join(home, '.volta', 'tools', 'image', 'node'),
  ];
  const found = [];
  for (const root of versionRoots) for (const name of readdir(root)) {
    const dir = path.join(root, name);
    found.push(dir, path.join(dir, 'bin'), path.join(dir, 'installation', 'bin'), path.join(dir, 'x64'));
  }
  return found;
}

let cached;
/** The folder list changes only when something is installed, so it is reused for a moment instead of on every row. */
function searchDirs(env = process.env, home = os.homedir()) {
  const key = `${home}\u0000${platform.environmentPath(env)}`;
  if (cached && cached.key === key && Date.now() - cached.at < 30000) return cached.dirs;
  const { local, roaming } = windowsRoots(env, home);
  const dirs = [];
  const add = dir => { if (dir && path.isAbsolute(dir) && !dirs.includes(dir)) dirs.push(dir); };
  if (process.platform === 'win32') {
    for (const dir of platform.environmentPath(env).split(path.delimiter)) add(dir);
    add(path.join(roaming, 'npm'));
    add(path.join(local, 'pnpm'));
    add(path.join(local, 'Yarn', 'bin'));
    add(path.join(home, '.local', 'bin'));
    add(path.join(home, '.npm-global', 'bin'));
    // Grok installs itself under ~/.grok/bin and never touches PATH.
    add(path.join(home, '.grok', 'bin'));
    add(path.join(home, '.bun', 'bin'));
    add(path.join(home, 'scoop', 'shims'));
    add(path.join(home, '.claude', 'local'));
    add(path.join(local, 'Microsoft', 'WindowsApps'));
    for (const dir of nodeRuntimeFolders(env, home)) add(dir);
    const apps = [...programsAppFolders(env), ...storeAppFolders(env), ...shortcutAppFolders(env, home)];
    for (const app of apps) {
      add(app);
      for (const relative of BUNDLED_CLI_RELATIVE) add(path.join(app, ...relative));
    }
  } else {
    for (const dir of platform.environmentPath(env).split(path.delimiter)) add(dir);
    for (const dir of ['/opt/homebrew/bin', '/usr/local/bin', '/usr/bin']) add(dir);
    for (const dir of ['.local/bin', '.npm-global/bin', '.bun/bin', '.claude/local', '.volta/bin', '.local/share/ego/active_version_dir']) add(path.join(home, dir));
    for (const dir of nodeRuntimeFolders(env, home)) add(dir);
  }
  cached = { key, at: Date.now(), dirs };
  return dirs;
}

/** Test seam: runs on a synthetic machine without waiting for the cache to expire. */
searchDirs.reset = () => { cached = undefined; };

module.exports = { searchDirs, WINDOWS_EXTENSIONS, shortcutTarget };
