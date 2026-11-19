import {useEffect,useState} from 'react'
import {createPortal} from 'react-dom'
import {markOnboardingSeen,onboardingSeen,setDeployOpen,summary,useDeploy} from './onboarding-store'
import './first-run-guide.css'

type Rect={left:number;top:number;width:number;height:number;bottom:number;right:number}

/** New install: point at the header's 一键部署 button once, then never again. */
export function FirstRunDeployGuide(){
  const snap=useDeploy(),s=summary(snap)
  const [rect,setRect]=useState<Rect|null>(null)
  const [show,setShow]=useState(false)
  const [checked,setChecked]=useState(false)

  /* 部署自检是串行的，可能要几十秒；首启指引不能等它跑完才出现。
     先短暂确认未标记过就上指引，若检查期间发现环境已就绪，再自动收起。 */
  useEffect(()=>{
    let live=true,timer=0
    void onboardingSeen().then(seen=>{
      if(!live)return
      if(seen){setChecked(true);return}
      timer=window.setTimeout(()=>{if(!live)return;if(!s.ready)setShow(true);setChecked(true)},1200)
    })
    return()=>{live=false;window.clearTimeout(timer)}
  },[])
  useEffect(()=>{if(show&&s.ready){setShow(false);void markOnboardingSeen()}},[show,s.ready])

  useEffect(()=>{
    if(!show)return
    const measure=()=>{
      const target=document.querySelector<HTMLElement>('.ec-ruler__actions .ot-deploy-nav, .workspace-global-actions .ot-deploy-nav, .ot-deploy-nav')
      if(!target)return
      const r=target.getBoundingClientRect()
      setRect({left:r.left,top:r.top,width:r.width,height:r.height,bottom:r.bottom,right:r.right})
    }
    measure()
    const timer=window.setInterval(measure,300)
    window.addEventListener('resize',measure)
    window.addEventListener('scroll',measure,true)
    return()=>{window.clearInterval(timer);window.removeEventListener('resize',measure);window.removeEventListener('scroll',measure,true)}
  },[show])

  if(!show||!checked)return null
  const close=()=>{setShow(false);void markOnboardingSeen()}
  const go=()=>{close();setDeployOpen(true)}

  const spot=rect?{left:rect.left-9,top:rect.top-9,width:rect.width+18,height:rect.height+18}:undefined
  const tip=rect?{
    left:Math.max(20,Math.min(rect.left-40,window.innerWidth-400)),
    top:Math.min(rect.bottom+22,window.innerHeight-230),
  }:undefined

  return createPortal(<div className="frg" role="dialog" aria-modal="false" aria-label="新手指引">
    {spot&&<div className="frg__spot" style={spot}/>}
    {tip&&<div className="frg__tip" style={tip}>
      <span className="frg__eyebrow">第一次打开 · 从这里开始</span>
      <h2>先点右上角「一键部署」</h2>
      <p>Python、编码器、Ego 浏览器……2 分钟自动装好。先把环境准备好，后面写稿、剪辑、发布才不会中途卡住。</p>
      <div className="frg__ops">
        <button type="button" className="frg__go" onClick={go}>带我去部署 <span aria-hidden="true">→</span></button>
        <button type="button" className="frg__skip" onClick={close}>知道了，跳过</button>
      </div>
      <span className="frg__arrow" aria-hidden="true">↑</span>
    </div>}
  </div>,document.body)
}
