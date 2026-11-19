import {Navigate,useLocation,useParams} from 'react-router'
import './legacy-creator-tool.css'

const pages:Record<string,string>={bench:'bench',topics:'topics',script:'script',score:'score',voice:'edit&tab=1',avatar:'edit&tab=2'}
export default function LegacyCreatorTool(){const {tool}=useParams(),{pathname}=useLocation();if(tool==='voice'||tool==='avatar')return <Navigate to="/production" replace/>;const page=pathname==='/pro/reader'?'bench':pages[tool||'topics']||'topics';return <main className="legacy-creator-tool"><iframe title="AI 博主创作工具" src={`http://127.0.0.1:7891/tools?page=${page}`}/></main>}
