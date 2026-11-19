/**
 * 设置 › 已接入的 Agent 里那张小图，必须是厂商自己的图标。
 *
 * 这里踩过两次坑，两条都守着：
 * 1. 除 WorkBuddy 和 ZCode 外，其余都是手描的单路径，Grok 画成字母 X、Hermes 画成字母 H；
 * 2. Claude Code 用的是命令行里的像素小人，Copilot / Cursor / OpenCode / Qwen 四家干脆
 *    没有图，界面上只剩 GH、C、OC、Q 两个字母。
 * 现在 13 家全部走 agent-icons.ts 里的官方图，清单里少一家就报错。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import {AGENT_ICONS} from '../src/studio/agent-icons.ts';
import {AGENT_IMAGES,AGENT_MARKS,type AgentId} from '../src/studio/agent-marks.ts';
import {AGENT_CONNECTION_IDS} from '../shared/agent-connections.ts';

/** 有厂商图的智能体：接入清单里的 13 家全都得有。 */
const VENDOR_AGENTS:AgentId[]=[...AGENT_CONNECTION_IDS];

/** 「claudecode」那张像素小图（命令行里的彩蛋形象），不能拿来当设置里的 Claude 图标。 */
const CLAUDE_CODE_PIXEL_SHA256='521c0d3f127cc8bc0c299753715fd5857e7743a510d4f5623336a446cb3b70da';

const DATA_URI=/^data:image\/(png|svg\+xml);base64,([A-Za-z0-9+/=]+)$/;

test('接入清单里的 13 家每家都有厂商官方图标，不再拿字母顶替',()=>{
 assert.equal(VENDOR_AGENTS.length,13,'接入清单的智能体数量变了，先核对这里的预期');
 for(const id of VENDOR_AGENTS){
  const uri=AGENT_IMAGES[id];
  assert.ok(uri,`${id} 没有官方图标，界面上只会显示两个字母`);
  assert.match(uri,DATA_URI,`${id} 的图标不是合法的 data URI`);
 }
 assert.equal(Object.keys(AGENT_ICONS).length,VENDOR_AGENTS.length);
 for(const id of Object.keys(AGENT_ICONS)as AgentId[])assert.ok(VENDOR_AGENTS.includes(id),`多出未登记的图标 ${id}`);
});

test('图标是真图：能解码，PNG 头正确，尺寸够显示',()=>{
 for(const id of VENDOR_AGENTS){
  const match=AGENT_IMAGES[id]!.match(DATA_URI)!;
  const bytes=Buffer.from(match[2],'base64');
  assert.ok(bytes.length>800,`${id} 的图标只有 ${bytes.length} 字节，可能是空占位`);
  assert.ok(bytes.length<40_000,`${id} 的图标 ${bytes.length} 字节，内联进前端太重`);
  if(match[1]==='png'){
   assert.deepEqual([...bytes.subarray(0,8)],[0x89,0x50,0x4e,0x47,0x0d,0x0a,0x1a,0x0a],`${id} 不是 PNG`);
   // IHDR 宽高：8 字节签名 + 4 长度 + 4 类型，紧接 width/height
   const width=bytes.readUInt32BE(16),height=bytes.readUInt32BE(20);
   assert.ok(width>=40&&height>=40,`${id} 只有 ${width}×${height}，放到 40px 的格子里会糊`);
   assert.equal(bytes.readUInt32BE(16),bytes.readUInt32BE(20),`${id} 不是正方形`);
  }
 }
});

test('各家的图不是同一张，避免模板套错',()=>{
 const unique=new Set(VENDOR_AGENTS.map(id=>AGENT_IMAGES[id]!));
 assert.equal(unique.size,VENDOR_AGENTS.length);
});

test('Claude 用厂商官标，不是命令行那个像素小人',()=>{
 const bytes=Buffer.from(AGENT_IMAGES.claude!.split(',')[1],'base64');
 const sha=crypto.createHash('sha256').update(bytes).digest('hex');
 assert.notEqual(sha,CLAUDE_CODE_PIXEL_SHA256,'Claude 又变回 claudecode 那张像素图了');
});

test('openscreen 没有厂商图，仍靠自带路径兜底',()=>{
 assert.equal(AGENT_IMAGES.openscreen,undefined);
 assert.match(AGENT_MARKS.openscreen!.d,/^M/);
 assert.equal(AGENT_MARKS.openscreen!.color,'#04C442');
});
