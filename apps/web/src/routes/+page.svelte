<script lang="ts">
  import { resolve } from '$app/paths';
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  import { t } from '$lib/app.svelte';
  onMount(() => { void goto(resolve('/datasets'), { replaceState: true }); });
</script>
<div class="page"><p role="status">{t('loading')}</p></div>
