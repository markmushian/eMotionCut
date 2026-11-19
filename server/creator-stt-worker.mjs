import fs from 'node:fs'
import {pipeline,env} from '@huggingface/transformers'

const [pcm,cache]=process.argv.slice(2)
if(!pcm||!cache)throw new Error('缺少转写输入或模型缓存目录')
env.cacheDir=cache
const raw=fs.readFileSync(pcm)
if(raw.length<6400)throw new Error('作品没有可识别的人声音轨')
const audio=new Float32Array(raw.buffer.slice(raw.byteOffset,raw.byteOffset+raw.byteLength))
const recognizer=await pipeline('automatic-speech-recognition','onnx-community/whisper-base',{device:'cpu',dtype:'q8'})
try{
 const output=await recognizer(audio,{language:'zh',task:'transcribe',chunk_length_s:30,stride_length_s:5,return_timestamps:false})
 const text=(Array.isArray(output)?output.map(item=>item.text||'').join('\n'):output.text||'').trim()
 process.stdout.write(JSON.stringify({text}))
}finally{await recognizer.dispose?.()}
