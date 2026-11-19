const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {searchDirs, WINDOWS_EXTENSIONS, shortcutTarget} = require('./agent-locations.cjs');
const {agentLaunch} = require('./agent-launch.cjs');

/** A machine on disk: only the folders that matter are created, so a missing one proves nothing was invented. */
function machine() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'emotioncut-locations-'));
  const at = (...parts) => {
    const dir = path.join(root, ...parts);
    fs.mkdirSync(dir, {recursive: true});
    return dir;
  };
  const env = {
    LOCALAPPDATA: at('Local'),
    APPDATA: at('Roaming'),
    ProgramFiles: at('PF'),
    ProgramData: at('ProgramData'),
    PUBLIC: at('Public'),
    PATH: [at('PathBin')].join(path.delimiter),
  };
  return {root, at, env, home: at('Home')};
}

test('a Store app, an app folder and a bundled CLI are all searched', () => {
  const {at, env, home, root} = machine();
  at('PF', 'WindowsApps', 'OpenAI.Codex_26.1103.7773.0_x64__2p2nqsd0c76g0', 'app', 'resources');
  at('PF', 'WindowsApps', 'Microsoft.WindowsCalculator_11.0_x64__8wekyb3d8bbwe', 'app');
  at('Local', 'Programs', 'WorkBuddy', 'resources', 'app.asar.unpacked', 'cli', 'bin');
  at('Local', 'Programs', 'DeepSeek Harness', 'resources', 'runtime', 'cli', 'bin');
  searchDirs.reset();
  const dirs = searchDirs(env, home);
  assert.ok(dirs.includes(path.join(env.PATH)), 'the login PATH comes first');
  assert.ok(dirs.some(dir => dir.includes(path.join('OpenAI.Codex_26.1103.7773.0_x64__2p2nqsd0c76g0', 'app', 'resources'))), 'the Store package keeps codex.exe in app/resources');
  assert.ok(!dirs.some(dir => dir.includes('WindowsCalculator')), 'only packages that carry an agent are scanned');
  assert.ok(dirs.includes(path.join(root, 'Local', 'Programs', 'WorkBuddy', 'resources', 'app.asar.unpacked', 'cli', 'bin')));
  assert.ok(dirs.includes(path.join(root, 'Local', 'Programs', 'DeepSeek Harness', 'resources', 'runtime', 'cli', 'bin')));
  fs.rmSync(root, {recursive: true, force: true});
});

test('an app on another drive is found through the shortcut that launches it', () => {
  const {at, env, home, root} = machine();
  // The ANSI local base path of a .lnk is written in the machine's code page: GBK here, so 豆包 is B6B9 B0FC.
  const target = Buffer.concat([Buffer.from('E:\\', 'latin1'), Buffer.from([0xb6, 0xb9, 0xb0, 0xfc]), Buffer.from('\\Zcode\\ZCode.exe', 'latin1')]);
  const lnk = path.join(at('Home', 'Desktop'), 'ZCode.lnk');
  fs.writeFileSync(lnk, Buffer.concat([Buffer.alloc(76), target, Buffer.alloc(8)]));
  assert.equal(shortcutTarget(lnk), 'E:\\豆包\\Zcode\\ZCode.exe');
  at('Local', 'Programs');
  searchDirs.reset();
  const dirs = searchDirs(env, home);
  assert.ok(dirs.includes(path.join('E:\\豆包\\Zcode', 'resources', 'glm')), 'ZCode keeps its command in resources/glm');
  fs.rmSync(root, {recursive: true, force: true});
});

test('a broken or foreign shortcut is ignored instead of guessed', () => {
  const {at, env, home, root} = machine();
  const lnk = path.join(at('Home', 'Desktop'), 'Broken.lnk');
  fs.writeFileSync(lnk, Buffer.from('not a shortcut', 'latin1'));
  assert.equal(shortcutTarget(lnk), undefined);
  assert.equal(shortcutTarget(path.join(root, 'Home', 'Desktop', 'missing.lnk')), undefined);
  searchDirs.reset();
  assert.ok(Array.isArray(searchDirs(env, home)));
  fs.rmSync(root, {recursive: true, force: true});
});

test('a command may be an exe, a batch shim, a bare script or a Node entry file', () => {
  assert.deepEqual(WINDOWS_EXTENSIONS.slice(0, 3), ['.exe', '.cmd', '.bat']);
  assert.ok(WINDOWS_EXTENSIONS.includes(''), 'WorkBuddy ships an extension-less codebuddy');
  assert.ok(WINDOWS_EXTENSIONS.includes('.cjs'), 'ZCode ships zcode.cjs');
});

test('a JavaScript command runs on the Node runtime that is already here', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'emotioncut-script-'));
  const script = path.join(dir, 'zcode.cjs');
  fs.writeFileSync(script, "console.log(process.argv.slice(2).join(','))\n");
  const launch = agentLaunch(script, ['--version'], {}, 'win32');
  assert.equal(launch.file, process.execPath);
  assert.deepEqual(launch.args, [script, '--version']);
  assert.equal(launch.options.env.ELECTRON_RUN_AS_NODE, '1');
  const exe = agentLaunch('C:\\Tools\\codex.exe', ['--version'], {}, 'win32');
  assert.equal(exe.file, 'C:\\Tools\\codex.exe', 'an exe is started directly');
  fs.rmSync(dir, {recursive: true, force: true});
});
