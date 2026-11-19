const path=require('node:path');
function windowsRuntimeRoot(source,env=process.env){
 const override=env.EMOTIONCUT_WINDOWS_RUNTIME_ROOT;
 if(override&&!path.isAbsolute(override))throw Error('EMOTIONCUT_WINDOWS_RUNTIME_ROOT 必须是绝对路径');
 return path.resolve(override||path.join(source,'../runtime-payload'));
}
module.exports={windowsRuntimeRoot};
