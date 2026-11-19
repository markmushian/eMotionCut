import {spawn, type SpawnOptions} from 'node:child_process';
import {createRequire} from 'node:module';
const launcher = createRequire(import.meta.url)('../desktop/agent-launch.cjs') as typeof import('../desktop/agent-launch.cjs');
export const agentLaunch = launcher.agentLaunch;
export const quoteAgentArgument = launcher.quoteArgument;
/** All agent subprocesses use the same Windows batch boundary. */
export const spawnAgentProcess: typeof spawn = ((file: string, args: string[] = [], options: SpawnOptions = {}) => {
 const launch = agentLaunch(file, args, options);
 return spawn(launch.file, launch.args, launch.options);
}) as typeof spawn;
/**
 * A short read-only call to an agent's command (`--version`, `login status`).
 * Some commands are batch shims or JavaScript entry files, so it goes through the
 * same launcher as a real run; the process is killed when it outstays the timeout.
 */
export function execAgentFile(file: string, args: string[], options: {env?: NodeJS.ProcessEnv; cwd?: string; timeoutMs?: number; signal?: AbortSignal} = {}): Promise<{stdout: string; stderr: string; code: number | null}> {
 return new Promise((resolve, reject) => {
  const launch = agentLaunch(file, args, {env: options.env, cwd: options.cwd, windowsHide: true});
  const child = spawn(launch.file, launch.args, {...launch.options, stdio: ['ignore', 'pipe', 'pipe']});
  let stdout = '', stderr = '', settled = false;
  const finish = (error?: Error) => {
   if (settled) return;
   settled = true;
   clearTimeout(timer);
   options.signal?.removeEventListener('abort', cancel);
   if (error) reject(error); else resolve({stdout, stderr, code: child.exitCode});
  };
  // A cancelled check must not leave the CLI running behind the UI.
  const cancel = () => {
   try { child.kill('SIGKILL'); } catch { /* already gone */ }
   finish(Object.assign(Error('agent command cancelled'), {stdout, stderr, code: null}));
  };
  if (options.signal?.aborted) { cancel(); return; }
  options.signal?.addEventListener('abort', cancel, {once: true});
  const timer = setTimeout(() => {
   try { child.kill('SIGKILL'); } catch { /* already gone */ }
   const error = Object.assign(Error('agent command timed out'), {stdout, stderr, code: null});
   finish(error);
  }, options.timeoutMs ?? 8000);
  child.stdout?.setEncoding('utf8');
  child.stderr?.setEncoding('utf8');
  child.stdout?.on('data', (chunk: string) => { if (stdout.length < 65536) stdout += chunk; });
  child.stderr?.on('data', (chunk: string) => { if (stderr.length < 65536) stderr += chunk; });
  child.once('error', error => finish(Object.assign(error, {stdout, stderr})));
  child.once('close', () => finish());
 });
}
