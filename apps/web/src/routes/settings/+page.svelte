<script lang="ts">
  import { t } from '$lib/app.svelte';
  import SpeciesNames from '$lib/SpeciesNames.svelte';
  import XenoCantoKey from '$lib/XenoCantoKey.svelte';
</script>

<div class="page">
  <div class="page-header"><div><h1>{t('navSettings')}</h1><p>{t('settingsIntro')}</p></div></div>
  <SpeciesNames />
  <section class="panel"><h2>{t('xenoCanto')}</h2><XenoCantoKey /></section>
</div>
