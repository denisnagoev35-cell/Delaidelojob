const { chromium } = require('playwright'); const fs=require('fs'); const dir=__dirname;
const tpl=fs.readFileSync(dir+'/tpl2.html','utf8');
const items=[
 ['kalyan','Кальянщик','6&nbsp;000–8&nbsp;000&nbsp;₽','за смену с % и чаевыми','Учат на месте'],
 ['barmen','Бармен','<small>от</small>1&nbsp;500&nbsp;₽','за смену + % и чаевые','Можно без опыта'],
 ['oficiant','Официант','≈5&nbsp;000&nbsp;₽','за смену с чаевыми','Можно без опыта'],
 ['povar','Повар','4&nbsp;500–6&nbsp;000&nbsp;₽','за смену','Помощником без опыта'],
 ['hostes','Хостес','<small>до</small>6&nbsp;000&nbsp;₽','за смену','Вечерние смены'],
];
(async()=>{
 const b=await chromium.launch();
 for(const [k,kick,money,unit,chip] of items){
  for(const [h,kg,ms,msz,tg,cd] of [[1920,170,190,760,'34%','block']]){
   const html=tpl.replace('top:64px','top:'+(h==1920?240:64)+'px').replace('bottom:-60px','bottom:'+(h==1920?'240px':'-60px')).replace('HEIGHT',h).replace('KGAP',kg).replace(/MSZ/g,msz).replace('MSpx',ms+'px').replace('TOPGLOW',tg).replace('KICK',kick).replace('MONEY',money).replace('UNIT',unit).replace('CHIPD',cd).replace('CHIP',chip);
   const f=dir+`/_t.html`; fs.writeFileSync(f,html);
   const p=await b.newPage({viewport:{width:1080,height:h}});
   await p.goto('file://'+f); await p.evaluate(()=>document.fonts.ready); await p.waitForTimeout(150);
   const pct=await p.evaluate(()=>{var m=document.querySelector('.money'),fs=parseFloat(getComputedStyle(m).fontSize);while(m.scrollWidth>936&&fs>60){fs-=4;m.style.fontSize=fs+'px';}
     var a=0;document.querySelectorAll('.t').forEach(e=>{var r=e.getBoundingClientRect();a+=r.width*r.height});return a/(1080*innerHeight)*100});
   await p.screenshot({path:dir+`/${k}_1080x${h}.png`}); await p.close();
   console.log(k,h,pct.toFixed(1)+'%');
  }
 }
 fs.unlinkSync(dir+'/_t.html'); await b.close();
})();
