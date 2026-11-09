(() => {
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const markdown = (value) => {
    let s = esc(value);
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)]+|\/?\?page=[^)]+)\)/g, (_m,label,url) => {
      const external = /^https?:\/\//.test(url) ? ' target="_blank" rel="noopener"' : '';
      return `<a href="${url}"${external}>${label}</a>`;
    });
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/(^|\s)(https?:\/\/[^\s<]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
    const lines = s.split(/\r?\n/); let out=[], inList=false;
    lines.forEach(line => {
      const t=line.trim();
      if (/^[-•]\s+/.test(t)) { if(!inList){out.push('<ul>');inList=true;} out.push(`<li>${t.replace(/^[-•]\s+/,'')}</li>`); }
      else { if(inList){out.push('</ul>');inList=false;} out.push(line ? line : '<br>'); }
    });
    if(inList) out.push('</ul>');
    return out.join('<br>').replace(/<br><ul>/g,'<ul>').replace(/<\/ul><br>/g,'</ul>');
  };

  // Contextual help popovers.
  document.addEventListener('click', (e) => {
    const btn=e.target.closest('[data-help-toggle]');
    if (btn) {
      e.preventDefault(); e.stopPropagation();
      const wrap=btn.closest('.help046');
      document.querySelectorAll('.help046.open').forEach(x=>{if(x!==wrap)x.classList.remove('open')});
      wrap?.classList.toggle('open'); return;
    }
    if (!e.target.closest('.help046')) document.querySelectorAll('.help046.open').forEach(x=>x.classList.remove('open'));
  });

  // Notifications: floating dropdown + toasts for new completed work.
  const notificationMenu=document.querySelector('[data-notification-menu]');
  const preview=document.querySelector('[data-notification-preview]');
  let latestNotificationId=0;
  const notificationHtml=(n)=>`<a href="${esc(n.action_url||'?page=notifications')}" class="notification-item046 ${esc(n.level||'info')}"><strong>${esc(n.title||'Linda')}</strong><span>${esc(n.message||'')}</span><small>${esc(n.created_at||'')}</small></a>`;
  async function refreshNotifications(showToast=false){
    if(!preview) return;
    try{
      const r=await fetch('/?page=api_notifications',{headers:{'Accept':'application/json','X-Requested-With':'XMLHttpRequest'},cache:'no-store'}); if(!r.ok)return;
      const data=await r.json(); if(!data.ok)return;
      const items=Array.isArray(data.items)?data.items:[];
      preview.innerHTML=items.length?items.map(notificationHtml).join(''):'<div class="notification-empty046">Zatím žádná upozornění.</div>';
      const top=items.length?Number(items[0].id||0):0;
      if(showToast && latestNotificationId && top>latestNotificationId && window.lindaToast37){const n=items[0];window.lindaToast37(`${n.title||'Linda'}${n.message?` – ${n.message}`:''}`,n.level==='critical'?'error':'info',n.action_url||'')}
      if(top)latestNotificationId=Math.max(latestNotificationId,top);
    }catch(_e){}
  }
  notificationMenu?.addEventListener('toggle',()=>{if(notificationMenu.open)refreshNotifications(false)});
  refreshNotifications(false); setInterval(()=>refreshNotifications(true),12000);

  // Direct Linda chat.
  const form=document.querySelector('[data-linda-chat-form]');
  const thread=document.querySelector('[data-chat-thread]');
  if(form && thread){
    const textarea=form.querySelector('textarea[name="message"]');
    const submit=form.querySelector('[data-chat-submit]');
    const status=form.querySelector('[data-chat-status]');
    const lindaImg=document.querySelector('.coworker-person045 img')?.src || document.querySelector('.brand043 img')?.src || '';
    const idle='Enter odešle · Shift+Enter nový řádek';
    const stages=['Přemýšlím…','Analyzuji projekt…','Procházím web a obsah…','Kontroluji nastavení a možnosti…','Skládám doporučení…'];
    let sending=false;
    const scroll=()=>{thread.scrollTop=thread.scrollHeight};
    const stamp=()=>new Intl.DateTimeFormat('cs-CZ',{hour:'2-digit',minute:'2-digit'}).format(new Date());
    const setStatus=(text,error=false)=>{if(!status)return;status.textContent=text;status.classList.toggle('error',error)};
    const renderActions=(actions=[])=>{
      if(!Array.isArray(actions)||!actions.length)return null;
      const wrap=document.createElement('div');wrap.className='chat-actions-inline046';
      actions.slice(0,3).forEach(a=>{
        if(a.href){const x=document.createElement('a');x.className='chat-action046';x.href=a.href;x.textContent=a.label||'Otevřít';wrap.appendChild(x)}
        else if(a.action){const b=document.createElement('button');b.type='button';b.className='chat-action046';b.dataset.chatAction=a.action;b.dataset.projectId=form.querySelector('[name="project_id"]')?.value||'';b.textContent=a.label||'Provést';wrap.appendChild(b)}
      });return wrap;
    };
    const add=(role,text,thinking=false,actions=[])=>{
      thread.querySelector('.chat-empty045')?.remove();
      const row=document.createElement('div');row.className=`chat-msg045 ${role}${thinking?' thinking':''}`;
      if(role==='linda'&&lindaImg){const img=document.createElement('img');img.className='chat-avatar045';img.src=lindaImg;img.alt='Linda';row.appendChild(img)}
      const bubble=document.createElement('div');bubble.className='chat-bubble045';
      const body=document.createElement('div');body.className='chat-markup046';if(role==='linda'&&!thinking)body.innerHTML=markdown(text);else body.textContent=text;bubble.appendChild(body);
      if(!thinking){const small=document.createElement('small');small.textContent=stamp();bubble.appendChild(small)}
      row.appendChild(bubble);thread.appendChild(row);const actionsEl=renderActions(actions);if(actionsEl)thread.appendChild(actionsEl);scroll();return {row,body};
    };
    const fit=()=>{if(!textarea)return;textarea.style.height='auto';textarea.style.height=`${Math.min(170,Math.max(42,textarea.scrollHeight))}px`};
    textarea?.addEventListener('input',fit);
    textarea?.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();form.requestSubmit()}});
    form.addEventListener('submit',async e=>{
      e.preventDefault();e.stopPropagation();if(sending)return;const message=(textarea?.value||'').trim();if(!message)return;
      sending=true;add('user',message);textarea.value='';fit();textarea.disabled=true;submit.disabled=true;
      let stage=0;const thinking=add('linda',stages[0],true);setStatus(stages[0]);
      const timer=setInterval(()=>{stage=Math.min(stage+1,stages.length-1);if(thinking?.body)thinking.body.textContent=stages[stage];setStatus(stages[stage])},1500);
      try{
        const body=new FormData(form);body.set('message',message);
        const res=await fetch('/?page=api_chat_send',{method:'POST',body,headers:{'Accept':'application/json','X-Requested-With':'XMLHttpRequest'}});let data={};try{data=await res.json()}catch(_e){}
        clearInterval(timer);thinking?.row?.remove();if(!res.ok||!data.ok)throw new Error(data.detail||data.error||`HTTP ${res.status}`);
        add('linda',data.reply||'Hotovo.',false,data.actions||[]);const eng=data.engine||{};setStatus(eng.run_id?`AI Engine · ${eng.model||eng.provider||'hotovo'}`:idle);
      }catch(err){clearInterval(timer);thinking?.row?.remove();add('linda','Teď se mi nepodařilo dokončit odpověď. Zkus to prosím ještě jednou.');setStatus(`Chyba: ${err.message||err}`,true);textarea.value=message;fit()}
      finally{sending=false;textarea.disabled=false;submit.disabled=false;textarea.focus();scroll()}
    },true);
    scroll();fit();
  }

  // Execute safe, explicit actions proposed by Linda.
  document.addEventListener('click', async (e)=>{
    const btn=e.target.closest('[data-chat-action]'); if(!btn)return;
    e.preventDefault(); if(btn.disabled)return; btn.disabled=true; const old=btn.textContent;btn.textContent='Pracuji…';
    const chatForm=document.querySelector('[data-linda-chat-form]');const csrf=chatForm?.querySelector('[name="csrf"]')?.value||document.querySelector('input[name="csrf"]')?.value||'';
    const fd=new FormData();fd.set('csrf',csrf);fd.set('project_id',btn.dataset.projectId||document.body.dataset.currentProject||'');fd.set('chat_action',btn.dataset.chatAction||'');
    try{const r=await fetch('/?page=api_chat_action',{method:'POST',body:fd,headers:{'Accept':'application/json','X-Requested-With':'XMLHttpRequest'}});const d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error||`HTTP ${r.status}`);window.lindaToast37?.(d.message||'Hotovo.','success');if(d.href)setTimeout(()=>location.href=d.href,650)}catch(err){window.lindaToast37?.(err.message||'Akci se nepodařilo provést.','error');btn.disabled=false;btn.textContent=old}
  });

  // Shared post editor for Content and Calendar. The old details-popup editor
  // was difficult to use and made calendar actions navigate away from the board.
  const postEditor=document.getElementById('postEditorDialog');
  const postEditorForm=document.getElementById('postEditorForm');
  let activePostId=0;
  const editorFetch=async(url,options={})=>{
    const r=await fetch(url,{...options,headers:{'Accept':'application/json','X-Requested-With':'XMLHttpRequest',...(options.headers||{})}});
    let d={};try{d=await r.json()}catch(_e){}
    if(!r.ok||!d.ok)throw new Error(d.error||d.detail||`HTTP ${r.status}`);return d;
  };
  const editorPreview=(media)=>{
    const target=postEditor?.querySelector('[data-post-editor-preview]');if(!target)return;
    if(!media){target.innerHTML='<div class="post-editor-empty047">Bez obrázku nebo videa</div>';return;}
    const isVideo=String(media.kind||'').includes('video');
    target.innerHTML=isVideo?`<video controls preload="metadata" src="${esc(media.url)}"></video>`:`<img src="${esc(media.thumb_url||media.url)}" alt="${esc(media.title||'Médium')}">`;
  };
  const editorLibrary=(items=[])=>{
    const target=postEditor?.querySelector('[data-post-editor-library]');if(!target)return;
    if(!Array.isArray(items)||!items.length){target.innerHTML='<div class="post-editor-library-empty047">Knihovna je prázdná.</div>';return;}
    target.innerHTML=items.map(m=>{const video=String(m.kind||'').includes('video');const preview=video?'<div class="post-editor-video-placeholder047">▶ video</div>':`<img src="${esc(m.thumb_url||m.url)}" alt="">`;return `<button type="button" class="post-editor-library-item047" data-assign-media="${Number(m.id||0)}" title="Použít: ${esc(m.title||'Médium')}">${preview}<span>${esc(m.title||'Médium')}</span></button>`}).join('');
  };
  const fillEditor=(d)=>{
    if(!postEditor||!postEditorForm)return;const p=d.post||{};activePostId=Number(p.id||0);
    postEditorForm.elements.post_id.value=activePostId;
    ['title','platform','post_type','body','hashtags','scheduled_at','destination_url','image_prompt','status','notes'].forEach(k=>{if(postEditorForm.elements[k])postEditorForm.elements[k].value=p[k]??''});
    const title=postEditor.querySelector('[data-post-editor-title]');if(title)title.textContent=p.title||'Bez názvu';
    const subtitle=postEditor.querySelector('[data-post-editor-subtitle]');if(subtitle)subtitle.textContent=`${p.platform||''} · ${p.post_type||''}${p.publication_hold?' · publikace pozastavena':''}`;
    const state=postEditor.querySelector('[data-post-editor-status]');if(state)state.textContent=p.status||'';
    const linkState=postEditor.querySelector('[data-post-editor-link-status]');if(linkState){const st=p.destination_status||'';linkState.textContent=p.permalink?`Publikováno: ${p.permalink}`:(st==='ok'?'Odkaz ověřen.':st==='error'?'Odkaz je potřeba opravit.':p.destination_url?'Odkaz čeká na ověření.':'Odkaz není vyplněný.');linkState.dataset.state=st||'';}
    const catWrap=postEditor.querySelector('[data-post-editor-wp-category]');const catSel=postEditorForm.elements.wp_category_id;const catHint=postEditor.querySelector('[data-post-editor-category-hint]');
    if(catWrap&&catSel){const isBlog=p.platform==='blog';catWrap.hidden=!isBlog;catSel.innerHTML='<option value="">Linda vybere automaticky</option>';if(isBlog){(d.wp_categories||[]).forEach(c=>{const o=document.createElement('option');o.value=String(c.id);o.textContent=c.name||c.slug||`Rubrika #${c.id}`;if(d.suggested_category&&Number(d.suggested_category.id)===Number(c.id))o.selected=true;catSel.appendChild(o)});if(catHint)catHint.textContent=d.suggested_category?`Linda doporučuje: ${d.suggested_category.name}. Můžeš změnit.`:'Rubriku Linda vybere při publikaci.';}}
    editorPreview(d.current_media);editorLibrary(d.media_library||[]);
  };
  const openPostEditor=async(postId)=>{
    if(!postEditor||!postEditorForm)return;activePostId=Number(postId||0);if(!activePostId)return;
    try{
      if(typeof postEditor.showModal==='function'&&!postEditor.open)postEditor.showModal();else postEditor.setAttribute('open','');
      document.body.classList.add('post-editor-open047');
      postEditor.querySelector('[data-post-editor-title]').textContent='Načítám…';
      const d=await editorFetch(`/?page=api_post_detail&post_id=${activePostId}`);fillEditor(d);
    }catch(err){window.lindaToast37?.(`Detail se nepodařilo načíst: ${err.message}`,'error');if(postEditor.open)postEditor.close?.()}
  };
  const closePostEditor=()=>{if(!postEditor)return;if(typeof postEditor.close==='function'&&postEditor.open)postEditor.close();else postEditor.removeAttribute('open');document.body.classList.remove('post-editor-open047')};
  document.addEventListener('click',e=>{
    const opener=e.target.closest('[data-post-editor]');if(opener){e.preventDefault();e.stopPropagation();openPostEditor(opener.dataset.postEditor);return;}
    if(e.target.closest('[data-post-editor-close]')){e.preventDefault();closePostEditor();return;}
  });
  postEditor?.addEventListener('click',e=>{if(e.target===postEditor)closePostEditor()});
  postEditor?.addEventListener('close',()=>document.body.classList.remove('post-editor-open047'));
  postEditorForm?.addEventListener('submit',async e=>{
    e.preventDefault();const fd=new FormData(postEditorForm);fd.set('action','save');
    try{const d=await editorFetch('/?page=api_post_action',{method:'POST',body:fd});window.lindaToast37?.(d.message||'Uloženo.','success');closePostEditor();setTimeout(()=>location.reload(),250)}catch(err){window.lindaToast37?.(`Uložení se nepodařilo: ${err.message}`,'error')}
  });
  document.addEventListener('click',async e=>{
    const actionBtn=e.target.closest('[data-post-editor-action]');if(!actionBtn||!activePostId||!postEditorForm)return;
    e.preventDefault();const action=actionBtn.dataset.postEditorAction||'';if(action==='delete'&&!confirm('Smazat tento příspěvek?'))return;
    const old=actionBtn.textContent;actionBtn.disabled=true;actionBtn.textContent='Pracuji…';
    const fd=new FormData();fd.set('csrf',postEditorForm.elements.csrf.value);fd.set('post_id',String(activePostId));fd.set('action',action);
    if(postEditorForm.elements.ai_instruction)fd.set('ai_instruction',postEditorForm.elements.ai_instruction.value||'');
    if(postEditorForm.elements.wp_category_id)fd.set('wp_category_id',postEditorForm.elements.wp_category_id.value||'');
    try{const d=await editorFetch('/?page=api_post_action',{method:'POST',body:fd});window.lindaToast37?.(d.message||'Hotovo.','success');
      if(action==='verify_link'){if(d.url&&postEditorForm.elements.destination_url)postEditorForm.elements.destination_url.value=d.url;const ls=postEditor.querySelector('[data-post-editor-link-status]');if(ls){ls.textContent=d.message||'Odkaz ověřen.';ls.dataset.state=d.status||'';}actionBtn.disabled=false;actionBtn.textContent=old;return;}
      if(d.deleted||action==='publish'){closePostEditor();setTimeout(()=>location.reload(),350)}else if(action==='generate_image'||action==='regenerate_text'){actionBtn.textContent='Předáno Lindě';closePostEditor();setTimeout(()=>location.reload(),350);return}}
    catch(err){window.lindaToast37?.(`Akce se nepodařila: ${err.message}`,'error')}
    actionBtn.disabled=false;actionBtn.textContent=old;
  });
  document.addEventListener('click',async e=>{
    const media=e.target.closest('[data-assign-media]');if(!media||!activePostId||!postEditorForm)return;e.preventDefault();
    const fd=new FormData();fd.set('csrf',postEditorForm.elements.csrf.value);fd.set('post_id',String(activePostId));fd.set('action','assign_media');fd.set('media_id',media.dataset.assignMedia||'');
    try{const d=await editorFetch('/?page=api_post_action',{method:'POST',body:fd});editorPreview({thumb_url:d.thumb_url,url:d.thumb_url,title:'Vybrané médium'});window.lindaToast37?.(d.message||'Médium přiřazeno.','success')}catch(err){window.lindaToast37?.(`Médium se nepodařilo přiřadit: ${err.message}`,'error')}
  });

  postEditorForm?.elements.platform?.addEventListener('change',()=>{const wrap=postEditor?.querySelector('[data-post-editor-wp-category]');if(wrap)wrap.hidden=postEditorForm.elements.platform.value!=='blog';});
  const uploadTrigger=postEditor?.querySelector('[data-post-editor-upload-trigger]');const uploadInput=postEditor?.querySelector('[data-post-editor-upload]');
  uploadTrigger?.addEventListener('click',()=>uploadInput?.click());
  uploadInput?.addEventListener('change',async()=>{const file=uploadInput.files?.[0];if(!file||!activePostId||!postEditorForm)return;const fd=new FormData();fd.set('csrf',postEditorForm.elements.csrf.value);fd.set('post_id',String(activePostId));fd.set('action','upload_media');fd.set('file',file);uploadTrigger.disabled=true;const old=uploadTrigger.textContent;uploadTrigger.textContent='Nahrávám…';try{const d=await editorFetch('/?page=api_post_action',{method:'POST',body:fd});editorPreview({thumb_url:d.thumb_url,url:d.thumb_url,title:file.name});window.lindaToast37?.(d.message||'Médium nahráno.','success')}catch(err){window.lindaToast37?.(`Nahrání selhalo: ${err.message}`,'error')}finally{uploadTrigger.disabled=false;uploadTrigger.textContent=old;uploadInput.value=''}});

  // Calendar drag & drop. Uses a dedicated JSON endpoint and reloads after a
  // successful drop so the board always reflects the persisted DB state.
  let dragged=null;
  const calendarRoot=document.querySelector('[data-calendar-root]');
  const calendarCsrf=calendarRoot?.dataset.csrf||document.querySelector('input[name="csrf"]')?.value||'';
  document.querySelectorAll('[data-calendar-card]').forEach(card=>{
    card.querySelectorAll('img,a,button,input,select,textarea,summary').forEach(el=>el.setAttribute('draggable','false'));
    card.addEventListener('dragstart',e=>{if(e.target.closest('a,button,input,select,textarea,summary,details')){e.preventDefault();return;}dragged=card;card.classList.add('dragging046');e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/plain',card.dataset.postId||'')});
    card.addEventListener('dragend',()=>{card.classList.remove('dragging046');document.querySelectorAll('.drag-over046').forEach(x=>x.classList.remove('drag-over046'));dragged=null});
  });
  document.querySelectorAll('[data-calendar-drop]').forEach(slot=>{
    slot.addEventListener('dragenter',e=>{if(!dragged)return;e.preventDefault();slot.classList.add('drag-over046')});
    slot.addEventListener('dragover',e=>{if(!dragged)return;e.preventDefault();slot.classList.add('drag-over046');e.dataTransfer.dropEffect='move'});
    slot.addEventListener('dragleave',e=>{if(!slot.contains(e.relatedTarget))slot.classList.remove('drag-over046')});
    slot.addEventListener('drop',async e=>{
      e.preventDefault();e.stopPropagation();slot.classList.remove('drag-over046');const postId=e.dataTransfer.getData('text/plain')||dragged?.dataset.postId;if(!postId)return;
      const fd=new FormData();fd.set('csrf',calendarCsrf);fd.set('post_id',postId);fd.set('date',slot.dataset.date||'');fd.set('platform',slot.dataset.platform||'');
      try{const d=await editorFetch('/?page=api_calendar_move',{method:'POST',body:fd});window.lindaToast37?.(`Přesunuto na ${slot.dataset.date}.`,'success');setTimeout(()=>location.reload(),250)}catch(err){window.lindaToast37?.(`Přesun se nepodařil: ${err.message}`,'error')}
    });
  });

  // Queue live state. It also lets the server repair abandoned running rows and
  // legacy queued chat jobs without requiring a manual DB intervention.
  const queueList=document.querySelector('[data-queue-live047]');
  const queueSummary=document.querySelector('[data-queue-summary]');
  const postStates=[...document.querySelectorAll('[data-post-job-state]')];
  let reloadPending=false;
  async function refreshQueue047(){
    if(!queueList&&!queueSummary&&!postStates.length)return;
    try{
      const d=await editorFetch('/?page=api_queue_status');
      const items=d.items||[];const map=new Map(items.map(x=>[String(x.id),x]));
      if(queueList){
        queueList.querySelectorAll('[data-job-id]').forEach(row=>{const q=map.get(row.dataset.jobId);if(!q)return;const previous=row.dataset.status||'';row.dataset.status=q.status;row.querySelector('[data-job-status]')?.replaceChildren(document.createTextNode(q.status_label||q.status));row.querySelector('[data-job-progress]')?.replaceChildren(document.createTextNode(`${q.progress||0} %`));row.querySelector('[data-job-message]')?.replaceChildren(document.createTextNode(q.message||''));row.querySelector('[data-job-updated]')?.replaceChildren(document.createTextNode(q.updated_at?`Aktualizováno ${q.updated_at}`:''));if(previous&&previous!==q.status&&!reloadPending){reloadPending=true;setTimeout(()=>location.reload(),600);}});
        const hb=document.querySelector('[data-cron-heartbeat]');if(hb){const last=d.cron?.linda_cron_last_run;hb.textContent=last?`Background worker naposledy: ${last}`:'Pozor: background worker zatím nemá zaznamenaný automatický běh. Fronta se sama neposune.';hb.classList.toggle('warning047',!last);}
      }
      if(queueSummary&&d.summary){const changed=String(d.summary.queued)!==queueSummary.dataset.queued||String(d.summary.running)!==queueSummary.dataset.running||String(d.summary.failed)!==queueSummary.dataset.failed;if(changed&&!reloadPending){reloadPending=true;setTimeout(()=>location.reload(),700);}}
      if(postStates.length){
        const activeByPost=new Map();items.filter(q=>(q.status==='queued'||q.status==='running')&&q.post_id).forEach(q=>{if(!activeByPost.has(String(q.post_id))||q.status==='running')activeByPost.set(String(q.post_id),q)});
        postStates.forEach(el=>{const q=activeByPost.get(el.dataset.postId||'');const wasActive=el.classList.contains('active');if(q){el.classList.add('active');el.dataset.jobId=String(q.id);el.innerHTML=`<span class="spinner065"></span><div><b>${q.type_label||'Linda pracuje na změně'}</b><small>${q.status==='running'?'Probíhá':'Čeká ve frontě'} · ${q.progress||0} %</small></div>`;}else if(wasActive&&el.dataset.jobId&&!reloadPending){reloadPending=true;setTimeout(()=>location.reload(),650);}});
      }
    }catch(_e){}
  }
  refreshQueue047();if(queueList||queueSummary||postStates.length)setInterval(refreshQueue047,4000);

})();

