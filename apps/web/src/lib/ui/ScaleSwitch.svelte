<script lang="ts">
  import { app, t, setFrequencyScale } from '../app.svelte';
</script>

<!-- One app-wide choice; every spectrogram on the page reloads with it. -->
<div class="segmented" role="group" aria-label={t('scaleLabel')} title={t('scaleHelp')}>
  <button type="button" aria-pressed={app.frequencyScale === 'mel'} onclick={() => setFrequencyScale('mel')}>{t('scaleMel')}</button>
  <button type="button" aria-pressed={app.frequencyScale === 'linear'} onclick={() => setFrequencyScale('linear')}>{t('scaleLinear')}</button>
</div>

<style>
  /* In a narrow column a label wraps into two even lines ("低い音を / 見やすく") instead of leaving one character behind. */
  .segmented button { text-wrap: balance; line-height: 1.2; }
</style>
