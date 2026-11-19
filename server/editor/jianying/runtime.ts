import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {fileURLToPath} from 'node:url';
import {runExportTool} from '../../export-diagnostics.ts';
import {execFileSync} from 'node:child_process';

type JianyingRuntime = {python:string;packages:string};

/**
 * The draft builder needs two things: the embedded Python and the pyJianYingDraft package tree.
 * The installer sets EMOTIONCUT_JIANYING_PYTHON/PACKAGES (desktop/bundled-runtimes.cjs). A source
 * build or an unpacked folder carries the same resources/runtimes tree without those variables,
 * so look there too instead of refusing to export. Every candidate is checked on disk first.
 */
function runtimeCandidates(): JianyingRuntime[] {
 const list: JianyingRuntime[] = [];
 const add = (python?: string, packages?: string) => {
  if (python && packages) list.push({python, packages});
 };
 add(process.env.EMOTIONCUT_JIANYING_PYTHON, process.env.EMOTIONCUT_JIANYING_PACKAGES);
 if (process.platform === 'win32') {
  // ELECTRON_RUN_AS_NODE keeps execPath on the app binary, in the app process and in the worker alike.
  const roots = [
   path.join(path.dirname(process.execPath), 'resources', 'runtimes'),
   path.join(process.env.LOCALAPPDATA || '', 'eMotionCut', 'runtimes'),
  ];
  for (const root of roots) add(path.join(root, 'python', 'python.exe'), path.join(root, 'jianying-python'));
 } else {
  const resources = (process as unknown as {resourcesPath?:string}).resourcesPath || path.dirname(process.execPath);
  const bundled = path.join(resources, 'runtimes');
  add(path.join(bundled, 'python', 'bin', 'python3'), path.join(bundled, 'jianying-python'));
  const legacy = path.join(os.homedir(), 'Library', 'Application Support', 'LinkReader', 'jianying-runtime-0.3.0', 'bin', 'python');
  add(legacy, path.join(os.homedir(), 'Library', 'Application Support', 'LinkReader', 'jianying-runtime-0.3.0', 'packages'));
 }
 return list;
}
const usable = (value: JianyingRuntime) => {
 try { return fs.statSync(value.python).isFile() && fs.existsSync(path.join(value.packages, 'pyJianYingDraft', '__init__.py')); }
 catch { return false; }
};
export function jianyingRuntime(): JianyingRuntime {
 const candidates = runtimeCandidates();
 const found = candidates.find(usable);
 if (found) return found;
 const looked = candidates.length ? candidates.map(c => `- ${c.python}`).join('\n') : '- （没有任何候选路径）';
 const message = process.platform === 'win32'
  ? `剪映草稿生成组件缺失：没找到内置 Python 或 pyJianYingDraft。已查找：\n${looked}\n请重新安装完整 eMotionCut Windows 包；安装剪映不会补齐此组件。`
  : `缺少本机剪映导出组件（pyJianYingDraft）。已查找：\n${looked}`;
 throw Object.assign(Error(message), {status: 503});
}
export async function requireJianyingRuntime(signal?:AbortSignal) {
 const runtime=jianyingRuntime();
 try{await runExportTool(runtime.python,['-I','-X','utf8',fileURLToPath(new URL('./runtime-check.py',import.meta.url)),runtime.packages||''],{timeout:20000,maxBuffer:2<<20,signal});}
 catch(error){throw Object.assign(Error(`剪映草稿组件预检失败，尚未开始渲染：${error instanceof Error?error.message:error}`),{status:503})}
 return runtime;
}
/** The existing screen-export reservation API is synchronous. Validate before reserving output. */
export function requireJianyingRuntimeSync() {
 const runtime=jianyingRuntime();
 try{execFileSync(runtime.python,['-I','-X','utf8',fileURLToPath(new URL('./runtime-check.py',import.meta.url)),runtime.packages||''],{timeout:20000,maxBuffer:2<<20,windowsHide:true,encoding:'utf8'});}
 catch(error){throw Object.assign(Error(`剪映草稿组件预检失败，尚未开始渲染：${error instanceof Error?error.message:error}`),{status:503})}
 return runtime;
}
export async function jianyingCapabilities() {
 try{await requireJianyingRuntime();return {available:false,draftBuilderReady:true,warning:process.platform==='win32'?'草稿组件已就绪。导出后保存并退出剪映，运行文件夹中的「导入剪映-Windows.cmd」。':'草稿组件已就绪，导出后使用随包导入脚本。'}}
 catch(error){return {available:false,draftBuilderReady:false,warning:error instanceof Error?error.message:String(error)}}
}
