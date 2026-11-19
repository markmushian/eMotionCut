import MotionLibrary from './MotionLibrary'
import './style-library-page.css'

/** /styles: 风格模板已下线，这里就是 Motion 模板库。 */
export default function StyleLibraryPage() {
  return <main className="styles-page" aria-labelledby="styles-page-title">
    <div className="styles-page__shield" aria-hidden="true" />
    <MotionLibrary />
  </main>
}
