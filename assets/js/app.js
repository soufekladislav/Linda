document.addEventListener('DOMContentLoaded', () => {
  const btn = document.querySelector('[data-menu-toggle]');
  const sidebar = document.getElementById('appSidebar');
  const backdrop = document.querySelector('[data-menu-backdrop]');
  if (btn && sidebar) {
    const isMobileMenu = () => window.matchMedia('(max-width: 900px)').matches;
    const setMenu = (open) => {
      const shouldOpen = Boolean(open) && isMobileMenu();
      sidebar.classList.toggle('open', shouldOpen);
      document.body.classList.toggle('mobile-menu-open045', shouldOpen);
      btn.setAttribute('aria-expanded', shouldOpen ? 'true' : 'false');
      btn.setAttribute('aria-label', shouldOpen ? 'Zavřít menu' : 'Otevřít menu');
      if (backdrop) backdrop.setAttribute('aria-hidden', shouldOpen ? 'false' : 'true');
    };
    btn.setAttribute('aria-expanded', 'false');
    setMenu(false);
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      setMenu(!sidebar.classList.contains('open'));
    });
    backdrop?.addEventListener('click', () => setMenu(false));
    sidebar.querySelectorAll('a').forEach(a => a.addEventListener('click', () => setMenu(false)));
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && sidebar.classList.contains('open')) setMenu(false);
    });
    window.addEventListener('resize', () => {
      if (!isMobileMenu()) setMenu(false);
    }, {passive:true});
  }

  const specs = {
    facebook: { label: 'Facebook', min: 500, max: 900, recommended: '500–900 znaků', note: 'Osobněji, jasný hook a jednoduché CTA.' },
    instagram: { label: 'Instagram', min: 400, max: 700, recommended: '400–700 znaků', note: 'Emoce, vizuální kontext, CTA k reakci nebo uložení.' },
    linkedin: { label: 'LinkedIn', min: 700, max: 1200, recommended: '700–1200 znaků', note: 'Odborný nebo příběhový post; 90–150 znaků je jen hook.' },
    x: { label: 'X', min: 120, max: 280, recommended: '120–280 znaků', note: 'Krátce, jasně a s jedním hlavním sdělením.' },
    tiktok: { label: 'TikTok', min: 120, max: 600, recommended: '120–600 znaků', note: 'Krátký caption, silný hook a vizuální nebo video kontext.' },
    blog: { label: 'Blog / web', min: 1800, max: 3600, recommended: '1800–3600 znaků', note: 'Krátký článek cca 1–2 normostrany, struktura s mezititulky.' }
  };

  function closeDetails(el) {
    const d = el.closest('details');
    if (d) d.open = false;
  }
  document.addEventListener('click', (e) => {
    const close = e.target.closest('[data-close-modal]');
    if (close) { e.preventDefault(); closeDetails(close); }
  });
  document.addEventListener('click', (e) => {
    document.querySelectorAll('details.user-menu[open]').forEach(d => {
      if (!d.contains(e.target)) d.open = false;
    });
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') document.querySelectorAll('details[open]').forEach(d => d.open = false);
  });

  function selectedPlatform(scope) {
    const p = scope.querySelector('[data-platform-select]');
    return (p && p.value) || 'facebook';
  }
  function refreshFormatSelect(scope) {
    const platform = scope.querySelector('[data-platform-select]');
    const format = scope.querySelector('[data-format-select]');
    if (!platform || !format) return;
    const selected = platform.value;
    let firstVisible = null;
    Array.from(format.options).forEach(opt => {
      const allowed = (opt.dataset.platforms || '').split(',');
      const visible = allowed.includes(selected);
      opt.hidden = !visible; opt.disabled = !visible;
      if (visible && !firstVisible) firstVisible = opt;
    });
    if ((format.selectedOptions[0] && format.selectedOptions[0].disabled) && firstVisible) firstVisible.selected = true;
    if (selected === 'blog') {
      const blog = Array.from(format.options).find(o => o.value === 'blog_promo');
      if (blog) blog.selected = true;
    }
  }
  function refreshPlatformHints(scope) {
    const platform = selectedPlatform(scope);
    const spec = specs[platform] || specs.facebook;
    scope.querySelectorAll('[data-platform-hint]').forEach(h => {
      h.textContent = `${spec.label}: doporučeno ${spec.recommended}. ${spec.note}`;
    });
    scope.querySelectorAll('[data-body-textarea]').forEach(t => updateCounter(t, spec));
  }
  function updateCounter(textarea, spec) {
    const scope = textarea.closest('form, .modal-panel') || document;
    const counter = scope.querySelector('[data-char-count]');
    if (!counter) return;
    const len = textarea.value.length;
    let cls = 'char-ok';
    let label = `${len} znaků / doporučeno ${spec.recommended}`;
    if (len === 0) { cls = ''; }
    else if (len < spec.min * 0.6 || len > spec.max * 1.2) { cls = 'char-bad'; label += ' – výrazně mimo doporučení'; }
    else if (len < spec.min || len > spec.max) { cls = 'char-warn'; label += ' – mimo ideál'; }
    counter.className = cls;
    counter.textContent = label;
  }
  function refreshCreateMode(scope) {
    const mode = scope.querySelector('[data-create-mode]');
    if (!mode) return;
    const ai = mode.value === 'ai';
    scope.classList.toggle('ai-mode', ai);
    scope.querySelectorAll('.manual-only36').forEach(el => el.style.display = ai ? 'none' : 'grid');
  }
  function refreshAll(scope) {
    refreshFormatSelect(scope);
    refreshPlatformHints(scope);
    refreshCreateMode(scope);
  }
  document.querySelectorAll('form, .modal-panel').forEach(refreshAll);
  document.addEventListener('change', (e) => {
    const scope = e.target.closest('form, .modal-panel') || document;
    if (e.target.matches('[data-platform-select]')) refreshAll(scope);
    if (e.target.matches('[data-create-mode]')) refreshAll(scope);
    if (e.target.matches('[data-provider-select]')) refreshConnectorHelp(e.target.closest('form') || document);
  });
  document.addEventListener('input', (e) => {
    if (!e.target.matches('[data-body-textarea]')) return;
    const scope = e.target.closest('form, .modal-panel') || document;
    const spec = specs[selectedPlatform(scope)] || specs.facebook;
    updateCounter(e.target, spec);
  });

  function openAddPost(platform, date) {
    const modal = document.getElementById('addPostModal');
    if (!modal) return;
    modal.open = true;
    const select = modal.querySelector('[data-platform-select]');
    if (select && platform) select.value = platform;
    const scheduled = modal.querySelector('[data-scheduled-input]');
    if (scheduled && date) {
      const currentTime = scheduled.value && scheduled.value.includes('T') ? scheduled.value.split('T')[1] : '10:00';
      scheduled.value = `${date}T${currentTime || '10:00'}`;
    }
    refreshAll(modal);
  }
  document.addEventListener('click', (e) => {
    const opener = e.target.closest('[data-open-add-post]');
    if (!opener) return;
    e.preventDefault(); openAddPost(opener.dataset.platform || 'facebook', opener.dataset.date || '');
  });

  function refreshConnectorHelp(scope) {
    const provider = scope.querySelector('[data-provider-select]');
    if (!provider) return;
    scope.querySelectorAll('.connector-help').forEach(h => h.classList.toggle('active', h.dataset.help === provider.value));
    scope.querySelectorAll('[data-field-for]').forEach(el => {
      const allowed = (el.dataset.fieldFor || '').split(',');
      el.style.display = allowed.includes(provider.value) ? '' : 'none';
    });
    const authLabels = {
      facebook: 'Page ID', instagram: 'Instagram Business Account ID', linkedin: 'Author URN', x: 'User ID / účet', tiktok: 'TikTok účet / Creator ID'
    };
    const tokenLabels = {
      facebook: 'Page Access Token', instagram: 'Instagram Access Token', linkedin: 'LinkedIn Access Token', x: 'X User Access Token', tiktok: 'TikTok Access Token', wordpress: 'Access token z WordPress pluginu'
    };
    scope.querySelectorAll('[data-auth-label]').forEach(el => el.textContent = authLabels[provider.value] || 'Uživatel / Author ID');
    scope.querySelectorAll('[data-token-label]').forEach(el => el.textContent = tokenLabels[provider.value] || 'Access token');
    const authInput = scope.querySelector('input[name="auth_user"]');
    if (authInput) authInput.placeholder = provider.value === 'facebook' ? 'Např. 123456789012345' : (provider.value === 'linkedin' ? 'urn:li:organization:123456' : 'ID účtu');
  }
  document.querySelectorAll('.connector-form').forEach(refreshConnectorHelp);

  function showDialog373(dialog) {
    if (!dialog) return;
    if (typeof dialog.showModal === 'function') {
      if (!dialog.open) dialog.showModal();
    } else {
      dialog.setAttribute('open','');
    }
    document.body.classList.add('dialog-open373');
    const first = dialog.querySelector('input:not([type=hidden]),select,textarea,button');
    if (first) setTimeout(() => first.focus({preventScroll:true}), 30);
  }
  function closeDialog373(dialog) {
    if (!dialog) return;
    if (typeof dialog.close === 'function' && dialog.open) dialog.close();
    else dialog.removeAttribute('open');
    if (!document.querySelector('dialog[open]')) document.body.classList.remove('dialog-open373');
  }
  function openConnectorModal(provider) {
    const modal = document.getElementById('connectorAddDialog');
    if (!modal) return;
    const select = modal.querySelector('[data-provider-select]');
    if (select && provider) select.value = provider;
    refreshConnectorHelp(modal.querySelector('form') || modal);
    showDialog373(modal);
  }
  document.addEventListener('click', (e) => {
    const connectorOpener = e.target.closest('[data-open-connector]');
    if (connectorOpener) {
      e.preventDefault();
      openConnectorModal(connectorOpener.dataset.provider || 'wordpress');
      return;
    }
    const opener = e.target.closest('[data-open-dialog]');
    if (opener) {
      e.preventDefault();
      const dialog = document.getElementById(opener.dataset.openDialog || '');
      if (dialog) {
        refreshConnectorHelp(dialog.querySelector('form') || dialog);
        showDialog373(dialog);
      }
      return;
    }
    const closer = e.target.closest('[data-close-dialog]');
    if (closer) {
      e.preventDefault();
      closeDialog373(closer.closest('dialog'));
    }
  });
  document.querySelectorAll('dialog.connector-dialog373').forEach(dialog => {
    dialog.addEventListener('click', e => {
      if (e.target !== dialog) return;
      const r = dialog.getBoundingClientRect();
      const inside = e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
      if (!inside) closeDialog373(dialog);
    });
    dialog.addEventListener('close', () => {
      if (!document.querySelector('dialog[open]')) document.body.classList.remove('dialog-open373');
    });
  });

  // Simple browser speech input. Works mainly in Chrome/Edge on HTTPS.
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  document.querySelectorAll('textarea[data-voice-input]').forEach(textarea => {
    const wrap = document.createElement('div');
    wrap.className = 'voice-wrap';
    textarea.parentNode.insertBefore(wrap, textarea);
    wrap.appendChild(textarea);
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'mic-btn'; b.title = 'Nadiktovat text'; b.textContent = '🎙️';
    wrap.appendChild(b);
    if (!SpeechRecognition) { b.disabled = true; b.title = 'Diktování není v tomto prohlížeči podporované.'; return; }
    b.addEventListener('click', () => {
      const rec = new SpeechRecognition();
      rec.lang = 'cs-CZ'; rec.interimResults = false; rec.maxAlternatives = 1;
      b.classList.add('listening'); b.textContent = '●';
      rec.onresult = (ev) => {
        const text = ev.results[0][0].transcript;
        textarea.value = (textarea.value ? textarea.value + '\n' : '') + text;
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
      };
      rec.onend = () => { b.classList.remove('listening'); b.textContent = '🎙️'; };
      rec.onerror = () => { b.classList.remove('listening'); b.textContent = '🎙️'; };
      rec.start();
    });
  });
});

