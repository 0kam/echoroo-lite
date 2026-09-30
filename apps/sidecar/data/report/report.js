// One native player serves every reference and evidence clip, including on file URLs.
let player = null, activeButton = null;
document.querySelectorAll('[role="tablist"]').forEach(list => {
  const tabs = [...list.querySelectorAll('[role="tab"]')];
  const select = tab => {
    tabs.forEach(item => {
      const active = item === tab;
      item.setAttribute('aria-selected', String(active)); item.tabIndex = active ? 0 : -1;
      document.getElementById(item.getAttribute('aria-controls')).hidden = !active;
    });
    tab.focus();
  };
  list.addEventListener('click', event => { const tab = event.target.closest('[role="tab"]'); if (tab) select(tab); });
  list.addEventListener('keydown', event => {
    if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    event.preventDefault(); const current = tabs.indexOf(document.activeElement);
    const index = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (current + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    select(tabs[index]);
  });
});
document.addEventListener('click', async event => {
  const button = event.target.closest('button[data-audio]');
  if (!button) return;
  if (activeButton !== button) {
    if (player) { player.pause(); player.remove(); }
    if (activeButton) { activeButton.textContent = activeButton.dataset.play; activeButton.setAttribute('aria-pressed','false'); }
    activeButton = button;
    const audio = document.createElement('audio');
    player = audio;
    audio.controls = true; audio.preload = 'none'; audio.src = button.dataset.audio;
    audio.setAttribute('aria-label',button.getAttribute('aria-label'));
    button.after(audio);
    const update = () => {
      button.textContent = audio.paused ? button.dataset.play : button.dataset.pause;
      button.setAttribute('aria-pressed', String(!audio.paused));
    };
    audio.addEventListener('play',update); audio.addEventListener('pause',update); audio.addEventListener('ended',update);
  }
  if (player.paused) { try { await player.play(); } catch { player.focus(); } }
  else player.pause();
});
document.addEventListener('toggle',event => {
  if (event.target instanceof HTMLDetailsElement && !event.target.open && player && event.target.contains(player)) player.pause();
},true);
function revealTarget() {
  const target = document.getElementById(location.hash.slice(1));
  if (target instanceof HTMLDetailsElement) target.open = true;
  if (target) target.scrollIntoView({block: 'start'});
}
addEventListener('hashchange', revealTarget);
if (location.hash) revealTarget();
document.getElementById('copy').addEventListener('click', async event => {
  const button = event.currentTarget;
  const copy = document.querySelector('main').cloneNode(true);
  copy.querySelectorAll('.tab-panel[hidden]').forEach(node => node.removeAttribute('hidden'));
  copy.querySelectorAll('audio, img, nav, button, [data-nocopy], #audio').forEach(node => node.remove());
  copy.querySelectorAll('details, summary').forEach(node => {
    const replacement = document.createElement(node.tagName === 'SUMMARY' ? 'h2' : 'div');
    replacement.append(...node.childNodes); node.replaceWith(replacement);
  });
  const html = copy.innerHTML;
  const text = document.getElementById('plain-text').value;
  let success = false;
  try {
    await navigator.clipboard.write([new ClipboardItem({
      'text/html': new Blob([html], {type: 'text/html'}),
      'text/plain': new Blob([text], {type: 'text/plain'})
    })]);
    success = true;
  } catch {
    // A native copy event supports browsers that deny ClipboardItem on local files.
    const handler = event => {
      event.clipboardData.setData('text/html', html);
      event.clipboardData.setData('text/plain', text);
      event.preventDefault();
    };
    document.addEventListener('copy', handler);
    try { success = document.execCommand('copy'); } catch { /* Manual selection remains available. */ }
    finally { document.removeEventListener('copy', handler); }
  }
  document.getElementById('copy-status').textContent = button.dataset[success ? 'success' : 'failure'];
});
