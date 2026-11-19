function applicationMenu({platform,app,recordingMenu}){
 const about={label:'关于 eMotionCut',click:()=>app.showAboutPanel()};
 const edit={label:'编辑',submenu:[
  {label:'撤销',role:'undo'},{label:'重做',role:'redo'},{type:'separator'},
  {label:'剪切',role:'cut'},{label:'复制',role:'copy'},{label:'粘贴',role:'paste'},{label:'全选',role:'selectAll'}]};
 const view={label:'查看',submenu:[
  {label:'重新加载',role:'reload'},{label:'强制重新加载',role:'forceReload'},
  {label:'开发者工具',role:'toggleDevTools'},{type:'separator'},
  {label:'实际大小',role:'resetZoom'},{label:'放大',role:'zoomIn'},{label:'缩小',role:'zoomOut'},
  {type:'separator'},{label:'切换全屏',role:'togglefullscreen'}]};
 const window={label:'窗口',submenu:[{label:'最小化',role:'minimize'},{label:'关闭窗口',role:'close'}]};
 if(platform==='darwin')return [{label:'eMotionCut',submenu:[about,{type:'separator'},{label:'退出',role:'quit'}]},edit,view,window];
 return [...(recordingMenu?[{label:'录制',submenu:recordingMenu}]:[]),
  edit,view,window,
  {label:'帮助',submenu:[about,{type:'separator'},{label:'退出',role:'quit'}]}];
}
module.exports={applicationMenu};
