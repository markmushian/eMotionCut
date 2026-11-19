import test from 'node:test';
import assert from 'node:assert/strict';
import {headlessTurnArgs,filmAgentArgs,filmAgentId,applyGrokEvent,applyZcodeEvent,applyFilmAgentEvent} from './general/agent-adapter.ts';
import {isHeadlessFilmAgent,HEADLESS_FILM_IDS,connectionDefinition} from '../shared/agent-connections.ts';
import {headlessAnswered,codebuddyBundleEnv} from './agent-connections.ts';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const turn={work:'E:\\work',lastMessage:'E:\\work\\last.txt',network:false,model:'default',effort:'none'};

test('WorkBuddy、ZCode、Grok 走无头出片，提示词只出现一次且在各自的位置',()=>{
 for(const id of HEADLESS_FILM_IDS){assert.equal(isHeadlessFilmAgent(id),true);assert.equal(connectionDefinition(id).headless,true,'这家必须声明 headless');}
 const wb=headlessTurnArgs('codebuddy',turn,'写一个分镜',['--port','61234']);
 assert.equal(wb[0],'-p');
 assert.equal(wb.at(-1),'写一个分镜');
 assert.equal(wb.filter(arg=>arg==='写一个分镜').length,1);
 assert.ok(wb.includes('Bash'),'WorkBuddy 必须能执行工程制作命令');
 assert.ok(wb.includes('Bash(*)'),'音频生成需要放行 Bash 中的子命令');
 assert.ok(wb.includes('PowerShell'),'音频与字体脚本需要 Windows PowerShell 工具');
 assert.ok(wb.includes('PowerShell(*)'),'音频生成需要放行 PowerShell 脚本参数');
 assert.ok(wb.includes('--settings'),'权限仅注入当前会话，不写入 WorkBuddy 全局设置');
 assert.ok(!wb.includes('-y')&&!wb.includes('--dangerously-skip-permissions')&&!wb.includes('bypassPermissions'),'不能跳过全部权限');
 // ZCode and Grok: `-p` takes the prompt as its value, so it must sit immediately after it.
 for(const id of ['zcode','grok'] as const){
  const args=headlessTurnArgs(id,turn,'写一个分镜');
  assert.deepEqual(args.slice(0,2),['-p','写一个分镜']);
  assert.equal(args.filter(arg=>arg==='写一个分镜').length,1);
  assert.ok(args.includes('--cwd'),'这两家不跟随进程工作目录，必须显式给 --cwd');
  assert.equal(args[args.indexOf('--cwd')+1],turn.work);
 }
 assert.ok(headlessTurnArgs('grok',turn,'x').includes('--always-approve'),'无头模式不能停在工具授权上');
 assert.ok(headlessTurnArgs('zcode',turn,'x').includes('yolo'));
});

test('无头 CLI 出片参数由 filmAgentArgs 统一生成',()=>{
 assert.equal(filmAgentId({agent:'grok'}),'grok');
 assert.equal(filmAgentId({agent:'zcode'}),'zcode');
 assert.equal(filmAgentId({agent:'codebuddy'}),'codebuddy');
 assert.equal(filmAgentArgs({agent:'codebuddy',executable:'x',model:'default',effort:'none',args:['--port','61234'],transport:'headless'},turn,'提示词').at(-1),'提示词');
 const args=filmAgentArgs({agent:'grok',executable:'x',model:'default',effort:'none'},turn,'提示词');
 assert.deepEqual(args,headlessTurnArgs('grok',turn,'提示词'));
});

test('Grok 的 text 行成为正文，thought 与指令清单不进正文',()=>{
 const thought=applyGrokEvent([],{type:'thought',data:'先想一下'},'t');
 assert.equal(thought.message,undefined);
 const commands=applyGrokEvent([],{type:'available_commands',tools:[],commands:[]},'t');
 assert.equal(commands.message,undefined);
 const text=applyGrokEvent([],{type:'text',data:'可以'},'t');
 assert.equal(text.message,'可以');
 const failed=applyGrokEvent([],{type:'error',error:{message:'额度用完了'}},'t');
 assert.equal(failed.error,'额度用完了');
 assert.equal(applyGrokEvent([],{type:'result'},'t').turnDone,true);
});

