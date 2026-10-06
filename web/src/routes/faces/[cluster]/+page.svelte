<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { api, media } from '$lib/api';

	let data = $state<{ cluster_id: number; faces: any[] } | null>(null);
	let name = $state('');
	let msg = $state('');

	const cid = $derived(Number(page.params.cluster ?? -1));

	async function load() {
		data = await api.cluster(cid);
		name = data.faces.find((f) => f.person_name)?.person_name ?? '';
	}

	async function rename(e: Event) {
		e.preventDefault();
		if (!name.trim()) return;
		const r = await api.nameCluster(cid, name.trim());
		msg = `${r.faces_updated} visage(s) attribues a ${r.name}`;
		load();
	}

	onMount(load);
</script>

<p><a href="/faces">← groupes</a></p>
{#if data}
	<h2>Groupe {data.cluster_id}</h2>
	<form onsubmit={rename} style="display:flex; gap:0.5rem; align-items:center; margin-bottom:1rem">
		<input bind:value={name} placeholder="Nom de la personne" />
		<button type="submit">Nommer ce groupe</button>
		<span class="muted">{msg}</span>
	</form>
	<div class="grid">
		{#each data.faces as f}
			<a class="card" href={`/photo/${f.photo.id}`}>
				<img src={media(f.crop)} alt="" style="aspect-ratio:1" />
				<div class="t">
					{f.photo.title ?? f.photo.filename}
					<span class="muted">{(f.det_score * 100).toFixed(0)}%{f.age != null ? ` · ~${f.age}` : ''}</span>
				</div>
			</a>
		{/each}
	</div>
{/if}
