<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { navigating } from '$app/state';
	import { api, media, isExplicit, PAGE_SIZE, DELCAMPE_STATUSES, type Facets } from '$lib/api';
	import { blur } from '$lib/blur.svelte';
	import { selection, setSelected, clearSelection } from '$lib/selection.svelte';
	import TagFilter from '$lib/TagFilter.svelte';
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
	let statut = $state('');
	let tags = $state<string[]>([]);
	let sort = $state<'recent' | 'vente' | 'instagram'>('recent');
	$effect.pre(() => {
		({ q, mode, scene, decade, nudity, vlm, sort, statut } = data.params);
		typeObjet = data.params.type_objet;
		tags = data.params.tag;
	});

	let vectorSearch = $state(true);
	let facets = $state<Facets>({ statuts: [], types: [], scenes: [], decades: [], vlm: [] });
	const facetCount = (k: string) => facets.statuts.find((f) => f.value === k)?.count ?? 0;
	// Un tag porte par toutes les photos affichees ne filtrerait rien : on ne le propose pas.
	let refine = $derived(data.refine.filter((t) => data.total === null || t.count < data.total));
	let loading = $derived(navigating.to?.url.pathname === '/');

	function go(page: number) {
		const params: Record<string, string> = { q, mode, scene, decade, nudity, type_objet: typeObjet, vlm, statut, sort, page: String(page) };
		const defaults: Record<string, string> = { mode: 'text', nudity: 'all', sort: 'recent', page: '1' };
		const u = new URLSearchParams();
		for (const [k, v] of Object.entries(params)) if (v && v !== defaults[k]) u.set(k, v);
		for (const t of tags) u.append('tag', t);
		const qs = u.toString();
		goto(qs ? `/?${qs}` : '/', { keepFocus: true });
	}

	// Selection : clic sur la coche ; Maj+clic coche ou decoche toute la plage depuis le clic precedent.
	let last = -1;
	function pick(e: MouseEvent, i: number) {
		const on = !selection.has(data.items[i].id);
		const [a, b] = e.shiftKey && last >= 0 ? [Math.min(last, i), Math.max(last, i)] : [i, i];
		setSelected(data.items.slice(a, b + 1).map((p) => p.id), on);
		last = i;
	}
	let pageIds = $derived(data.items.map((p) => p.id));
	let pageAll = $derived(pageIds.length > 0 && pageIds.every((id) => selection.has(id)));
	let selecting = $state(false);
	let canSelectAll = $derived(data.total !== null && data.total > 0 && !(data.params.q && data.params.mode === 'vector'));

	async function selectAll() {
		selecting = true;
		try {
			const r = await api.photoIds({ ...data.filters, sort: data.params.sort });
			setSelected(r.ids, true);
			if (r.truncated) alert(`Selection limitee aux ${r.ids.length} premieres photos.`);
		} catch (e: any) {
			alert(e.message);
		} finally {
			selecting = false;
		}
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
	<select bind:value={vlm} onchange={search} class="narrow">
		<option value="">description : toutes</option>
		<option value="any">decrites par un VLM</option>
		<option value="none">jamais decrites</option>
		{#each facets.vlm as f}<option value={f.value}>par {f.value.replace(/^vlm:/, '')} ({f.count})</option>{/each}
	</select>
	<select bind:value={statut} onchange={search} class="narrow">
		<option value="">delcampe : tout</option>
		<option value="a_publier">a publier</option>
		<option value="aucun">sans statut ({facetCount('aucun')})</option>
		{#each Object.entries(DELCAMPE_STATUSES) as [k, label]}<option value={k}>{label} ({facetCount(k)})</option>{/each}
	</select>
	<select bind:value={sort} onchange={search} title="Tri (hors recherche semantique, triee par proximite)">
		<option value="recent">tri : decrites, recentes</option>
		<option value="vente">tri : potentiel de vente</option>
		<option value="instagram">tri : potentiel Instagram</option>
	</select>
	<TagFilter selected={tags} filters={data.filters} onchange={(t) => { tags = t; go(1); }} />
	<button type="submit">Chercher</button>
	<span class="muted">{data.total !== null ? `${data.total} photo(s)` : ''}{loading ? ' …' : ''}</span>
</form>

{#if refine.length}
	<div class="refine">
		<span class="muted">Affiner :</span>
		{#each refine as t (t.value)}
			<button type="button" class="tag" onclick={() => { tags = [...tags, t.value]; go(1); }}>{t.value} <span class="muted">{t.count}</span></button>
		{/each}
	</div>
{/if}

{#if data.error}<p style="color:#f66">{data.error}</p>{/if}

<div class="grid" class:selecting={selection.size > 0}>
	{#each data.items as p, i (p.id)}
		<div class="cell" class:selected={selection.has(p.id)}>
			<a class="card" href={`/photo/${p.id}`}>
				<img src={media(p.media.thumb)} alt={p.title ?? p.filename} loading="lazy" class:blur={blur.on && isExplicit(p.nudity_level)} />
				{#if p.potentiel}
					<span class="pot" title={`potentiel de vente ${p.potentiel.vente}/10, Instagram ${p.potentiel.instagram}/10`}>
						<b class:hi={p.potentiel.vente >= 7}>V{p.potentiel.vente}</b><b class:hi={p.potentiel.instagram >= 7}>I{p.potentiel.instagram}</b>
					</span>
				{/if}
				{#if p.delcampe_status}<span class="st st-{p.delcampe_status}">{DELCAMPE_STATUSES[p.delcampe_status]}</span>{/if}
				<div class="t" title={p.title ?? p.filename}>
					{p.title ?? p.filename}
					{#if p.score != null}<span class="muted"> {p.score.toFixed(2)}</span>{/if}
				</div>
			</a>
			<button
				type="button"
				class="pick"
				aria-pressed={selection.has(p.id)}
				aria-label={selection.has(p.id) ? 'retirer de la selection' : 'selectionner'}
				title="Selectionner (Maj+clic : toute la plage)"
				onclick={(e) => pick(e, i)}>✓</button
			>
		</div>
	{/each}
</div>

<div class="pager">
	<button disabled={data.params.page <= 1} onclick={() => go(data.params.page - 1)}>Precedent</button>
	<span>page {data.params.page}</span>
	<button disabled={data.items.length < PAGE_SIZE} onclick={() => go(data.params.page + 1)}>Suivant</button>
</div>

<div class="selbar">
	{#if selection.size}
		<b>{selection.size} selectionnee(s)</b>
	{:else}
		<span class="muted">Cocher des photos pour les exporter vers Delcampe</span>
	{/if}
	<button type="button" onclick={() => setSelected(pageIds, !pageAll)} disabled={!pageIds.length}>
		{pageAll ? 'Decocher la page' : 'Cocher la page'}
	</button>
	{#if canSelectAll}
		<button type="button" onclick={selectAll} disabled={selecting}>Cocher les {data.total} resultats{selecting ? ' …' : ''}</button>
	{/if}
	{#if selection.size}
		<button type="button" onclick={() => confirm(`Vider la selection (${selection.size}) ?`) && clearSelection()}>Vider</button>
		<a class="go" href="/export">Exporter pour Delcampe →</a>
	{/if}
</div>

<style>
	.cell {
		position: relative;
	}
	.cell.selected :global(.card) {
		outline: 2px solid #9cf;
		outline-offset: -2px;
	}
	.pick {
		position: absolute;
		top: 0.35rem;
		left: 0.35rem;
		width: 1.6rem;
		height: 1.6rem;
		padding: 0;
		border-radius: 50%;
		border: 2px solid #fff;
		background: #0008;
		color: transparent;
		font-size: 0.9rem;
		line-height: 1;
		opacity: 0;
		transition: opacity 0.1s;
	}
	.cell:hover .pick,
	.selecting .pick,
	.pick:focus-visible {
		opacity: 1;
	}
	@media (hover: none) {
		.pick {
			opacity: 0.8;
		}
	}
	.pick[aria-pressed='true'] {
		background: #39f;
		border-color: #39f;
		color: #fff;
		opacity: 1;
	}
	.pot {
		position: absolute;
		right: 0.3rem;
		top: 0.3rem;
		display: flex;
		gap: 0.2rem;
		font-size: 0.7rem;
	}
	.pot b {
		background: #000a;
		border-radius: 3px;
		padding: 0.05rem 0.25rem;
		font-weight: 600;
		color: #ccc;
	}
	.pot b.hi {
		background: #2d6a3e;
		color: #fff;
	}
	.cell :global(.card) {
		position: relative;
	}
	.st {
		position: absolute;
		left: 0.3rem;
		bottom: 2.1rem;
		font-size: 0.7rem;
		padding: 0.05rem 0.35rem;
		border-radius: 3px;
		background: #000b;
		color: #ddd;
	}
	.st-exportee {
		background: #5a4a1a;
		color: #fe9;
	}
	.st-en_vente {
		background: #1d4f7a;
		color: #fff;
	}
	.st-vendue {
		background: #2d6a3e;
		color: #fff;
	}
	.selbar {
		position: sticky;
		bottom: 0;
		display: flex;
		flex-wrap: wrap;
		gap: 0.6rem;
		align-items: center;
		padding: 0.6rem 1rem;
		margin: 0 -1rem -1rem;
		background: #1c1c1cee;
		border-top: 1px solid #333;
	}
	.selbar .go {
		margin-left: auto;
		font-weight: 600;
	}
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
	.narrow {
		max-width: 14rem;
	}
	.refine {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.2rem;
		margin: -0.4rem 0 0.8rem;
	}
	.refine .tag {
		border: none;
		cursor: pointer;
	}
	.refine .tag:hover {
		background: #2d4a63;
	}
	.pager {
		display: flex;
		gap: 1rem;
		align-items: center;
		justify-content: center;
		margin: 1.5rem 0;
	}
</style>
