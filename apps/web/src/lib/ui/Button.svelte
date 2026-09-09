<script lang="ts">
  import type { Snippet } from 'svelte';
  import type { HTMLButtonAttributes } from 'svelte/elements';
  import Spinner from './Spinner.svelte';
  let { pending = false, variant = 'default', size = 'md', type = 'button', href, active = false, children, class: className = '', disabled,
    'aria-label': ariaLabel, ...rest }:
    HTMLButtonAttributes & { pending?: boolean; variant?: 'default' | 'primary' | 'ghost' | 'danger'; size?: 'sm' | 'md' | 'lg';
      href?: string; active?: boolean; children: Snippet; class?: string } = $props();
</script>

{#if href && !disabled && !pending}
  <a {href} aria-label={ariaLabel} class={`button ${variant} ${size} ${className}`} class:active>{@render children()}</a>
{:else}
  <button {...rest} {type} class={`button ${variant} ${size} ${className}`} class:active class:pending
    disabled={disabled || pending} aria-label={ariaLabel} aria-busy={pending || rest['aria-busy'] || undefined} aria-pressed={rest['aria-pressed']}>
    {#if pending}<Spinner size={size === 'sm' ? 12 : 14} />{/if}
    {@render children()}
  </button>
{/if}
