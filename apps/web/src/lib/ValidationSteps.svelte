<script lang="ts">
  import { t } from './app.svelte';
  import type { Validation } from './workflow';
  /** Shows where the user is in threshold setting → accuracy check → optional missed-call check.
   *  Without a validation, `listening` marks the first step as in progress (site-wide listening feeds it). */
  let { validation, listening = false, custom = false }: { validation: Validation | null; listening?: boolean; custom?: boolean } = $props();
  type Step = 'calibration' | 'evaluation' | 'recall';
  const steps = $derived<Step[]>([
    ...(custom || validation?.kind === 'custom' || validation && !validation.counts.calibration.total ? [] : ['calibration' as Step]),
    'evaluation', ...(!validation || validation.method === 'fixed_precision_audit_v1' ? ['recall' as Step] : [])]);
  function state(step: Step) {
    if (!validation) return listening && step === steps[0] ? 'current' : 'todo';
    const precisionDone = validation.stage === 'complete' || validation.audit_step === 'recall';
    if (step === 'calibration') return validation.stage === 'calibration' ? 'current' : 'done';
    if (step === 'evaluation') return validation.stage === 'calibration' ? 'todo' : precisionDone ? 'done' : 'current';
    const recall = validation.counts.recall;
    return validation.audit_step !== 'recall' ? 'todo' : recall?.total && recall.answered === recall.total ? 'done' : 'current';
  }
  const label = { calibration: 'vStepAdjust', evaluation: 'vStepPrecision', recall: 'vStepRecall' };
</script>
<ol class="validation-steps" aria-label={t('vSteps')}>{#each steps as step, index}{@const current = state(step)}
  <li class={current} aria-current={current === 'current' ? 'step' : undefined}><span class="number" aria-hidden="true">{current === 'done' ? '✓' : index + 1}</span>{t(label[step])}{#if current === 'done'}<span class="hidden"> · {t('vStepDone')}</span>{/if}</li>{/each}</ol>
<style>
  .validation-steps{display:flex;flex-wrap:wrap;gap:6px 20px;margin:4px 0 8px;padding:0;list-style:none;font-size:15px;color:rgb(var(--stone-700))}
  li{display:flex;align-items:center;gap:8px}li.current{font-weight:700;color:rgb(var(--primary-700))}:global(.dark) li.current{color:rgb(var(--primary-500))}
  .number{display:inline-grid;place-items:center;width:26px;height:26px;border-radius:50%;border:2px solid currentColor;font-size:13px;font-weight:700}li.current .number{border-width:3px}
  .hidden{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
</style>
