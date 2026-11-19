import { ArrowLeft } from 'lucide-react'
import { WorkspaceLink as Link } from '@/workspace/navigation-guard'

/** 走错了工序：不是错误弹窗，是一张空页，给回案台的路。 */
export default function Atelier404() {
  return <div className="ec-deadend">
    <p className="ec-kicker">— · 空页</p>
    <p className="ec-deadend__no" aria-hidden="true">404</p>
    <h1 className="ec-h1">这页不在案上</h1>
    <p className="ec-deadend__lede">地址可能抄错，也可能这条工序还没开。回案台重新挑一条。</p>
    <Link to="/" className="ec-deadend__back"><ArrowLeft size={14} aria-hidden="true" />回案台</Link>
  </div>
}
