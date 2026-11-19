import {useEffect, useState} from 'react'
import {FileText} from 'lucide-react'
import {Dialog, DialogContent, DialogDescription, DialogTitle} from '@/components/ui/dialog'
import './license-notice.css'

/**
 * 以前这里直接 <a href="/NOTICE" target="_blank">，在桌面壳里会弹出一个没有样式的纯文本窗口，
 * 看起来就是黑屏。现在在应用内打开可读的许可面板，正文仍取自随包的 NOTICE 文件。
 */
export function LicenseNotice() {
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open || text) return
    let live = true
    void fetch('/NOTICE', {cache: 'no-store'})
      .then(response => { if (!response.ok) throw Error(); return response.text() })
      .then(value => { if (live) setText(value) })
      .catch(() => { if (live) setError('许可文件暂时读不到。也可以到安装包目录直接查看 NOTICE、LICENSE 与 THIRD-PARTY-LICENSES.json。') })
    return () => { live = false }
  }, [open, text])

  return <>
    <button type="button" className="ec-license-link" onClick={() => setOpen(true)}><FileText size={13}/>开源许可与第三方声明</button>
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="ec-license-dialog">
        <DialogTitle>开源许可与第三方声明</DialogTitle>
        <DialogDescription>eMotionCut 本体的许可条款与随包第三方组件声明。</DialogDescription>
        <div className="ec-license-brand">
          <div className="ec-license-brand__name">eMotionCut</div>
          <p>一款把选题、写稿、Agent 剪辑与矩阵发布串起来的本地优先创作工作台。</p>
          <a href="https://github.com/markmushian/eMotionCut" target="_blank" rel="noreferrer">源码仓库：github.com/markmushian/eMotionCut ↗</a>
        </div>
        <pre className="ec-license-dialog__text">{error || text || '正在读取许可文件…'}</pre>
      </DialogContent>
    </Dialog>
  </>
}