// Linda 0.4.4: blocking process overlay removed. Long work is reported non-blockingly by Linda status/toasts.

// Linda 3.7 – live autonomous status without page refresh.
(function(){
  const body = document.body;
  if (!body || body.dataset.statusPoll !== '1') return;
  let lastWork = null, lastUnread = null, busy = false;
  const isQueuePage = new URLSearchParams(location.search).get('page') === 'queue';

  function toast(text, kind='success', href='') {
    const stack = document.getElementById('toastStack37');
    if (!stack || !text) return;
    const el = document.createElement('div');
    el.className = `toast37 ${kind}`;
    el.innerHTML = `<b>${kind === 'error' ? 'Linda potřebuje pozornost' : 'Linda hlásí'}</b><span></span>`;
    el.querySelector('span').textContent = text;
    if (href) { el.classList.add('clickable069'); el.tabIndex = 0; el.setAttribute('role','link'); const go=()=>{location.href=href}; el.addEventListener('click',go); el.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();go();}}); }
    stack.appendChild(el);
    requestAnimationFrame(()=>el.classList.add('show'));
    setTimeout(()=>{el.classList.remove('show');setTimeout(()=>el.remove(),250)},5200);
  }
  window.lindaToast37 = toast;

  async function pollStatus(){
    if (busy || document.hidden) return;
    busy=true;
    try{
      const res=await fetch('?page=api_status37'+(isQueuePage?'&queue=1':''),{headers:{'X-Requested-With':'XMLHttpRequest'},cache:'no-store'});
      if(!res.ok) return;
      const data=await res.json(); if(!data.ok) return;
      const c=Number(data.work?.count||0), p=Number(data.work?.progress||0), unread=Number(data.unread||0);
      document.querySelectorAll('[data-work-label]').forEach(el=>el.textContent=c?`Fronta úloh · ${c}`:'Fronta úloh');
      document.querySelectorAll('[data-work-progress]').forEach(el=>el.textContent=c?`${p} %`:'');
      document.querySelectorAll('[data-work-indicator]').forEach(el=>{el.classList.toggle('working',c>0);el.classList.toggle('idle',c===0)});
      document.querySelectorAll('[data-notification-count]').forEach(el=>{el.textContent=unread;el.classList.toggle('hidden',unread===0)});
      document.querySelectorAll('[data-notification-list]').forEach(el=>{if(typeof data.notifications_html==='string') el.innerHTML=data.notifications_html});
      const live=document.querySelector('[data-live-queue]'); if(live && typeof data.queue_html==='string') live.innerHTML=data.queue_html;
      if(lastWork!==null && lastWork>0 && c===0) toast('Fronta je hotová. Výsledky můžeš zkontrolovat, až budeš mít čas.');
      if(lastUnread!==null && unread>lastUnread) toast('Máš nové upozornění od Lindy.');
      lastWork=c; lastUnread=unread;
    }catch(e){} finally{busy=false;}
  }
  pollStatus(); setInterval(pollStatus,isQueuePage?2500:8000);

  document.addEventListener('change',e=>{
    const master=e.target.closest('[data-select-all37]'); if(!master) return;
    const formId=master.dataset.bulkForm||'';
    const selector=formId?`input[name="selected[]"][form="${CSS.escape(formId)}"]`:'input[name="selected[]"]';
    document.querySelectorAll(selector).forEach(cb=>cb.checked=master.checked);
  });

  // Make background job submission feel instant: tell the user they can move on.
  document.addEventListener('submit',e=>{
    const form=e.target; if(!(form instanceof HTMLFormElement)||String(form.method).toLowerCase()!=='post')return;
    const action=String(new FormData(form).get('action')||'');
    const bg=['refresh_profile','analyze_brand','queue_web','generate_image','generate_carousel','publish_post','publish_blog','distribute_blog'];
    if(bg.includes(action) || form.dataset.backgroundJob === '1') toast('Úlohu předávám Lindě. Nemusíš čekat – můžeš pokračovat v práci.','info');
  },true);
})();
