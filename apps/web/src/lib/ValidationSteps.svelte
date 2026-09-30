<script lang="ts">
  import { t } from './app.svelte';
  import type { Validation } from './workflow';
  /** Shows where the user is in setting the threshold → checking accuracy → optional missed-call check, with each step's sound count. */
  let { validation, review = false }: { validation: Validation | null; review?: boolean } = $props();
  type Step = 'calibration' | 'evaluation' | 'recall';
  const steps: Step[] = ['calibration', 'evaluation', 'recall'];
  function state(step: Step) {
    if (!validation) return 'todo';
    if (review && validation.question) {
      const current = validation.question.phase === 'calibration' ? step === 'calibration'
        : validation.question.samples.some(sample => sample.sample === (step === 'recall' ? 'recall' : step === 'evaluation' ? 'precision' : ''));
      return current ? 'current' : 'todo';
    }
    // Without a threshold, ② and ③ never start.
    if (validation.status === 'undecided' && step !== 'calibration') return 'todo';
    const precisionDone = validation.stage === 'complete' || validation.audit_step === 'recall';
    if (step === 'calibration') return validation.stage === 'calibration' ? 'current' : 'done';
    if (step === 'evaluation') return validation.stage === 'calibration' ? 'todo' : precisionDone ? 'done' : 'current';
    const recall = validation.counts.recall;
    return validation.audit_step !== 'recall' ? 'todo' : recall?.total && recall.answered === recall.total ? 'done' : 'current';
  }
  const label = { calibration: 'vStepAdjust', evaluation: 'vStepPrecision', recall: 'vStepRecall' };
  // The real number of sounds: fewer when a model has fewer windows.
  const count = (step: Step) => validation?.counts[step]?.total || 0;
</script>
<ol class="validation-steps" aria-label={t('vSteps')}>{#each steps as step, index}{@const current = state(step)}
  <li class={current} aria-current={current === 'current' ? 'step' : undefined}><span class="number" aria-hidden="true">{current === 'done' ? '✓' : index + 1}</span>{t(label[step])}{#if count(step)}<span class="count">· {t('vStepCount').replace('{n}', String(count(step)))}</span>{/if}{#if current === 'done'}<span class="hidden"> · {t('vStepDone')}</span>{/if}</li>{/each}</ol>
<style>
  .validation-steps{display:flex;flex-wrap:wrap;gap:6px 20px;margin:4px 0 8px;padding:0;list-style:none;font-size:15px;color:rgb(var(--stone-700))}
  li{display:flex;align-items:center;gap:8px}li.current{font-weight:700;color:rgb(var(--primary-700))}:global(.dark) li.current{color:rgb(var(--primary-500))}
  .number{display:inline-grid;place-items:center;width:26px;height:26px;border-radius:50%;border:2px solid currentColor;font-size:13px;font-weight:700}li.current .number{border-width:3px}
  .count{font-weight:400;margin-left:.3em}
  .hidden{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
</style>
