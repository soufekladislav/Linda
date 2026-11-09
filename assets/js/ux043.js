(() => {
  const form = document.querySelector('[data-linda-chat-form]');
  if (!form) return;

  const thread = document.querySelector('[data-chat-thread]');
  const submit = form.querySelector('[data-chat-submit]');
  const textarea = form.querySelector('textarea[name="message"]');
  const status = form.querySelector('[data-chat-status]');
  const lindaImg = document.querySelector('.coworker-person042 img')?.src || '';

  const escTime = () => new Intl.DateTimeFormat('cs-CZ', {hour:'2-digit', minute:'2-digit'}).format(new Date());
  const scroll = () => { if (thread) thread.scrollTop = thread.scrollHeight; };

  const addBubble = (role, text, opts={}) => {
    if (!thread) return null;
    thread.querySelector('.chat-empty043')?.remove();
    const wrap = document.createElement('div');
    wrap.className = `chat-msg042 chat-msg043 ${role}${opts.typing ? ' typing' : ''}${opts.error ? ' error' : ''}`;
    if (role === 'linda' && lindaImg) {
      const img = document.createElement('img');
      img.className = 'chat-avatar042'; img.src = lindaImg; img.alt = 'Linda'; wrap.appendChild(img);
    }
    const bubble = document.createElement('div');
    bubble.className = 'chat-bubble042'; bubble.textContent = text;
    if (!opts.typing) {
      const small = document.createElement('small'); small.textContent = escTime(); bubble.appendChild(small);
    }
    wrap.appendChild(bubble); thread.appendChild(wrap); scroll();
    return wrap;
  };

  const addSuggestions = (items=[]) => {
    document.querySelectorAll('.chat-suggestions043').forEach(x => x.remove());
    const clean = items.filter(Boolean).slice(0,4);
    if (!clean.length || !thread) return;
    const box = document.createElement('div'); box.className = 'chat-suggestions043';
    clean.forEach(label => {
      const b = document.createElement('button'); b.type='button'; b.textContent=label;
      b.addEventListener('click', () => { textarea.value = label; textarea.focus(); }); box.appendChild(b);
    });
    thread.appendChild(box); scroll();
  };

  textarea?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault(); form.requestSubmit();
    }
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const message = (textarea?.value || '').trim();
    if (!message || submit?.disabled) return;
    addBubble('user', message);
    textarea.value = '';
    textarea.disabled = true; submit.disabled = true;
    if (status) { status.textContent = 'Linda přemýšlí nad projektem a webem…'; status.classList.remove('error'); }
    const typing = addBubble('linda', 'Linda přemýšlí…', {typing:true});

    try {
      const body = new FormData(form); body.set('message', message);
      const response = await fetch('/?page=api_chat_send', {method:'POST', body, headers:{'Accept':'application/json'}});
      let data = {}; try { data = await response.json(); } catch (_) {}
      typing?.remove();
      if (!response.ok || !data.ok) throw new Error(data.detail || data.error || `HTTP ${response.status}`);
      addBubble('linda', data.reply || 'Hotovo.');
      addSuggestions(Array.isArray(data.suggested_actions) ? data.suggested_actions : []);
      const engine = data.engine || {};
      if (status) status.textContent = engine.run_id ? `AI Engine · ${engine.model || engine.provider || 'zpracováno'} · run ${engine.run_id}` : 'Linda odpověděla přes AI Engine.';
    } catch (err) {
      typing?.remove();
      addBubble('linda', 'Teď se mi nepodařilo spojit s AI Enginem. Zpráva je zachovaná; zkus ji prosím poslat znovu.', {error:true});
      if (status) { status.textContent = `Chyba komunikace s AI Enginem: ${err.message || err}`; status.classList.add('error'); }
      textarea.value = message;
    } finally {
      textarea.disabled = false; submit.disabled = false; textarea.focus(); scroll();
    }
  });
})();
