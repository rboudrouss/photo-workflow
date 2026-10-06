<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';

	let stats = $state<any>(null);
	let error = $state('');

	async function load() {
		try {
			stats = await api.stats();
		} catch (e: any) {
			error = e.message;
		}
	}
	onMount(() => {
		load();
		const t = setInterval(load, 5000);
		return () => clearInterval(t);
	});
</script>

<h2>Statut</h2>
{#if error}<p style="color:#f66">{error}</p>{/if}
{#if stats}
	<ul>
		<li>{stats.photos} photos, dont {stats.photos_with_caption} avec legende</li>
		<li>{stats.faces} visages, {stats.face_clusters} groupes, {stats.persons} personnes nommees</li>
		<li class="muted">embeddings : {stats.config.embedding_model} · VLM : {stats.config.vlm_backend}</li>
	</ul>
	<h3>File de jobs</h3>
	<table>
		<thead><tr><th>extracteur</th><th>statut</th><th>nombre</th></tr></thead>
		<tbody>
			{#each stats.jobs as j}
				<tr><td>{j.extractor}</td><td>{j.status}</td><td>{j.count}</td></tr>
			{/each}
		</tbody>
	</table>
{/if}

<style>
	table {
		border-collapse: collapse;
	}
	td,
	th {
		padding: 0.3rem 0.8rem;
		border-bottom: 1px solid #333;
		text-align: left;
	}
</style>
