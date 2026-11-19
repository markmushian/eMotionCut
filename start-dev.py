"""One-click launcher for the eMotionCut dev build.

Windows refuses to create a process when the environment carries the same key
twice in different cases (HTTP_PROXY vs http_proxy), and a stray
ELECTRON_RUN_AS_NODE turns the app into a headless node shell. Both are fixed
here so the app always starts the same way.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ELECTRON = ROOT / 'node_modules' / 'electron' / 'dist' / 'electron.exe'
NODE_DIR = Path(r'E:\AI小程序\tools\node24')
LOG = ROOT / '.dev' / 'launch.log'


def clean_env():
    env = {}
    seen = set()
    for key, value in os.environ.items():
        low = key.lower()
        if low in seen:
            continue
        seen.add(low)
        env[key] = value
    env.pop('ELECTRON_RUN_AS_NODE', None)
    env.pop('NODE_OPTIONS', None)
    env['PATH'] = str(NODE_DIR) + os.pathsep + env.get('PATH', '')
    return env


def main():
    if not ELECTRON.exists():
        sys.exit(f'未找到 Electron：{ELECTRON}\n先运行 npm ci 安装依赖。')

    LOG.parent.mkdir(parents=True, exist_ok=True)
    handle = open(LOG, 'wb')
    # Detach so the app outlives the shell that launched it. Without this the app
    # dies with its parent whenever it is started from a script or a task runner.
    flags = 0
    if os.name == 'nt':
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(
        [str(ELECTRON), '.'],
        cwd=str(ROOT),
        env=clean_env(),
        stdin=subprocess.DEVNULL,
        stdout=handle,
        stderr=subprocess.STDOUT,
        creationflags=flags,
        close_fds=True,
    )
    print(f'eMotionCut 已启动（PID {proc.pid}）')
    print(f'日志：{LOG}')


if __name__ == '__main__':
    main()
