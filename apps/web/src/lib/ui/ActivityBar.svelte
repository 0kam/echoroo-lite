<script lang="ts">
  import { activity } from '../api.svelte';
  // Show the bar only for requests that take longer than a blink, so quick calls do not flicker.
  let visible = $state(false);
  $effect(() => {
    if (!activity.count) { visible = false; return; }
    const timer = setTimeout(() => visible = true, 150);
    return () => clearTimeout(timer);
  });
</script>

<div class="activity" class:visible aria-hidden="true"></div>

<style>
  .activity { position: fixed; top: 0; left: 0; right: 0; height: 3px; z-index: 100; pointer-events: none; opacity: 0; transition: opacity .2s; overflow: hidden; }
  .activity.visible { opacity: 1; }
  .activity::after { content: ''; position: absolute; inset: 0; width: 40%; background: rgb(var(--primary-500)); animation: slide 1.1s ease-in-out infinite; }
  @keyframes slide { from { transform: translateX(-100%); } to { transform: translateX(250%); } }
</style>
