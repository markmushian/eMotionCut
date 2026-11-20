import {AGENT_IMAGES,AGENT_MARKS,SERVICE_MARKS,type AgentId} from '../agent-marks';
/** An agent's maker's mark on a dark tile (settings rows: 40 px; the work-module picker: 22 px). */
export function AgentMark({id,size=40}:{id:AgentId|string;size?:number}){
 const image=AGENT_IMAGES[id as AgentId],mark=AGENT_MARKS[id as AgentId]??SERVICE_MARKS[id];
 return <span className="agent-mark" style={{width:size,height:size,borderRadius:size/4}} aria-hidden="true">
  {image?<img src={image} width={size} height={size} alt="" style={{borderRadius:'inherit',objectFit:'cover',display:'block'}}/>:mark?<svg viewBox={mark.viewBox} width={size*.55} height={size*.55}><path fill={mark.color} d={mark.d}/></svg>:<span style={{fontSize:size*.35,fontWeight:600}}>{id.slice(0,2).toUpperCase()}</span>}
 </span>;
}
