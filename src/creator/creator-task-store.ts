export type CreatorTask={id:string;title:string;state:'running'|'done'|'failed';startedAt:number;finishedAt?:number;returnTo:string;result?:string;error?:string}
let tasks:CreatorTask[]=[];try{tasks=JSON.parse(sessionStorage.getItem('emotioncut:creator-tasks')||'[]').filter((x:CreatorTask)=>x.state!=='running')}catch{}
const listeners=new Set<()=>void>();const save=()=>{tasks=[...tasks];sessionStorage.setItem('emotioncut:creator-tasks',JSON.stringify(tasks.slice(0,12)));listeners.forEach(fn=>fn())}
const announce=(task:CreatorTask)=>window.dispatchEvent(new CustomEvent('emotioncut:creator-task-complete',{detail:{...task}}))
export const creatorTaskSnapshot=()=>tasks
export const subscribeCreatorTasks=(fn:()=>void)=>{listeners.add(fn);return()=>listeners.delete(fn)}
export function createCreatorTask(input:{title:string;returnTo:string}){const task:CreatorTask={id:crypto.randomUUID(),title:input.title,state:'running',startedAt:Date.now(),returnTo:input.returnTo};tasks=[task,...tasks.filter(x=>x.state==='running')];save();return task.id}
export function updateCreatorTask(id:string,patch:Partial<Pick<CreatorTask,'title'|'returnTo'>>){const task=tasks.find(item=>item.id===id);if(task){Object.assign(task,patch);save()}}
export function finishCreatorTask(id:string,result:string){const task=tasks.find(item=>item.id===id);if(task){task.state='done';task.result=result;task.finishedAt=Date.now();save();announce(task)}}
export function failCreatorTask(id:string,error:unknown){const task=tasks.find(item=>item.id===id);if(task){task.state='failed';task.error=error instanceof Error?error.message:String(error);task.finishedAt=Date.now();save()}}
export async function runCreatorTask(input:{title:string;returnTo:string;system:string;user:string;taskId?:string}){
 const existing=input.taskId&&tasks.find(item=>item.id===input.taskId),task=existing||{id:crypto.randomUUID(),title:input.title,state:'running' as const,startedAt:Date.now(),returnTo:input.returnTo};task.title=input.title;task.returnTo=input.returnTo;if(!existing){tasks=[task,...tasks.filter(x=>x.state==='running')];save()}else save()
 try{const response=await fetch('/api/creator/agent',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({system:input.system,user:input.user})}),value=await response.json();if(!response.ok)throw Error(value.error);task.state='done';task.result=String(value.result.text);task.finishedAt=Date.now();save();announce(task);return task.result}catch(error){task.state='failed';task.error=error instanceof Error?error.message:String(error);task.finishedAt=Date.now();save();throw error}
}
export function dismissCreatorTask(id:string){tasks=tasks.filter(x=>x.id!==id);save()}