// Linda 0.4.6.3: ruční přidání příspěvku/článku přímo z kalendáře.
(() => {
  const dialog=document.querySelector('[data-calendar-create-dialog]');
  const form=document.querySelector('[data-calendar-create-form]');
  if(!dialog||!form)return;
  const open=()=>{if(typeof dialog.showModal==='function')dialog.showModal();else dialog.setAttribute('open','');};
  const close=()=>{if(typeof dialog.close==='function'&&dialog.open)dialog.close();else dialog.removeAttribute('open');};
  document.querySelector('[data-calendar-create-open]')?.addEventListener('click',open);
  document.querySelectorAll('[data-calendar-create-close]').forEach(b=>b.addEventListener('click',close));
  const kind=form.querySelector('[data-calendar-kind]');
  const platformWrap=form.querySelector('[data-calendar-platform-wrap]');
  const formatWrap=form.querySelector('[data-calendar-format-wrap]');
  const syncKind=()=>{const article=kind?.value==='article';if(platformWrap)platformWrap.hidden=article;if(formatWrap)formatWrap.hidden=article;};
  kind?.addEventListener('change',syncKind);syncKind();
  form.addEventListener('submit',async(e)=>{
    e.preventDefault();
    const btn=form.querySelector('button[type="submit"]');const old=btn?.textContent||'';if(btn){btn.disabled=true;btn.textContent='Předávám Lindě…';}
    try{
      const r=await fetch('/?page=api_calendar_create',{method:'POST',body:new FormData(form),headers:{'X-Requested-With':'XMLHttpRequest'}});
      const d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error||'Úlohu se nepodařilo vytvořit.');
      window.lindaToast37?.(d.message||'Obsah byl přidán do fronty.','success');close();setTimeout(()=>location.reload(),500);
    }catch(err){window.lindaToast37?.(err.message||'Přidání obsahu selhalo.','error');}
    finally{if(btn){btn.disabled=false;btn.textContent=old;}}
  });
})();
