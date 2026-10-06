<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { navigating } from '$app/state';
	import { api, media, isExplicit, PAGE_SIZE, type Facets } from '$lib/api';
	import { blur } from '$lib/blur.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// Champs du formulaire : recopies de l'URL a chaque navigation (retour arriere compris), modifies localement.
	let q = $state('');
	let mode = $state<'text' | 'vector'>('text');
	let scene = $state('');
	let decade = $state('');
	let nudity = $state<'all' | 'exclude' | 'only'>('all');
	let typeObjet = $state('');
	let vlm = $state('');
	$effect.pre(() => {
		({ q, mode, scene, decade, nudity, vlm } = data.params);
		typeObjet = data.params.type_objet;
	});

	let vectorSearch = $state(true);
	let facets = $state<Facets>({ types: [], scenes: [], decades: [], vlm: [] });
	let loading = $derived(navigating.to?.url.pathname === '/');

	function go(page: number) {
		const params: Record<string, string> = { q, mode, scene, decade, nudity, type_objet: typeObjet, vlm, page: String(page) };
		const defaults: Record<string, string> = { mode: 'text', nudity: 'all', page: '1' };
		const u = new URLSearchParams();
		for (const [k, v] of Object.entries(params)) if (v && v !== defaults[k]) u.set(k, v);
		const qs = u.toString();
		goto(qs ? `/?${qs}` : '/', { keepFocus: true });
	}

	function search(e: Event) {
		e.preventDefault();
		go(1);
	}

	onMount(async () => {
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
	<span class="muted">{data.total !== null ? `${data.total} photo(s)` : ''}{loading ? ' …' : ''}</span>
</form>

{#if data.error}<p style="color:#f66">{data.error}</p>{/if}

<div class="grid">
	{#each data.items as p (p.id)}
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
	<button disabled={data.params.page <= 1} onclick={() => go(data.params.page - 1)}>Precedent</button>
	<span>page {data.params.page}</span>
	<button disabled={data.items.length < PAGE_SIZE} onclick={() => go(data.params.page + 1)}>Suivant</button>
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
