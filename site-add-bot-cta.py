import re, glob, sys
BOT = "https://t.me/delaidelo24bot?start="
CSS = (".botcta{margin-top:18px;padding:16px;border-radius:16px;background:linear-gradient(135deg,#1d1803,#111);"
       "border:1px solid rgba(255,198,3,.45);display:flex;flex-direction:column;gap:10px;text-align:left}"
       ".botcta b{font-style:italic;font-weight:900;font-size:17px;text-transform:uppercase}"
       ".botcta span{color:#CFCFCF;font-size:14.5px;line-height:1.45}"
       ".botcta .btn{margin:0;min-height:52px}\n")
def block(src):
    return ('<div class="botcta"><b>💰 Сколько должны платить тебе?</b>'
            '<span>Выбери профессию и опыт — бот покажет вилку зарплаты и свежие вакансии. 30 секунд, бесплатно.</span>'
            f'<a class="btn btn-main" href="{BOT}{src}" onclick="return ddGo(event,this,\'bot_click\')">Узнать свою зарплату</a></div>')
changed=0
for p in sorted(glob.glob('rabota/**/index.html', recursive=True)):
    s=open(p,encoding='utf8').read()
    if 'class="botcta"' in s: continue
    m=re.search(r'(<p class="note">Ставки[^<]*</p>)(</section>)', s)
    if not m: continue
    s=s[:m.end(1)]+block('site_city')+s[m.end(1):]
    s=s.replace('</style>', CSS+'</style>',1)
    open(p,'w',encoding='utf8').write(s); changed+=1
# /vk/ page: after salary source note
p='vk/index.html'; s=open(p,encoding='utf8').read()
if 'class="botcta"' not in s:
    a='Точная оплата указана в каждой вакансии.</p>'
    assert s.count(a)==1
    s=s.replace(a, a+'\n  '+block('site_vk'),1).replace('</style>', CSS+'</style>',1)
    open(p,'w',encoding='utf8').write(s); changed+=1
# main page: before facts block
p='index.html'; s=open(p,encoding='utf8').read()
if 'class="botcta"' not in s:
    a='  <div class="facts">'
    assert s.count(a)==1
    s=s.replace(a, '  '+block('site_main')+'\n'+a,1).replace('</style>', CSS+'</style>',1)
    open(p,'w',encoding='utf8').write(s); changed+=1
print('changed',changed)