test('ZCode 的 JSON 流没有文档，正文按已知字段宽容读取，报错直接暴露原因',()=>{
 assert.equal(applyZcodeEvent([],{type:'assistant',text:'写好了'},'t').message,'写好了');
 assert.equal(applyZcodeEvent([],{type:'assistant',delta:{text:'分镜'}},'t').message,'分镜');
 // A user/system line must never be mistaken for the agent's answer.
 assert.equal(applyZcodeEvent([],{type:'user',text:'原始要求'},'t').message,undefined);
 const failed=applyZcodeEvent([],{type:'error',error:{message:'Insufficient balance'}},'t');
 assert.equal(failed.error,'Insufficient balance');
 assert.equal(applyZcodeEvent([],{type:'result'},'t').turnDone,true);
});

test('事件分派把 WorkBuddy 归到 Claude 兼容格式，ZCode 与 Grok 保留自己的流格式',()=>{
 const wb=applyFilmAgentEvent('codebuddy',[],{type:'result',subtype:'success',result:'完成'},'t');
 assert.equal(wb.turnDone,true);
 assert.equal(wb.message,'完成');
 assert.equal(applyFilmAgentEvent('grok',[],{type:'text',data:'好'},'t').message,'好');
 assert.equal(applyFilmAgentEvent('zcode',[],{type:'assistant',text:'好'},'t').message,'好');
});

// 登录检查只认「真的答了话」：WorkBuddy 的 json 模式会先回显提问，那不是回复。
test('无头登录检查只把真正的回答当成接通证据',()=>{
 const echo='{"type":"message","role":"user","content":[{"type":"input_text","text":"只回复两个字：可以"}]}';
 assert.equal(headlessAnswered('codebuddy',echo),false,'回显的提问不算回答');
 assert.equal(headlessAnswered('codebuddy','{"type":"result","subtype":"success","result":"可以"}'),true);
 assert.equal(headlessAnswered('codebuddy','{"type":"assistant","message":{"content":[{"type":"text","text":"可以"}]}}'),true);
 assert.equal(headlessAnswered('grok','{"type":"text","data":"可以"}'),true);
 assert.equal(headlessAnswered('grok','{"type":"thought","data":"想一想"}'),false);
 assert.equal(headlessAnswered('zcode','{"type":"user","text":"只回复两个字：可以"}'),false);
});

// WorkBuddy 的 launcher 用环境变量挑 bundle；挑错会 1 秒退出并报 Cannot find module。
test('WorkBuddy 按实际存在的产物选择 bundle，并清掉父进程泄漏的会话身份',()=>{
 const cli=fs.mkdtempSync(path.join(os.tmpdir(),'emotioncut-cli-'));
 fs.mkdirSync(path.join(cli,'bin'),{recursive:true});fs.mkdirSync(path.join(cli,'dist'),{recursive:true});
 const executable=path.join(cli,'bin','codebuddy');fs.writeFileSync(executable,'');
 const leaked={CODEBUDDY_SESSION_ID:'x',CODEBUDDY_CONVERSATION_ID:'y',CODEBUDDY_SERVICE_PROXY_URL:'http://127.0.0.1:1'};
 const empty=codebuddyBundleEnv(executable,leaked);
 assert.equal(empty.CODEBUDDY_FORCE_LITE_WB_BUNDLE,undefined,'没有产物就不要乱设');
 assert.equal(empty.CODEBUDDY_SESSION_ID,undefined,'父会话身份不能带进子进程');
 assert.equal(empty.CODEBUDDY_SERVICE_PROXY_URL,'http://127.0.0.1:1','它自己的网关地址要保留');
 fs.writeFileSync(path.join(cli,'dist','codebuddy-lite-wb.mjs'),'');
 assert.equal(codebuddyBundleEnv(executable,leaked).CODEBUDDY_FORCE_LITE_WB_BUNDLE,'1');
 fs.rmSync(cli,{recursive:true,force:true});
});
