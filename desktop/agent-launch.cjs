const path = require('node:path');
const fs = require('node:fs');
const {randomUUID} = require('node:crypto');

// Windows cannot start a JavaScript file directly, and some agent CLIs ship as one
// (WorkBuddy's `codebuddy`, ZCode's `zcode.cjs`). They run on the Node runtime that
// is already here, exactly as their own `npm` shim would run them.
function isScript(file) {
 if (/\.(?:cjs|mjs|js)$/i.test(file)) return true;
 if (path.extname(file)) return false;
 try { return /^#!.*\bnode\b/.test(fs.readFileSync(file, 'utf8').slice(0, 128)); } catch { return false; }
}

// cmd parses a batch invocation once, then a typical npm shim expands %*.
// Protect both passes. Percent signs travel through a private, single-pass
// environment expansion so names such as %PATH% remain literal data.
const meta = /[()[\]%!^"`<>&|;, *?]/g;
const caret = value => value.replace(meta, char => '^' + char);
function quoteArgument(value) {
 let result = '"', slashes = 0;
 for (const char of value) {
  if (char === '\\') { slashes++; continue; }
  result += '\\'.repeat(char === '"' ? slashes * 2 + 1 : slashes) + char;
  slashes = 0;
 }
 return result + '\\'.repeat(slashes * 2) + '"';
}
function agentLaunch(file, args = [], options = {}, platform = process.platform) {
 if (platform !== 'win32') return {file, args: [...args], options: {...options, shell: false}};
 if (!/\.(cmd|bat)$/i.test(file) && isScript(file)) {
  const env = {...(options.env || process.env), ELECTRON_RUN_AS_NODE: '1'};
  return {file: process.execPath, args: [file, ...args], options: {...options, env, shell: false}};
 }
 if (!/\.(cmd|bat)$/i.test(file)) return {file, args: [...args], options: {...options, shell: false}};
 if (typeof file !== 'string' || [file, ...args].some(value => typeof value !== 'string' || /[\r\n\0]/.test(value))) throw Error('程序路径和命令参数不能包含换行或空字符');
 const env = {...(options.env || process.env)};
 const percent = 'EMOTIONCUT_LITERAL_' + randomUUID().replaceAll('-', '');
 env[percent] = '%';
 const protectPercent = value => value.replaceAll('%', '%' + percent + '%');
 const command = [protectPercent(caret(path.win32.normalize(file))), ...args.map(value => protectPercent(caret(caret(quoteArgument(value)))))].join(' ');
 const systemRoot = Object.entries(env).find(([key]) => key.toLowerCase() === 'systemroot')?.[1] || 'C:\\Windows';
 return {file: path.win32.join(systemRoot, 'System32', 'cmd.exe'), args: ['/d', '/s', '/v:off', '/c', '"' + command + '"'], options: {...options, env, shell: false, windowsVerbatimArguments: true}};
}
module.exports = {agentLaunch, quoteArgument};
