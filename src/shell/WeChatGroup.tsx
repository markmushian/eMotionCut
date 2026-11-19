import {useEffect, useRef, useState} from 'react'
import './wechat-group.css'

/** 微信双气泡标（白色，放在绿色圆底上）。 */
function WeChatMark({size = 16}: {size?: number}) {
  return <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true">
    <path fill="currentColor" d="M9.5 4C5.36 4 2 6.69 2 10c0 1.89 1.08 3.56 2.78 4.66l-.7 2.1 2.45-1.23c.87.28 1.86.47 2.97.47.28 0 .55-.01.82-.04A5.1 5.1 0 0 1 10 14.5c0-3.04 2.91-5.5 6.5-5.5.34 0 .67.02 1 .06C16.77 6.44 13.45 4 9.5 4Zm-2.75 3.5a.9.9 0 1 1 0 1.8.9.9 0 0 1 0-1.8Zm5.5 0a.9.9 0 1 1 0 1.8.9.9 0 0 1 0-1.8Z"/>
    <path fill="currentColor" opacity=".88" d="M22 14.5c0-2.76-2.69-5-6-5s-6 2.24-6 5 2.69 5 6 5c.9 0 1.7-.14 2.44-.38l2.06 1.04-.6-1.78C21.14 17.44 22 16.04 22 14.5Zm-8.25-1.4a.8.8 0 1 1 0 1.6.8.8 0 0 1 0-1.6Zm4.5 0a.8.8 0 1 1 0 1.6.8.8 0 0 1 0-1.6Z"/>
  </svg>
}

/** 左下角的微信交流群入口：点开是横条卡片，「展示二维码」展开群二维码。 */
export function WeChatGroup() {
  const [open, setOpen] = useState(false)
  const [showQr, setShowQr] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => {if (box.current && !box.current.contains(e.target as Node)) setOpen(false)}
    const esc = (e: KeyboardEvent) => {if (e.key === 'Escape') {setOpen(false); setShowQr(false)}}
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', esc)
    return () => {document.removeEventListener('mousedown', close); document.removeEventListener('keydown', esc)}
  }, [open])
  return <div className="wechat-group" ref={box}>
    <button type="button" className="wechat-group__btn" aria-label="微信交流群" aria-expanded={open}
      title="微信交流群" onClick={() => {setOpen(value => !value); setShowQr(false)}}>
      <WeChatMark/>
    </button>
    {open && <div className="wechat-group__card" role="dialog" aria-label="微信交流群">
      <div className="wechat-group__head">
        <span className="wechat-group__mark" aria-hidden="true"><WeChatMark size={19}/></span>
        <span className="wechat-group__name">微信交流群</span>
        <button type="button" className="wechat-group__reveal" aria-expanded={showQr} onClick={() => setShowQr(value => !value)}>
          {showQr ? '收起二维码' : '展示二维码'}
        </button>
      </div>
      {showQr && <img className="wechat-group__qr" src="/wechat-group-qr.png" alt="微信交流群二维码" width={232} height={232}/>}
    </div>}
  </div>
}
