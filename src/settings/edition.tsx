import {CreationEnvironment} from '../general/CreationEnvironment';
import {AboutUpdate} from '../standard/ClientUpdate';
import {LicenseNotice} from './LicenseNotice';
import {ConnectionList} from '../studio/components/ConnectionSettings';
import {AgentInstallation} from '../studio/components/AgentInstallation';
import {UNIFIED_AGENTS} from '../general/platform';
import type {ConnectionId,ConnectionMode} from '../studio/connections';
import pkg from '../../package.json';
export const settingsGroups=['general','agents','models','integrations','storage','export','backup','about'];
export function AgentRoster(props:{initialAgent?:ConnectionId;initialMode?:ConnectionMode;onDirtyChange?:(value:boolean)=>void;onBusyChange?:(value:boolean)=>void}={}){return UNIFIED_AGENTS?<ConnectionList {...props}/>:<section className="agent-roster"><h2>已接入的 Agent</h2><p className="agent-roster-intro">按运行工作台服务的这台电脑检测。Codex 接入后，可在「工作模块的模型」里选择模型。</p><div className="agent-roster-list"><AgentInstallation kind="codex" {...props} use={<>用于 <b>工作模块</b>（见「工作模块的模型」）</>}/></div></section>}

export function EnhancedAgentSettings(){return null}
export function IntegrationSettings(){return <ConnectionList kind="service"/>}
export function PublishDirSetting(){return null}
export function ProjectMigration(_props:{defaultTarget:string;disabled:boolean;onChanged:()=>void}){return null}
export function StandardAbout({environment=true}:{environment?:boolean}={}){return <><AboutUpdate/>{environment&&<CreationEnvironment/>}<section><h3>eMotionCut</h3><p>版本 {pkg.version}</p><p>面向内容创作者的本地智能视频工作台，覆盖素材整理、文案编排、Motion 动效、剪辑与导出。</p><p>素材、工程与渲染默认保存在本机；使用智能体或第三方接口时，只向你选择的服务发送完成任务所需的内容。</p><LicenseNotice/></section></>}
export function DesktopSettings({section}:{section?:string}){return section==='about'?<StandardAbout environment={false}/>:null}
