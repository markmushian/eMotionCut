/** 两个版本共用的接入契约。这里不保存、返回或记录密钥。 */
export const CONNECTION_IDS = ['codex','claude','kimi','deepseek','hermes','minimax','codebuddy','zcode','grok','copilot','cursor','opencode','qwen','dreamina','minimax-speech','hailuo'] as const;
export type ConnectionId = typeof CONNECTION_IDS[number];
export type ConnectionMode = 'cli'|'api';
export type CliAuthState='connected'|'needs-login'|'unknown'|'pending'|'disabled'|'missing';
export const AGENT_CONNECTION_IDS=['codex','claude','kimi','deepseek','hermes','minimax','codebuddy','zcode','grok','copilot','cursor','opencode','qwen'] as const;
export type AgentConnectionId=typeof AGENT_CONNECTION_IDS[number];
export const LIGHT_AGENT_MODULES=['smallCard','captionTranslation','spokenScript','brollPrompt'] as const;
export type ConnectionModel = {id:string;name:string;isDefault?:boolean};
export type ConnectionConfig = {authRejectedAt?:string;revision?:number;mode:ConnectionMode;enabled:boolean;baseUrl:string;model:string;cliPath:string;hasKey:boolean;models:ConnectionModel[];checkedAt?:string;connected?:boolean;message?:string};
export type ConnectionView = ConnectionConfig & {cliVersion?:string;filmMinimumVersion?:string;filmOutdated?:boolean;upgradeCommand?:string;acpModelSelection?:boolean;acpVerification?:'local'|'docs'|'pending';acpNote?:string;loginHint?:string;cliAuthState?:CliAuthState;cliAuthMessage?:string;cliLoginAvailable?:boolean;id:ConnectionId;name:string;kind:'agent'|'service';cliInstalled:boolean;executable?:string;relay:boolean;heavySupported:boolean;state:'connected'|'disabled'|'unconfigured'|'pending';protocol:string;cliReason?:string;apiReason?:string};
export type ConnectionDefinition = {id:ConnectionId;name:string;kind:'agent'|'service';baseUrl:string;hosts:string[];protocol:'responses'|'anthropic'|'chat'|'voices'|'hailuo'|'ark'|'pending';cliNames:string[];heavySupported:boolean;/** A CLI that films through its own one-shot headless mode instead of ACP. */headless?:boolean;acpArgs?:readonly string[];loginArgs?:readonly string[];logoutArgs?:readonly string[];filmMinimumVersion?:string;upgradeCommand?:string;authStatusArgs?:readonly string[];installCommand?:string;acpVerification?:'local'|'docs'|'pending';acpNote?:string;loginHint?:string;cliReason?:string;apiReason?:string;page:string;/** 云端服务：注册 / 控制台直达，给「API 密钥」页的引导链接 */signupPage?:string;signupLabel?:string};
export const CONNECTION_DEFINITIONS:readonly ConnectionDefinition[] = [
 {id:'codex',name:'Codex',kind:'agent',baseUrl:'https://api.openai.com/v1',hosts:['api.openai.com'],protocol:'responses',cliNames:['codex'],heavySupported:true,loginArgs:['login'],logoutArgs:['logout'],authStatusArgs:['login','status'],page:'https://developers.openai.com/codex/cli/'},
 {id:'claude',name:'Claude Code',kind:'agent',baseUrl:'https://api.anthropic.com',hosts:['api.anthropic.com'],protocol:'anthropic',cliNames:['claude'],heavySupported:true,loginArgs:['auth','login'],logoutArgs:['auth','logout'],authStatusArgs:['auth','status','--json'],page:'https://code.claude.com/docs/en/overview'},
 {id:'kimi',name:'Kimi',kind:'agent',baseUrl:'https://api.moonshot.cn/v1',hosts:['api.moonshot.cn','api.moonshot.ai'],protocol:'chat',cliNames:['kimi'],heavySupported:true,acpArgs:['acp'],loginArgs:['login'],acpVerification:'local',acpNote:'本机 ACP 与登录参数已核实；真实出片待验收',page:'https://www.kimi.com/code/docs/en/kimi-code-cli/'},
 {id:'deepseek',name:'DeepSeek',kind:'agent',baseUrl:'https://api.deepseek.com',hosts:['api.deepseek.com'],protocol:'chat',cliNames:['dsh'],heavySupported:true,acpArgs:['--profile','acp'],acpVerification:'docs',acpNote:'官方文档已核实，未实机验证',installCommand:'npm install -g @deepseek-ai/dsh',page:'https://github.com/deepseek-ai/deepseek-harness'},
 // Hermes Agent ships bin/hermes-acp.exe, which is an ACP stdio server and takes no extra arguments.
 {id:'hermes',name:'Hermes',kind:'agent',baseUrl:'',hosts:[],protocol:'pending',cliNames:['hermes-acp'],heavySupported:true,acpArgs:[],loginHint:'先在 Hermes 自己的终端里完成模型配置',acpVerification:'local',acpNote:'本机 hermes-acp 已实测：ACP stdio 服务，无需额外参数',apiReason:'使用本机 Hermes 登录，通用 API 待适配',page:'https://github.com/NousResearch/Hermes-Agent'},
 {id:'minimax',name:'MiniMax Code',kind:'agent',baseUrl:'https://api.minimax.io/v1',hosts:['api.minimax.io','api.minimax.cn','api.minimaxi.com'],protocol:'chat',cliNames:['mcode','mmx-code'],heavySupported:true,acpArgs:['acp'],loginArgs:['login'],logoutArgs:['logout'],acpVerification:'docs',acpNote:'官方文档已核实；本机帮助被权限拦截，未实机验证',page:'https://platform.minimax.io/docs/api-reference/text-openai-api'},
 // WorkBuddy (CodeBuddy Code) ships this same command inside its own install folder.
 // This packaged WorkBuddy advertises ACP stdio but never answers its initialize request. Use
 // its supported one-shot mode and pre-authorize the named production tools instead of `-y`.
 {id:'codebuddy',name:'WorkBuddy',kind:'agent',baseUrl:'',hosts:[],protocol:'pending',cliNames:['codebuddy','cbc'],heavySupported:true,headless:true,acpArgs:['--acp'],loginArgs:[],loginHint:'终端打开后输入 /login',acpVerification:'local',acpNote:'本机实测：无头出片；仅预授权工程制作工具，不使用跳过全部权限',apiReason:'官方通用 API 接入格式待适配',page:'https://www.codebuddy.cn/docs/cli/installation'},
 // ZCode keeps its command as resources/glm/zcode.cjs and speaks its own app-server protocol; there is no ACP.
 // `-p --json` is its headless mode, and it needs ZCODE_BUILTIN_PROVIDER_CONFIG_FILE to find its provider.
 {id:'zcode',name:'ZCode',kind:'agent',baseUrl:'',hosts:[],protocol:'pending',cliNames:['zcode'],heavySupported:true,headless:true,loginArgs:[],loginHint:'终端运行 zcode login',acpVerification:'local',acpNote:'本机实测：-p 无头可用；请求能发出，账号余额不足时返回 429',apiReason:'在 ZCode 自己的界面里登录',page:'https://z.ai/'},
 // Grok ships grok.exe plus a same-named agent.exe — the one that used to be mistaken for Cursor.
 {id:'grok',name:'Grok',kind:'agent',baseUrl:'',hosts:[],protocol:'pending',cliNames:['grok'],heavySupported:true,headless:true,loginArgs:[],loginHint:'终端运行 grok --oauth 登录',acpVerification:'local',acpNote:'本机实测：-p --output-format streaming-json 可用',apiReason:'使用本机 Grok 登录，通用 API 待适配',page:'https://docs.x.ai/docs/guides/grok-code'},
 {id:'copilot',filmMinimumVersion:'0.0.397',upgradeCommand:'npm install -g @github/copilot',name:'GitHub Copilot',kind:'agent',baseUrl:'',hosts:[],protocol:'pending',cliNames:['copilot'],heavySupported:true,acpArgs:['--acp','--stdio'],loginArgs:['login'],installCommand:'npm install -g @github/copilot',acpVerification:'docs',acpNote:'官方文档已核实，未实机验证',apiReason:'使用本机 Copilot 登录，通用 API 待适配',page:'https://docs.github.com/en/copilot/reference/copilot-cli-reference/acp-server'},
 {id:'cursor',name:'Cursor',kind:'agent',baseUrl:'',hosts:[],protocol:'pending',cliNames:['agent','cursor-agent'],heavySupported:true,acpArgs:['acp'],loginArgs:['login'],logoutArgs:['logout'],authStatusArgs:['status'],installCommand:'curl https://cursor.com/install -fsS | bash',acpVerification:'docs',acpNote:'官方文档已核实，未实机验证',apiReason:'使用本机 Cursor 登录，通用 API 待适配',page:'https://cursor.com/docs/cli/acp'},
 {id:'opencode',name:'OpenCode',kind:'agent',baseUrl:'',hosts:[],protocol:'pending',cliNames:['opencode'],heavySupported:true,acpArgs:['acp'],loginArgs:['auth','login'],logoutArgs:['auth','logout'],authStatusArgs:['auth','list'],installCommand:'curl -fsSL https://opencode.ai/install | bash',acpVerification:'docs',acpNote:'官方文档已核实，未实机验证',apiReason:'在 OpenCode 自己的终端配置服务商',page:'https://opencode.ai/docs/acp/'},
 {id:'qwen',filmMinimumVersion:'0.6.1',upgradeCommand:'npm install -g @qwen-code/qwen-code@latest',name:'Qwen Code',kind:'agent',baseUrl:'',hosts:[],protocol:'pending',cliNames:['qwen'],heavySupported:true,acpArgs:['--acp'],loginArgs:[],loginHint:'终端打开后输入 /auth',installCommand:'npm install -g @qwen-code/qwen-code@latest',acpVerification:'docs',acpNote:'官方文档已核实，未实机验证',apiReason:'在 Qwen Code 自己的终端配置服务商',page:'https://qwenlm.github.io/qwen-code-docs/en/users/configuration/settings/'},
 {id:'dreamina',name:'豆包 · 火山方舟（Seedance / Seedream）',kind:'service',baseUrl:'https://ark.cn-beijing.volces.com/api/v3',hosts:['ark.cn-beijing.volces.com'],protocol:'ark',cliNames:[],heavySupported:false,cliReason:'火山方舟使用 API Key 和模型接入点，不需要本地安装命令行',page:'https://www.volcengine.com/docs/82379',signupPage:'https://console.volcengine.com/ark',signupLabel:'注册火山引擎，到方舟控制台创建 API Key ↗'},
 {id:'minimax-speech',name:'MiniMax 语音',kind:'service',baseUrl:'https://api.minimax.io/v1',hosts:['api.minimax.io','api.minimax.cn','api.minimaxi.com'],protocol:'voices',cliNames:[],heavySupported:false,cliReason:'官方没有提供命令行，只能用 API 密钥',page:'https://platform.minimax.io/docs/api-reference/voice-management-get',signupPage:'https://platform.minimaxi.com/user-center/basic-information/interface-key',signupLabel:'去 MiniMax 开放平台注册并创建 API 密钥 ↗'},
 {id:'hailuo',name:'MiniMax 视频 · 海螺',kind:'service',baseUrl:'https://api.minimax.io/v2',hosts:['api.minimax.io','api.minimax.cn','api.minimaxi.com'],protocol:'hailuo',cliNames:[],heavySupported:false,cliReason:'官方没有提供命令行，只能用 API 密钥',page:'https://platform.minimax.io/docs/api-reference/video-generation-intro',signupPage:'https://platform.minimaxi.com/user-center/basic-information/interface-key',signupLabel:'去 MiniMax 开放平台注册并创建 API 密钥 ↗'},
];
export function connectionDefinition(id:ConnectionId){const definition=CONNECTION_DEFINITIONS.find(item=>item.id===id);if(!definition)throw Error('未知接入项');return definition;}
/** Users often paste a full endpoint from a relay's docs. The rest of the code expects the service root (Anthropic) or API root (others). */
export function normalizeConnectionBaseUrl(id:ConnectionId,value:string){
 const definition=connectionDefinition(id);
 if(definition.kind!=='agent'||!['responses','anthropic','chat'].includes(definition.protocol)||!value)return value;
 let url:URL;try{url=new URL(value.trim())}catch{return value}
 if(url.username||url.password||url.search||url.hash)return value;
 let route=url.pathname.replace(/\/+$/,'');
 for(;;){const next=route.replace(/\/(?:messages(?:\/count_tokens)?|responses(?:\/input_tokens)?|chat\/completions|completions|models)$/i,'').replace(/\/+$/,'');if(next===route)break;route=next;}
 if(definition.protocol==='anthropic')route=route.replace(/\/v1$/i,'');
 return url.origin+route;
}
export function isRelay(id:ConnectionId,baseUrl:string){try{return !connectionDefinition(id).hosts.includes(new URL(baseUrl).hostname)}catch{return false}}
export function defaultConnection(id:ConnectionId):ConnectionConfig {const hasCli=connectionDefinition(id).cliNames.length>0;return {mode:hasCli?'cli':'api',enabled:id!=='claude',baseUrl:connectionDefinition(id).baseUrl,model:'default',cliPath:'',hasKey:false,models:[]};}
/** API film adapters only exist for Codex and Claude; native ACP providers require CLI login. */
export function connectionReadyForFilm(view:ConnectionView){return !!(view.enabled&&view.connected&&!view.filmOutdated&&view.cliInstalled&&view.heavySupported&&(view.mode==='cli'||view.id==='codex'||view.id==='claude'));}
export function connectionReadyForLight(view:ConnectionView){return !!(view.kind==='agent'&&view.enabled&&view.connected&&(view.mode==='api'?!view.apiReason:view.cliInstalled&&view.heavySupported));}
export function agentAcpArgs(id:ConnectionId):string[]|undefined {const args=connectionDefinition(id).acpArgs;return args?[...args]:undefined;}
/** Agents that film through a documented one-shot CLI run. Codex and Claude keep their own argument builders. */
export const HEADLESS_FILM_IDS=['codebuddy','zcode','grok'] as const;
export function isHeadlessFilmAgent(id:ConnectionId):boolean{return (HEADLESS_FILM_IDS as readonly string[]).includes(id);}
export function agentCliLoginArgs(id:ConnectionId):string[]|undefined {const args=connectionDefinition(id).loginArgs;return args?[...args]:undefined;}

/** Only documented command-introduction releases are floors; unknown/malformed versions never imply obsolete. */
export function filmVersionOutdated(version:string|undefined,minimum:string|undefined){
 if(!version||!minimum)return false;
 const parse=(v:string)=>/^([0-9]+)\.([0-9]+)\.([0-9]+)(?:[-+].*)?$/.exec(v)?.slice(1,4).map(Number);
 const current=parse(version),floor=parse(minimum);if(!current||!floor)return false;
 for(let i=0;i<3;i++){if(current[i]!==floor[i])return current[i]<floor[i];}
 return /-/.test(version)&&!/-/.test(minimum);
}
