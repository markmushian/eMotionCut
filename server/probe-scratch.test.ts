import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {probeCwd} from './probe-scratch.ts';

function root(){return fs.mkdtempSync(path.join(os.tmpdir(),'emotioncut-scratch-'))}

test('探测目录：同一个标签复用同一个目录，不同智能体互不干扰',()=>{
 const dir=root();
 try{
  const first=probeCwd('codex',dir),again=probeCwd('codex',dir),other=probeCwd('grok',dir);
  assert.ok(first&&other);
  assert.equal(first,again);
  assert.notEqual(first,other);
  assert.ok(fs.statSync(first!).isDirectory());
  assert.ok(fs.statSync(other!).isDirectory());
 }finally{fs.rmSync(dir,{recursive:true,force:true})}
});

test('探测目录：目录不会被删除，命令行刚退出时也不会留下 EPERM',()=>{
 const dir=root();
 try{
  const first=probeCwd('auth-claude',dir);
  // 旧实现每次探测后都 rmSync：命令行还握着句柄时删除抛 EPERM，异常会把整份智能体列表打空。
  for(let i=0;i<3;i++)assert.equal(probeCwd('auth-claude',dir),first);
  assert.equal(fs.existsSync(first!),true);
 }finally{fs.rmSync(dir,{recursive:true,force:true})}
});

test('探测目录：建不出来就返回 undefined，调用方退回家目录而不是报错',()=>{
 const dir=root(),file=path.join(dir,'not-a-directory');
 fs.writeFileSync(file,'x');
 try{assert.equal(probeCwd('blocked',file),undefined)}finally{fs.rmSync(dir,{recursive:true,force:true})}
});

test('探测目录：标签里的路径分隔符不会跑出临时目录',()=>{
 const dir=root();
 try{
  const value=probeCwd('../../etc/passwd',dir);
  assert.ok(value);
  assert.ok(value!.startsWith(path.join(dir,'emotioncut-probe')));
 }finally{fs.rmSync(dir,{recursive:true,force:true})}
});
