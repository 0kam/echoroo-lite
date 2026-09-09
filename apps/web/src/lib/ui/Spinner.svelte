<script lang="ts">
  let { size = 14 }: { size?: number } = $props();
</script>

<span class="spinner" style:width={`${size}px`} style:height={`${size}px`} aria-hidden="true"></span>

<style>
  .spinner { display: inline-block; flex: none; border-radius: 50%; border: 2px solid currentColor; border-right-color: transparent; animation: spin .7s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
