(() => {
  const toast = document.createElement('div');
  toast.className = 'linda-action-toast044';
  toast.innerHTML = '<strong><i></i><span></span></strong><small></small>';
  document.body.appendChild(toast);
  let toastTimer = null;
  window.lindaAction044 = (title, detail='') => {
    const titleEl = toast.querySelector('strong span');
    const detailEl = toast.querySelector('small');
    if (titleEl) titleEl.textContent = title || 'Linda pracuje';
    if (detailEl) detailEl.textContent = detail || '';
    toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove('show'), 4500);
  };

  // Non-blocking feedback for ordinary POST/background actions.
  document.addEventListener('submit', (e) => {
    const form = e.target;
    if (!(form instanceof HTMLFormElement) || String(form.method).toLowerCase() !== 'post') return;
    if (form.matches('[data-linda-chat-form]')) return;
    const action = String(new FormData(form).get('action') || '');
    const messages = {
      generate_image: ['Linda připravuje obrázek', 'Úlohu můžeš nechat běžet na pozadí.'],
      generate_carousel: ['Linda připravuje carousel', 'Výsledek se objeví v médiích a u příspěvku.'],
      publish_post: ['Linda předává příspěvek k publikaci', 'Před odesláním proběhne kontrola konektoru.'],
      publish_blog: ['Linda připravuje článek k publikaci', 'Průběh uvidíš ve Frontě.'],
      sync: ['Linda synchronizuje web', 'Procházím obsah přes Fresh Core.'],
      sync_web: ['Linda synchronizuje web', 'Procházím obsah přes Fresh Core.'],
      refresh_profile: ['Linda aktualizuje profil projektu', 'Analyzuji znalosti webu a nabídku.'],
      analyze_brand: ['Linda analyzuje Brand kit', 'Pracuji s referencemi a vizuálním stylem.'],
    };
    if (messages[action] || form.dataset.backgroundJob === '1') {
      const msg = messages[action] || ['Linda převzala úlohu', 'Můžeš pokračovat v práci.'];
      window.lindaAction044(msg[0], msg[1]);
    }
  }, true);

  const form = document.querySelector('[data-linda-chat-form]');
  if (!form) return;
  const thread = document.querySelector('[data-chat-thread]');
  const textarea = form.querySelector('textarea[name="message"]');
  const submit = form.querySelector('[data-chat-submit]');
  const status = form.querySelector('[data-chat-status]');
  const lindaImg = document.querySelector('.coworker-person044 img')?.src || document.querySelector('.linda-brand-avatar043 img')?.src || '';
  const idleText = 'Enter odešle · Shift+Enter nový řádek';
  const stages = [
    'Přemýšlím nad zadáním…',
    'Analyzuji projekt…',
    'Procházím web a obsah…',
    'Kontroluji cestu ke konverzi…',
    'Skládám doporučení…',
  ];

  const now = () => new Intl.DateTimeFormat('cs-CZ', {hour:'2-digit', minute:'2-digit'}).format(new Date());
  const scroll = () => { if (thread) thread.scrollTop = thread.scrollHeight; };
  const setStatus = (text, error=false) => {
    if (!status) return;
    status.textContent = text;
    status.classList.toggle('error', error);
  };
  const addBubble = (role, text, opts={}) => {
    if (!thread) return null;
    thread.querySelector('.chat-empty044')?.remove();
    const row = document.createElement('div');
    row.className = `chat-msg044 ${role}${opts.thinking ? ' thinking' : ''}`;
    if (role === 'linda' && lindaImg) {
      const img = document.createElement('img');
      img.className = 'chat-avatar044'; img.src = lindaImg; img.alt = 'Linda'; row.appendChild(img);
    }
    const bubble = document.createElement('div');
    bubble.className = 'chat-bubble044';
    const body = document.createElement('div'); body.textContent = text || ''; bubble.appendChild(body);
    if (opts.thinking) {
      const dots = document.createElement('span'); dots.className = 'chat-thinking-dots044'; dots.innerHTML = '<i></i><i></i><i></i>'; body.appendChild(dots);
    } else {
      const small = document.createElement('small'); small.textContent = now(); bubble.appendChild(small);
    }
    row.appendChild(bubble); thread.appendChild(row); scroll();
    return {row, body};
  };

  textarea?.addEventListener('input', () => {
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.min(160, Math.max(50, textarea.scrollHeight))}px`;
  });
  textarea?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      form.requestSubmit();
    }
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    e.stopPropagation();
    const message = (textarea?.value || '').trim();
    if (!message || submit?.disabled) return;
    addBubble('user', message);
    textarea.value = ''; textarea.style.height = 'auto';
    textarea.disabled = true; submit.disabled = true;

    let stage = 0;
    const thinking = addBubble('linda', stages[0], {thinking:true});
    setStatus(stages[0]);
    const timer = setInterval(() => {
      stage = Math.min(stage + 1, stages.length - 1);
      if (thinking?.body) {
        thinking.body.childNodes[0].nodeValue = stages[stage];
      }
      setStatus(stages[stage]);
    }, 1900);

    try {
      const body = new FormData(form); body.set('message', message);
      const response = await fetch('/?page=api_chat_send', {method:'POST', body, headers:{'Accept':'application/json','X-Requested-With':'XMLHttpRequest'}});
      let data = {}; try { data = await response.json(); } catch (_) {}
      clearInterval(timer); thinking?.row?.remove();
      if (!response.ok || !data.ok) throw new Error(data.detail || data.error || `HTTP ${response.status}`);
      addBubble('linda', data.reply || 'Hotovo.');
      setStatus(idleText);
    } catch (err) {
      clearInterval(timer); thinking?.row?.remove();
      addBubble('linda', 'Nepodařilo se mi dokončit odpověď. Zkontroluji spojení s AI Enginem; zadání můžeš poslat znovu.');
      setStatus(`AI Engine: ${err.message || err}`, true);
      textarea.value = message;
    } finally {
      textarea.disabled = false; submit.disabled = false; textarea.focus(); scroll();
    }
  }, false);

  scroll();
})();
