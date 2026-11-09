(() => {
  const q = (s, root=document) => root.querySelector(s);
  const qa = (s, root=document) => [...root.querySelectorAll(s)];

  // Mobile menu is handled centrally in app.js. Do not attach a second toggle handler here.

  q('[data-project-switch]')?.addEventListener('change', (e) => {
    const id = e.target.value;
    const page = document.body.dataset.currentProject && new URLSearchParams(location.search).get('page') || 'dashboard';
    const projectPages = new Set(['dashboard','calendar','content','activities','blog','media','analytics','approvals','project_setup','connectors','knowledge','references','linda','settings']);
    const target = projectPages.has(page) ? page : 'dashboard';
    location.href = `/?page=${encodeURIComponent(target)}&project_id=${encodeURIComponent(id)}`;
  });

  qa('[data-workflow-chip]').forEach((chip) => chip.addEventListener('click', () => {
    qa('[data-workflow-chip]').forEach(x => x.classList.remove('active'));
    chip.classList.add('active');
    const select = q('[name="workflow_key"]');
    if (select) select.value = chip.dataset.workflowChip || '';
    q('[name="message"]')?.focus();
  }));

  const chat = q('[data-chat-thread]');
  if (chat) chat.scrollTop = chat.scrollHeight;


  const chatBox = q('[data-chat-project]');
  if (chatBox && !chatBox.dataset.chatDirect) {
    const pid = chatBox.dataset.chatProject;
    let lastId = Math.max(0, ...qa('[data-chat-thread] .chat-bubble042 small').map(() => 0));
    const pollChat = async () => {
      try {
        const r = await fetch(`/?page=api_chat&project_id=${encodeURIComponent(pid)}`, {headers:{'Accept':'application/json'}});
        if (!r.ok) return;
        const data = await r.json();
        const msgs = Array.isArray(data.messages) ? data.messages : [];
        const latest = msgs.length ? Number(msgs[msgs.length-1].id || 0) : 0;
        const known = Number(chatBox.dataset.lastChatId || 0);
        if (known && latest > known) location.reload();
        if (!known) chatBox.dataset.lastChatId = String(latest);
      } catch (_) {}
    };
    setInterval(pollChat, 5000);
  }

  qa('[data-network-filter] input').forEach((el) => el.addEventListener('change', () => {
    el.closest('form')?.submit();
  }));

  // Keep dialog controls functional even if legacy JS is not loaded/cached yet.
  qa('[data-open-dialog]').forEach(btn => btn.addEventListener('click', () => document.getElementById(btn.dataset.openDialog)?.showModal()));
  qa('[data-close-dialog]').forEach(btn => btn.addEventListener('click', () => btn.closest('dialog')?.close()));
})();
