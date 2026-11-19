import net from 'node:net';

/**
 * WorkBuddy's CLI always binds a local port, and its own desktop app already holds the stable one
 * (127.0.0.1:62649). Asking the OS for a free port keeps both running; `--port 0` is not honoured
 * by every release, so the port is resolved here and passed explicitly.
 */
export function freePort():Promise<number>{
  return new Promise((resolve,reject)=>{
    const server=net.createServer();
    server.unref();
    server.once('error',reject);
    server.listen(0,'127.0.0.1',()=>{
      const address=server.address();
      const port=typeof address==='object'&&address?address.port:0;
      server.close(()=>port?resolve(port):reject(Error('没有可用的本地端口')));
    });
  });
}
