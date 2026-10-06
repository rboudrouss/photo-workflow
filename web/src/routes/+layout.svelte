<script lang="ts">
	let { children } = $props();
</script>

<nav>
	<a href="/" class="brand">photoflow</a>
	<a href="/">Photos</a>
	<a href="/faces">Visages</a>
	<a href="/series">Séries</a>
	<a href="/stats">Statut</a>
</nav>
<main>
	{@render children()}
</main>

<style>
	:global(body) {
		margin: 0;
		font-family: system-ui, sans-serif;
		background: #111;
		color: #eee;
	}
	:global(a) {
		color: #9cf;
	}
	nav {
		display: flex;
		gap: 1.2rem;
		align-items: center;
		padding: 0.6rem 1rem;
		background: #1c1c1c;
		border-bottom: 1px solid #333;
	}
	nav a {
		color: #ddd;
		text-decoration: none;
	}
	.brand {
		font-weight: 700;
		color: #fff;
	}
	main {
		padding: 1rem;
	}
	:global(.grid) {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
		gap: 0.6rem;
	}
	:global(.card) {
		background: #1c1c1c;
		border-radius: 6px;
		overflow: hidden;
		display: block;
		color: inherit;
		text-decoration: none;
	}
	:global(.card img) {
		width: 100%;
		aspect-ratio: 4/3;
		object-fit: cover;
		display: block;
		background: #000;
	}
	:global(.card .t) {
		font-size: 0.8rem;
		padding: 0.4rem;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	:global(input, select, textarea, button) {
		font: inherit;
		background: #222;
		color: #eee;
		border: 1px solid #444;
		border-radius: 4px;
		padding: 0.4rem 0.6rem;
	}
	:global(button) {
		cursor: pointer;
	}
	:global(.muted) {
		color: #999;
		font-size: 0.85rem;
	}
	:global(.tag) {
		display: inline-block;
		background: #2a2a2a;
		border-radius: 999px;
		padding: 0.1rem 0.6rem;
		margin: 0.15rem;
		font-size: 0.8rem;
	}
</style>
