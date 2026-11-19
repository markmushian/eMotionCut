import fs from 'node:fs'
import path from 'node:path'
import AdmZip from 'adm-zip'
import { readMotionCatalog } from '../motion-library.ts'
import type { StyleInfo } from './brief.ts'

const PREFIX='motion-'
export const isMotionProjectStyle=(id:string)=>id.startsWith(PREFIX)&&/^[a-f0-9]{64}$/.test(id.slice(PREFIX.length))

export function motionProjectTemplate(id:string){
  if(!isMotionProjectStyle(id))return undefined
  return readMotionCatalog().packages.find(item=>item.sha256===id.slice(PREFIX.length))
}

export function motionProjectStyle(id:string):StyleInfo|undefined{
  const item=motionProjectTemplate(id);if(!item)return undefined
  return {id,name_zh:item.template.name||item.fileName.replace(/\.motion$/i,''),input_type:'footage',description_zh:'由 Agent 按 MotionGit 模板契约编排并渲染'}
}

export function prepareMotionProject(projectDir:string,styleId:string){
  const item=motionProjectTemplate(styleId);if(!item)throw Error('Motion 模板不存在或尚未安装')
  const root=path.join(projectDir,'motion'),assistant=path.join(root,'assistant')
  fs.mkdirSync(assistant,{recursive:true})
  fs.copyFileSync(item.path,path.join(root,'template.motion'))
  const zip=new AdmZip(item.path)
  for(const name of ['assistant/motion.mjs','assistant/guide.json']){
    const entry=zip.getEntry(name);if(!entry)throw Error(`Motion 模板缺少 ${name}`)
    fs.writeFileSync(path.join(root,path.basename(name)),entry.getData(),{mode:name.endsWith('.mjs')?0o700:0o600})
  }
  const manifest={sha256:item.sha256,name:item.template.name||item.fileName,layout:item.template.layout,width:item.template.width,height:item.template.height,package:item.fileName}
  fs.writeFileSync(path.join(root,'template.json'),JSON.stringify(manifest,null,2)+'\n',{mode:0o600})
  fs.writeFileSync(path.join(root,'STYLE.md'),`# Motion 模板导演规则

本工程必须使用已经授权并校验的 MotionGit 模板，不得只模仿封面或另做一套普通风格。

模板：**${manifest.name}**（${manifest.layout||'自动'}，${manifest.width||'—'}×${manifest.height||'—'}）

## 必须执行的链路

1. 在工作目录中执行：\`node ../motion/motion.mjs start ../motion/template.motion --summary\`。
2. 读取命令真实输出和 \`../motion/guide.json\`，选择与用户素材相符的 workflow；执行 \`node ../motion/motion.mjs contract ../motion/template.motion <workflow> <controls...>\`，不得猜参数名。
3. 执行 \`node ../motion/motion.mjs new ../motion/template.motion\`，使用它返回的 canonical \`instanceDirectory\`，不要自行假设实例目录。把 BRIEF.md 中的文案、用户上传素材绝对路径和画幅要求写入实例参数；再按 contract 返回的命令执行 \`check-params\`。
4. 执行 \`plan\`，阅读 \`plan/report.json\`；只有素材绑定、节奏和画幅正确时才把 \`plan/review.json\` 的 \`reviewed\` 设为 true。
5. 执行 \`prepare\`、\`preview\`，用 view_image 检查真实预览帧。发现问题必须修改参数或计划，不能跳过预览直接宣称完成。
6. 执行 \`render\` 生成 \`../motion/instance/video.mp4\`，确认文件存在且 ffprobe 可读。
7. 将成片复制到 \`assets/motion-output.mp4\`，按 HF.md 创建一个以该视频为唯一主画面的 HyperFrames 工程，保留声音，片长必须与视频一致；运行 \`hfx check\` 和 \`hfx look\`。
8. REPORT.md 记录所选 workflow、参数文件、预览时间点、成片路径以及任何降级，不得把“命令已提交”写成“已经出片”。

需要模板服务或素材搜索联网时，按 eMotionCut 的联网申请流程处理；不得绕过授权、修改冻结模板包或泄露包内个人凭据。
`)
  return manifest
}
