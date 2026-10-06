<script lang="ts">
	import { blur } from '$lib/blur.svelte';
	import { page } from '$app/state';
	import { api, media, isExplicit, type SeriesDetail } from '$lib/api';

	let data = $state<SeriesDetail | null>(null);
	let error = $state('');
	let name = $state('');
	let msg = $state('');
	let decade = $state('');
	let ville = $state('');
	let region = $state('');
	let pays = $state('');
	let lieuPrecis = $state('');
	let onlyUnknown = $state(true);

	const sid = $derived(Number(page.params.id ?? -1));

	async function load() {
		try {
			data = await api.serie(sid);
			name = data.name ?? '';
			decade = data.summary.decades[0]?.value ?? '';
		} catch (e: any) {
			error = e.message;
		}
	}

	async function saveName(e: Event) {
		e.preventDefault();
		await api.renameSeries(sid, name);
		msg = 'nom enregistré';
		setTimeout(() => (msg = ''), 1500);
	}

	async function propagate(e: Event) {
		e.preventDefault();
		const patch: Record<string, any> = {};
		if (decade.trim()) patch.epoque = { decennie: decade.trim(), confiance: 'forte' };
		const lieu: Record<string, string> = {};
		if (ville.trim()) lieu.ville = ville.trim();
		if (region.trim()) lieu.region = region.trim();
		if (pays.trim()) lieu.pays = pays.trim();
		if (lieuPrecis.trim()) lieu.lieu_precis = lieuPrecis.trim();
		if (Object.keys(lieu).length) patch.lieu = { ...lieu, confiance: 'forte' };
		if (!Object.keys(patch).length) return;
		const r = await api.propagate(sid, patch, onlyUnknown);
		msg = `${r.updated} photo(s) mises à jour sur ${r.photos}`;
		load();
	}

	$effect(() => {
		sid;
		load();
	});
</script>

<p><a href="/series">← séries</a></p>
{#if error}<p style="color:#f66">{error}</p>{/if}

{#if data}
	<h2>{data.name ?? `Série ${data.id}`} <span class="muted">{data.size} photos</span></h2>

	<form onsubmit={saveName} class="row">
		<input bind:value={name} placeholder="Nom de la série (ex. Vacances à Dinard, été 1934)" size="50" />
		<button type="submit">Enregistrer</button>
		<span class="muted">{msg}</span>
	</form>

	<div class="summary">
		<div>
			<b>Époques</b>
			{#each data.summary.decades as d}<span class="tag">{d.value} ({d.count})</span>{/each}
			{#if !data.summary.decades.length}<span class="muted">aucune</span>{/if}
		</div>
		<div>
			<b>Scènes</b>
			{#each data.summary.scenes as s}<span class="tag">{s.value} ({s.count})</span>{/each}
		</div>
		<div>
			<b>Personnes</b>
			{#each data.summary.persons as p}<span class="tag">{p.name} ({p.photos})</span>{/each}
			{#each data.summary.unnamed_clusters as c}<a class="tag" href={`/faces/${c.cluster_id}`}>groupe {c.cluster_id} ({c.photos})</a>{/each}
		</div>
		{#if data.summary.nudity_max && data.summary.nudity_max !== 'aucune'}
			<div><b>Nudité</b> <span class="tag">{data.summary.nudity_max}</span></div>
		{/if}
	</div>

	<details class="prop">
		<summary>Propager une date ou un lieu à toute la série</summary>
		<form onsubmit={propagate} class="row wrap">
			<input bind:value={decade} placeholder="Décennie (1930s)" size="12" />
			<input bind:value={lieuPrecis} placeholder="Lieu précis" />
			<input bind:value={ville} placeholder="Ville" />
			<input bind:value={region} placeholder="Région" />
			<input bind:value={pays} placeholder="Pays" />
			<label class="muted"><input type="checkbox" bind:checked={onlyUnknown} /> seulement si inconnu</label>
			<button type="submit">Appliquer à {data.size} photos</button>
		</form>
		<p class="muted">
			Écrit dans la légende humaine de chaque photo. Avec « seulement si inconnu », une décennie ou un lieu déjà
			saisis à la main ne sont pas écrasés.
		</p>
	</details>

	<div class="grid">
		{#each data.photos as p (p.id)}
			<a class="card" href={`/photo/${p.id}`}>
				<img src={media(p.media.thumb)} alt="" class:blur={blur.on && isExplicit(p.nudity_level)} />
				<div class="t">{p.title ?? p.filename} <span class="muted">{p.decade ?? ''}</span></div>
			</a>
		{/each}
	</div>

	<details>
		<summary class="muted">Pourquoi ces photos sont ensemble ({data.edges.length} liens)</summary>
		<table>
			<thead><tr><th>score</th><th>similarité</th><th>visages</th><th>physique</th><th>pHash</th></tr></thead>
			<tbody>
				{#each data.edges.slice(0, 200) as e}
					<tr>
						<td>{e.score.toFixed(3)}</td>
						<td>{e.reasons.sim}</td>
						<td>{e.reasons.faces ? 'oui' : ''}</td>
						<td>{e.reasons.physical}</td>
						<td>{e.reasons.phash ?? ''}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</details>
{/if}

<style>
	.row {
		display: flex;
		gap: 0.5rem;
		align-items: center;
		margin: 0.5rem 0 1rem;
	}
	.wrap {
		flex-wrap: wrap;
	}
	.summary {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		margin-bottom: 1rem;
	}
	.summary b {
		display: inline-block;
		min-width: 6rem;
		color: #999;
		font-weight: normal;
	}
	.prop {
		background: #1c1c1c;
		padding: 0.6rem;
		border-radius: 6px;
		margin-bottom: 1rem;
	}
	.blur {
		filter: blur(14px);
	}
	table {
		border-collapse: collapse;
		font-size: 0.8rem;
	}
	td,
	th {
		padding: 0.2rem 0.6rem;
		border-bottom: 1px solid #333;
		text-align: left;
	}
</style>
