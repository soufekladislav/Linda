(() => {
  const form = document.querySelector('[data-linda-chat-form]');
  if (!form) return;
  const thread = document.querySelector('[data-chat-thread]');
  const textarea = form.querySelector('textarea[name="message"]');
  const submit = form.querySelector('[data-chat-submit]');
  const status = form.querySelector('[data-chat-status]');
  const lindaImg = document.querySelector('.coworker-person045 img')?.src || document.querySelector('.brand043 img')?.src || '';
  const idle = 'Enter odešle · Shift+Enter nový řádek';
  const stages = ['Přemýšlím…','Analyzuji projekt…','Procházím web a obsah…','Hledám nejlepší cestu ke konverzi…','Skládám odpověď…'];
  let sending = false;

  const scroll = () => { if (thread) thread.scrollTop = thread.scrollHeight; };
  const stamp = () => new Intl.DateTimeFormat('cs-CZ',{hour:'2-digit',minute:'2-digit'}).format(new Date());
  const setStatus = (text, error=false) => {
    if (!status) return;
    status.textContent = text;
    status.classList.toggle('error', error);
  };
  const add = (role, text, thinking=false) => {
    if (!thread) return null;
    thread.querySelector('.chat-empty045')?.remove();
    const row = document.createElement('div');
    row.className = `chat-msg045 ${role}${thinking ? ' thinking' : ''}`;
    if (role === 'linda' && lindaImg) {
      const img = document.createElement('img'); img.className='chat-avatar045'; img.src=lindaImg; img.alt='Linda'; row.appendChild(img);
    }
    const bubble = document.createElement('div'); bubble.className='chat-bubble045';
    const body = document.createElement('div'); body.textContent=text; bubble.appendChild(body);
    if (!thinking) { const small=document.createElement('small'); small.textContent=stamp(); bubble.appendChild(small); }
    row.appendChild(bubble); thread.appendChild(row); scroll();
    return {row,body};
  };

  const fit = () => {
    if (!textarea) return;
    textarea.style.height='auto';
    textarea.style.height=`${Math.min(180,Math.max(52,textarea.scrollHeight))}px`;
  };
  textarea?.addEventListener('input', fit);
  textarea?.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); }
  });

  form.addEventListener('submit', async e => {
    e.preventDefault();
    e.stopPropagation();
    if (sending) return;
    const message=(textarea?.value || '').trim();
    if (!message) return;
    sending=true;
    add('user',message);
    textarea.value=''; fit(); textarea.disabled=true; submit.disabled=true;
    let stage=0;
    const thinking=add('linda',stages[0],true);
    setStatus(stages[0]);
    const timer=setInterval(()=>{
      stage=Math.min(stage+1,stages.length-1);
      if (thinking?.body) thinking.body.textContent=stages[stage];
      setStatus(stages[stage]);
    },1700);
    try {
      const body=new FormData(form); body.set('message',message);
      const res=await fetch('/?page=api_chat_send',{method:'POST',body,headers:{'Accept':'application/json','X-Requested-With':'XMLHttpRequest'}});
      let data={}; try { data=await res.json(); } catch (_) {}
      clearInterval(timer); thinking?.row?.remove();
      if (!res.ok || !data.ok) throw new Error(data.detail || data.error || `HTTP ${res.status}`);
      add('linda',data.reply || 'Hotovo.');
      const eng=data.engine || {};
      setStatus(eng.run_id ? `AI Engine · ${eng.model || eng.provider || 'hotovo'} · ${eng.run_id}` : idle);
    } catch (err) {
      clearInterval(timer); thinking?.row?.remove();
      add('linda','Teď se mi nepodařilo dokončit odpověď. Spojení s AI Enginem je v diagnostice zaznamenané.');
      setStatus(`Chyba AI Engine: ${err.message || err}`,true);
      textarea.value=message; fit();
    } finally {
      sending=false; textarea.disabled=false; submit.disabled=false; textarea.focus(); scroll();
    }
  }, true);
  scroll(); fit();
})();
