import type {GeneralLength} from './general-contract'

export type ScriptIssue = {id:string; label:string; sample:string; advice:string}
export type ScriptAnalysis = {
  units:number; estimatedSeconds:number; targetSeconds?:number; minUnits?:number; maxUnits?:number
  fit:'empty'|'auto'|'short'|'good'|'long'; issues:ScriptIssue[]
}

const rules:{id:string;label:string;pattern:RegExp;advice:string}[] = [
  {id:'padding',label:'空泛铺垫',pattern:/让我们(?:深入|来看|一起|聊聊)|接下来让我们|以下是你需要知道的|话不多说|废话不多说/g,advice:'直接进入第一条信息。'},
  {id:'ai-words',label:'AI 高频词',pattern:/赋能|至关重要|深入探讨|无缝(?:衔接|对接)?|闭环|在当今|值得注意的是?|需要指出|综上所述|众所周知/g,advice:'换成具体动作、结果或数字。'},
  {id:'promotion',label:'宣传腔',pattern:/堪称|可谓|不得不说|完美的?选择|梦想(?:天堂|之选)|天花板/g,advice:'改成有依据的判断，主观感受就写“我觉得”。'},
  {id:'service',label:'客服式收尾',pattern:/好问题|希望(?:这|对)您?(?:有|有所)?帮助|如有(?:疑问|需要)|感谢(?:您的)?(?:阅读|观看)|以上就是(?:我的)?分享/g,advice:'删掉问候和结束套话，留下实际下一步。'},
  {id:'background',label:'背景套话',pattern:/随着.{1,14}的(?:发展|进步|到来|普及)|在.{1,12}的时代背景下/g,advice:'背景没有独立信息就压缩掉。'},
  {id:'ending',label:'套话收尾',pattern:/总而言之|让我们一起|让我们拭目以待|期待(?:更多|未来)|共勉|以上就是全部/g,advice:'用结论、行动或一个具体问题收尾。'},
  {id:'process',label:'“进行＋动词”',pattern:/进行.{1,4}(?:测试|调整|优化|处理|分析|修改|讲解)/g,advice:'直接使用动词，例如“进行测试”改成“测试”。'},
]

export function spokenUnits(text:string){return (text.match(/[\u3400-\u9fff]|[A-Za-z0-9]+/g)??[]).length}

export function buildScriptOutline(input:string){
  const topic=(input.trim().split(/[。！？\n]/)[0]||'这期主题').slice(0,42)
  return [
    `【开场钩子】用一个具体结果、冲突或问题切入：${topic}`,
    '【观众处境】他们现在卡在哪里？只写真实、常见的场景。',
    '【先给结论】用一句话说清你的核心判断。',
    '【第一步】说明第一个动作，并补一个真实例子。',
    '【第二步】说明第二个动作；涉及数字时填写【待补充：来源与数据】。',
    '【避坑提醒】指出最容易做错的一点，以及会造成什么结果。',
    '【你的证据】补充亲历、案例或可核实来源；没有就保留【待补充】。',
    '【收尾行动】给观众一个现在就能完成的具体动作。',
  ].join('\n\n')
}

export function analyzeScript(text:string,length:GeneralLength|90|180):ScriptAnalysis{
  const units=spokenUnits(text),estimatedSeconds=Math.round(units/4)
  const issues=rules.flatMap(rule=>{rule.pattern.lastIndex=0;const match=rule.pattern.exec(text);return match?[{id:rule.id,label:rule.label,sample:match[0].slice(0,42),advice:rule.advice}]:[]})
  if(!units)return {units,estimatedSeconds,fit:'empty',issues}
  if(length==='auto')return {units,estimatedSeconds,fit:'auto',issues}
  const targetSeconds=length,target=targetSeconds*4,minUnits=Math.max(1,Math.floor(target*.95)-2),maxUnits=Math.ceil(target*1.08)
  const fit=units<minUnits?'short':units>maxUnits?'long':'good'
  return {units,estimatedSeconds,targetSeconds,minUnits,maxUnits,fit,issues}
}
