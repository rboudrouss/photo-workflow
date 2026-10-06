<script lang="ts">
	import { onMount } from 'svelte';
	import { api, media, type Cluster } from '$lib/api';

	let clusters = $state<Cluster[]>([]);
	let error = $state('');

	onMount(async () => {
		try {
			clusters = await api.clusters();
		} catch (e: any) {
			error = e.message;
		}
	});
</script>

<h2>Groupes de visages</h2>
<p class="muted">Un groupe = probablement la meme personne. Lance <code>photoflow faces cluster</code> apres l'extraction pour les recalculer.</p>
{#if error}<p style="color:#f66">{error}</p>{/if}

<div class="clusters">
	{#each clusters as c}
		<a class="cluster" href={`/faces/${c.cluster_id}`}>
			<div class="samples">
				{#each c.samples as s}<img src={media(s)} alt="" />{/each}
			</div>
			<div class="t">
				<b>{c.person_name ?? `groupe ${c.cluster_id}`}</b>
				<span class="muted">{c.faces} visages · {c.photos} photos</span>
			</div>
		</a>
	{/each}
</div>

<style>
	.clusters {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
		gap: 0.8rem;
	}
	.cluster {
		background: #1c1c1c;
		border-radius: 6px;
		padding: 0.5rem;
		text-decoration: none;
		color: inherit;
	}
	.samples {
		display: flex;
		gap: 0.25rem;
	}
	.samples img {
		width: 44px;
		height: 44px;
		object-fit: cover;
		border-radius: 3px;
	}
	.t {
		display: flex;
		justify-content: space-between;
		margin-top: 0.4rem;
		font-size: 0.85rem;
	}
</style>
