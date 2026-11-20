import {checkCreationEnvironment} from './creation-tools.ts'
import {getConnectionConfig} from './agent-connections.ts'
import {connectionDefinition} from '../shared/agent-connections.ts'

/** 云端服务接好（enabled + 测试通过）才算就绪；配置损坏按未接通处理。 */
function serviceConnected(id:'dreamina'|'minimax-speech'|'hailuo'){
  try{const config=getConnectionConfig(id);return !!(config.enabled&&config.connected)}catch{return false}
}

export async function creatorProductionReadiness(){
  const local=await checkCreationEnvironment()
  const voiceReady=serviceConnected('minimax-speech')
  const avatarReady=serviceConnected('hailuo')
  const anyReady=voiceReady||avatarReady||serviceConnected('dreamina')
  const capabilities=[
    {id:'voice-clone',label:'克隆声音',detail:'用 20—30 秒清晰人声生成整篇口播配音',connected:voiceReady,guide:'系统设置 → 账号与服务接入 → MiniMax 语音'},
    {id:'talking-avatar',label:'口型与动作驱动',detail:'让本人样本或数字人按稿件完成口型和自然动作',connected:avatarReady,guide:'系统设置 → 账号与服务接入 → MiniMax 视频 · 海螺'},
    {id:'cloud-storage',label:'云端任务与成片回传',detail:'长任务在云端运行，本机不需要高性能显卡',connected:anyReady,guide:'任一云端服务接通后可用'},
  ]
  const provider:('dreamina'|'minimax-speech'|'hailuo')|undefined=voiceReady?'minimax-speech':avatarReady?'hailuo':undefined
  return {
    ok:true,
    checkedAt:new Date().toISOString(),
    local,
    cloud:{
      connected:anyReady,
      provider:provider?connectionDefinition(provider).name:null,
      capabilities,
      privacy:'声音与人像属于敏感素材。连接云端前会单独说明上传内容、保存期限和删除方式。',
    },
    modes:[
      {id:'real',name:'真人完整口播',recommended:true,needsCloud:false,summary:'你完成出镜口播，Agent 负责气口、节奏、素材、字幕和 Motion 包装'},
      {id:'hybrid',name:'真人开头，云端续说',recommended:false,needsCloud:true,summary:'录制约 20 秒本人样本，云端补齐克隆声音、口型和动作'},
      {id:'avatar',name:'全程数字人口播',recommended:false,needsCloud:true,summary:'用人物素材和完整稿件生成数字人口播，再交给 Agent 导演剪辑'},
    ],
  }
}
