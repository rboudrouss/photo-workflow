<script lang="ts">
	import { onMount } from 'svelte';
	import { api, media, isExplicit, type PhotoSummary, type Facets } from '$lib/api';
	import { blur } from '$lib/blur.svelte';

	let q = $state('');
	let mode = $state<'text' | 'vector'>('text');
	let scene = $state('');
	let decade = $state('');
	let nudity = $state<'all' | 'exclude' | 'only'>('all');
	let typeObjet = $state('');
	let vlm = $state('');
	let page = $state(1);
	let items = $state<PhotoSummary[]>([]);
	let total = $state<number | null>(null);
	let loading = $state(false);
	let error = $state('');
	let vectorSearch = $state(true);
	let facets = $state<Facets>({ types: [], scenes: [], decades: [], vlm: [] });

	async function load() {
		loading = true;
		error = '';
		try {
			const r = await api.photos({ q, mode, scene, decade, nudity, type_objet: typeObjet, vlm, page, page_size: 60 });
			items = r.items;
			total = r.total;
		} catch (e: any) {
			error = e.message;
		} finally {
			loading = false;
		}
	}

	function search(e: Event) {
		e.preventDefault();
		page = 1;
		load();
	}

	onMount(async () => {
		load();
		try {
			facets = await api.facets();
			const st = await api.stats();
			vectorSearch = st.config?.vector_search ?? true;
		} catch {}
	});
</script>

<form onsubmit={search} class="bar">
	<input bind:value={q} placeholder="Rechercher : plage, marin, voiture, 1930s..." size="40" />
	<select bind:value={mode}>
		<option value="text">texte (legendes)</option>
		{#if vectorSearch}<option value="vector">semantique (embeddings)</option>{/if}
	</select>
	<select bind:value={typeObjet} onchange={search}>
		<option value="">tous objets</option>
		{#each facets.types as f}<option value={f.value}>{f.value} ({f.count})</option>{/each}
	</select>
	<select bind:value={scene} onchange={search}>
		<option value="">toutes scenes</option>
		{#each facets.scenes as f}<option value={f.value}>{f.value} ({f.count})</option>{/each}
	</select>
	<select bind:value={decade} onchange={search}>
		<option value="">toutes epoques</option>
		{#each facets.decades as f}<option value={f.value}>{f.value} ({f.count})</option>{/each}
	</select>
	<select bind:value={nudity} onchange={search}>
		<option value="all">nudite : tout</option>
		<option value="exclude">sans nudite</option>
		<option value="only">nudite seulement</option>
	</select>
	<select bind:value={vlm} onchange={search}>
		<option value="">description : toutes</option>
		<option value="any">decrites par un VLM</option>
		<option value="none">jamais decrites</option>
		{#each facets.vlm as f}<option value={f.value}>par {f.value.replace(/^vlm:/, '')} ({f.count})</option>{/each}
	</select>
	<button type="submit">Chercher</button>
	<span class="muted">{total !== null ? `${total} photo(s)` : ''}{loading ? ' …' : ''}</span>
</form>

{#if error}<p style="color:#f66">{error}</p>{/if}

<div class="grid">
	{#each items as p (p.id)}
		<a class="card" href={`/photo/${p.id}`}>
			<img src={media(p.media.thumb)} alt={p.title ?? p.filename} loading="lazy" class:blur={blur.on && isExplicit(p.nudity_level)} />
			<div class="t" title={p.title ?? p.filename}>
				{p.title ?? p.filename}
				{#if p.score != null}<span class="muted"> {p.score.toFixed(2)}</span>{/if}
			</div>
		</a>
	{/each}
</div>

<div class="pager">
	<button disabled={page <= 1} onclick={() => { page--; load(); }}>Precedent</button>
	<span>page {page}</span>
	<button disabled={items.length < 60} onclick={() => { page++; load(); }}>Suivant</button>
</div>

<style>
	.blur {
		filter: blur(14px);
	}
	.bar {
		display: flex;
		gap: 0.5rem;
		flex-wrap: wrap;
		align-items: center;
		margin-bottom: 1rem;
	}
	.pager {
		display: flex;
		gap: 1rem;
		align-items: center;
		justify-content: center;
		margin: 1.5rem 0;
	}
</style>
