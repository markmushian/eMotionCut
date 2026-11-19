import {CHECKS} from './onboarding-edition.ts';
import type {CheckResult,OnboardingCheck} from './onboarding-common.ts';
export {OPENSCREEN_MIN,versionBelow,onboardingSeen,markOnboardingSeen,openSystemPane,generalAgentOnboardingSeen,markGeneralAgentOnboardingSeen} from './onboarding-common.ts';
export type {CheckResult,CheckAction,CheckState,OnboardingCheck} from './onboarding-common.ts';
export const ONBOARDING_CHECKS=Object.keys(CHECKS) as OnboardingCheck[];
/** Raw filesystem errors（EPERM/EBUSY 之类）对用户没有意义，看起来还像程序崩了。 */
export function onboardingFailureText(error:unknown){
 const detail=(error instanceof Error?error.message:String(error??'')).trim();
 if(/EPERM|EACCES|EBUSY|resource busy|operation not permitted|permission denied/i.test(detail))return '这一项没查成：本机系统暂时没让程序读写临时目录（被占用或权限不足），请点「重新检查」；反复出现就重启 eMotionCut 再试';
 if(/ENOSPC|no space left/i.test(detail))return '这一项没查成：磁盘空间不足，请先清理磁盘再重新检查';
 if(/EMFILE|ENFILE|too many open files/i.test(detail))return '这一项没查成：本机同时打开的文件太多，请关掉一些程序后重新检查';
 if(/ENOENT/i.test(detail)&&/temp|tmp/i.test(detail))return '这一项没查成：系统临时目录不存在，请检查 TMP/TEMP 环境变量后重新检查';
 return `这一项没查成：${detail.replace(/\\\\\?\\/g,'').replace(/\s+/g,' ').slice(0,160)||'请重新检查'}`;
}
export async function runOnboardingCheck(id:string):Promise<CheckResult>{
 const check=CHECKS[id as OnboardingCheck];if(!check)throw Error('没有这一项检查');
 try{return await check()}catch(error){return {id:id as OnboardingCheck,state:'warn',text:onboardingFailureText(error)}}
}
