import {checkCreationEnvironment} from './creation-tools.ts'

const cloudCapabilities = [
  {id:'voice-clone',label:'克隆声音',detail:'用 20—30 秒清晰人声生成整篇口播配音',connected:false},
  {id:'talking-avatar',label:'口型与动作驱动',detail:'让本人样本或数字人按稿件完成口型和自然动作',connected:false},
  {id:'cloud-storage',label:'云端任务与成片回传',detail:'长任务在云端运行，本机不需要高性能显卡',connected:false},
]

export async function creatorProductionReadiness(){
  const local=await checkCreationEnvironment()
  return {
    ok:true,
    checkedAt:new Date().toISOString(),
    local,
    cloud:{connected:false,provider:null,capabilities:cloudCapabilities,privacy:'声音与人像属于敏感素材。连接云端前会单独说明上传内容、保存期限和删除方式。'},
    modes:[
      {id:'real',name:'真人完整口播',recommended:true,needsCloud:false,summary:'你完成出镜口播，Agent 负责气口、节奏、素材、字幕和 Motion 包装'},
      {id:'hybrid',name:'真人开头，云端续说',recommended:false,needsCloud:true,summary:'录制约 20 秒本人样本，云端补齐克隆声音、口型和动作'},
      {id:'avatar',name:'全程数字人口播',recommended:false,needsCloud:true,summary:'用人物素材和完整稿件生成数字人口播，再交给 Agent 导演剪辑'},
    ],
  }
}
