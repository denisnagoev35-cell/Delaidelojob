const { chromium } = require('playwright'); const { spawn } = require('child_process'); const fs=require('fs');
const scenes=[[0,2.6],[2.6,10.6],[10.6,14.2],[14.2,17.0]];
(async()=>{const b=await chromium.launch();const p=await b.newPage({viewport:{width:1080,height:1920}});
await p.goto('file://'+__dirname+'/v.html');await p.evaluate(()=>document.fonts.ready);
const list=[];
for(let si=0;si<scenes.length;si++){const [a,e]=scenes[si]; const n=Math.round((e-a)*30);
 const tmp=__dirname+`/sc${si}.tmp.mp4`, out=__dirname+`/sc${si}.mp4`;
 const ff=spawn('ffmpeg',['-y','-v','error','-f','image2pipe','-framerate','30','-c:v','mjpeg','-i','-','-c:v','libx264','-preset','veryfast','-crf','18','-pix_fmt','yuv420p','-r','30',tmp]);
 for(let i=0;i<n;i++){await p.evaluate(t=>render(t),a+i/30);const buf=await p.screenshot({type:'jpeg',quality:92});
   if(!ff.stdin.write(buf)) await new Promise(r=>ff.stdin.once('drain',r));}
 ff.stdin.end(); const code=await new Promise(r=>ff.on('close',r)); if(code!==0) throw new Error('ffmpeg '+si);
 fs.renameSync(tmp,out); list.push(`file '${out}'`);}
fs.writeFileSync(__dirname+'/list.txt',list.join('\n')); await b.close();})();
