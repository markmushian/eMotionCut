import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const scratchDirs=new Map<string,string|undefined>();

/** 每次探测命令行时都要换到一个空目录，避免工程里的配置影响回答。
 *  以前每个探测都新建并在结束时删掉临时目录：命令行刚退出时 Windows 仍握着该目录的句柄，
 *  删除会抛 EPERM/EBUSY，而这个异常又会把整份智能体列表打空。
 *  现在每个标签复用一个固定目录：不删就不会有删除失败，不同智能体之间也不串配置。 */
export function probeCwd(label:string,tempRoot?:string):string|undefined{
 const root=tempRoot??os.tmpdir(),key=`${root}|${label}`,cached=scratchDirs.get(key);
 if(cached&&isDirectory(cached))return cached;
 const dir=path.join(root,'emotioncut-probe',label.replace(/[^a-zA-Z0-9._-]/g,'').slice(0,40)||'agent');
 let value:string|undefined;
 try{fs.mkdirSync(dir,{recursive:true});if(isDirectory(dir))value=dir}catch{value=undefined}
 scratchDirs.set(key,value);return value;
}
function isDirectory(value:string){try{return fs.statSync(value).isDirectory()}catch{return false}}
