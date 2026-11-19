import {GeneralRunWatcher} from './general/GeneralRunWatcher';
import {RouteLoadBoundary} from './workspace/RouteLoadBoundary';
import {ClientUpdate} from './standard/ClientUpdate';
import {Routes,Route,Link,useParams,useLocation} from 'react-router';
import GeneralEdit from './pages/GeneralEdit';
import GeneralProject from './pages/GeneralProject';
import StyleLibraryPage from './pages/StyleLibraryPage';
import Screen from './pages/OpenScreenWorkspace';
import Home from './shell/AtelierHome';
import Atelier404 from './shell/Atelier404';
import StudioProjects from './shell/AtelierProjects';
import {WorkspaceNavigationProvider} from './workspace/navigation-guard';
import {AppShell,ShellToaster} from './shell/AppShell';
import {StandardAbout} from './settings/edition';
import {modules} from './standard/modules';
import CreatorTopics from './pages/CreatorTopics';
import CreatorProject from './pages/CreatorProject';
import {MatrixDesk} from './publish/MatrixDesk';
import {MonitorDesk} from './publish/MonitorDesk';
import CompetitiveResearch from './pages/CompetitiveResearch';
import LegacyCreatorTool from './pages/LegacyCreatorTool';
import ProductionMethod from './pages/ProductionMethod';
import ScriptWorkbench from './pages/ScriptWorkbench';
export const upgrade=()=>{};
function Pro(){
 const {id}=useParams();const m=modules.find(x=>x.id===id);
 if(!m)return <main className="standard-page"><h1>页面不存在</h1><Link to="/">返回首页</Link></main>;
 const target=id==='topics'?'/general?entry=topic':id==='reader'?'/general?entry=footage':id==='motion'?'/styles':id==='koubo'?'/general?entry=footage':'/studio';
 return <main className="home-soon workspace-dashboard standard-pro-page"><section className="home-entry standard-pro-intro" data-kind="koubo"><div className="standard-pro-media"><img src={m.poster} alt={m.name+'封面'}/></div><div className="standard-pro-copy"><div className="standard-pro-title"><h1>{m.name}</h1></div><p>{'description' in m?m.description:m.brief}</p><div className="standard-pro-actions"><Link className="home-entry__button" to={target}>开始使用</Link><Link to="/">返回案台</Link></div></div></section></main>;
}
export default function App(){const {pathname}=useLocation();return <WorkspaceNavigationProvider><ClientUpdate/><GeneralRunWatcher/><AppShell><div className="workspace-content"><RouteLoadBoundary key={pathname}><Routes><Route path="/" element={<Home/>}/><Route path="/general" element={<GeneralEdit/>}/><Route path="/general/projects/:id" element={<GeneralProject/>}/><Route path="/styles" element={<StyleLibraryPage/>}/><Route path="/studio" element={<StudioProjects/>}/><Route path="/production" element={<ProductionMethod/>}/><Route path="/screen-motion" element={<StudioProjects/>}/><Route path="/screen-motion/projects/:id" element={<Screen/>}/><Route path="/about" element={<div className="system-settings"><main><StandardAbout/></main></div>}/><Route path="/creator-tools/bench" element={<CompetitiveResearch/>}/><Route path="/creator-tools/topics" element={<CreatorTopics/>}/><Route path="/creator-tools/script" element={<ScriptWorkbench mode="script"/>}/><Route path="/creator-tools/score" element={<ScriptWorkbench mode="score"/>}/><Route path="/creator-tools/:tool" element={<LegacyCreatorTool/>}/><Route path="/pro/topics" element={<CreatorTopics/>}/><Route path="/pro/reader" element={<CompetitiveResearch/>}/><Route path="/pro/publish" element={<MatrixDesk/>}/><Route path="/pro/analytics" element={<MonitorDesk/>}/><Route path="/pro/review" element={<MonitorDesk/>}/><Route path="/creator/projects/:id" element={<CreatorProject/>}/><Route path="/pro/:id" element={<Pro/>}/><Route path="*" element={<Atelier404/>}/></Routes></RouteLoadBoundary></div></AppShell><ShellToaster/></WorkspaceNavigationProvider>}
