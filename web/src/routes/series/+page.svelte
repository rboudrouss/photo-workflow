<script lang="ts">
	import { onMount } from 'svelte';
	import { api, media, isExplicit, type SeriesSummary } from '$lib/api';

	let items = $state<SeriesSummary[]>([]);
	let total = $state<number | null>(null);
	let page = $state(1);
	let error = $state('');

	async function load() {
		try {
			const r = await api.series(page);
			items = r.items;
			total = r.total;
		} catch (e: any) {
			error = e.message;
		}
	}
	onMount(load);
</script>

<h2>Séries <span class="muted">{total ?? ''}</span></h2>
<p class="muted">
	Photos de la même pellicule ou de la même séance, regroupées par embeddings, visages communs, format du tirage et
	doublons. Recalcul : <code>photoflow series build</code>.
</p>
{#if error}<p style="color:#f66">{error}</p>{/if}

<div class="list">
	{#each items as s (s.id)}
		<a class="serie" href={`/series/${s.id}`}>
			<div class="samples">
				{#each s.samples as p}<img src={media(p.media.thumb)} alt="" class:blur={isExplicit(p.nudity_level)} />{/each}
			</div>
			<div class="t">
				<b>{s.name ?? `Série ${s.id}`}</b>
				<span class="muted">{s.size} photos{s.decade ? ` · ${s.decade}` : ''}{s.scene ? ` · ${s.scene}` : ''}</span>
				{#if s.persons.length}<span class="muted">{s.persons.join(', ')}</span>{/if}
			</div>
		</a>
	{/each}
</div>

<div class="pager">
	<button disabled={page <= 1} onclick={() => { page--; load(); }}>Précédent</button>
	<span>page {page}</span>
	<button disabled={items.length < 40} onclick={() => { page++; load(); }}>Suivant</button>
</div>

<style>
	.list {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(420px, 1fr));
		gap: 0.8rem;
	}
	.serie {
		background: #1c1c1c;
		border-radius: 6px;
		padding: 0.5rem;
		text-decoration: none;
		color: inherit;
	}
	.samples {
		display: flex;
		gap: 0.25rem;
		overflow: hidden;
	}
	.samples img {
		width: 64px;
		height: 64px;
		object-fit: cover;
		border-radius: 3px;
	}
	.blur {
		filter: blur(8px);
	}
	.t {
		display: flex;
		gap: 0.8rem;
		align-items: baseline;
		margin-top: 0.4rem;
		font-size: 0.85rem;
		flex-wrap: wrap;
	}
	.pager {
		display: flex;
		gap: 1rem;
		align-items: center;
		justify-content: center;
		margin: 1.5rem 0;
	}
</style>
