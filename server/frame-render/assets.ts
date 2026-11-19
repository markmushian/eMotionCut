import fs from 'node:fs';
import path from 'node:path';
import {staticAssetRoot} from '../static-assets.ts';
export async function readFrameAsset(bundle:string,pathname:string,publicRoot=staticAssetRoot()) {
  const relative=decodeURIComponent(pathname).replace(/^\//,'')||'index.html';
  const base=relative.startsWith('public/')||relative.startsWith('semantic-icons/')?publicRoot:bundle;
  const file=path.resolve(base,relative.replace(/^public\//,''));
  if(!file.startsWith(path.resolve(base)+path.sep))throw Error('Invalid asset path');
  const ext=path.extname(file).toLowerCase();
  const contentType=({'.html':'text/html','.js':'text/javascript','.css':'text/css','.png':'image/png','.svg':'image/svg+xml','.jpg':'image/jpeg','.jpeg':'image/jpeg','.woff2':'font/woff2','.ttf':'font/ttf','.ttc':'font/collection','.wav':'audio/wav','.mp3':'audio/mpeg','.ogg':'audio/ogg'} as Record<string,string>)[ext]||'application/octet-stream';
  try{return {body:await fs.promises.readFile(file),contentType}}
  catch(error){
    if((error as NodeJS.ErrnoException).code!=='ENOENT')throw error;
    if(['.wav','.mp3','.ogg','.m4a'].includes(ext))return {body:silentWav(),contentType:'audio/wav'};
    if(process.platform==='win32'&&['.ttf','.ttc','.otf'].includes(ext)){
      const installed=path.join(process.env.WINDIR||'C:/Windows','Fonts',path.basename(file));
      if(fs.existsSync(installed))return {body:await fs.promises.readFile(installed),contentType};
    }
    throw error;
  }
}

function silentWav(){
  const body=Buffer.alloc(44+1600);body.write('RIFF',0);body.writeUInt32LE(body.length-8,4);body.write('WAVEfmt ',8);body.writeUInt32LE(16,16);body.writeUInt16LE(1,20);body.writeUInt16LE(1,22);body.writeUInt32LE(16000,24);body.writeUInt32LE(32000,28);body.writeUInt16LE(2,32);body.writeUInt16LE(16,34);body.write('data',36);body.writeUInt32LE(body.length-44,40);return body;
}
