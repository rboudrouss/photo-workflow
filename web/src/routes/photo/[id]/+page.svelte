<script lang="ts">
	import { page } from '$app/state';
	import { api, media, NUDITY_LEVELS, isExplicit, type PhotoDetail } from '$lib/api';

	let photo = $state<PhotoDetail | null>(null);
	let error = $state('');
	let title = $state('');
	let description = $state('');
	let saved = $state('');
	let fiche = $state('');

	const id = $derived(page.params.id ?? '');

	async function load() {
		photo = null;
		try {
			photo = await api.photo(id);
			const best = photo.captions.find((c) => c.source === 'human') ?? photo.captions[0];
			title = best?.title ?? '';
			description = best?.description ?? '';
		} catch (e: any) {
			error = e.message;
		}
	}

	async function save() {
		if (!photo) return;
		await api.saveCaption(photo.id, { title, description });
		saved = 'enregistre';
		setTimeout(() => (saved = ''), 1500);
		load();
	}

	async function setNudity(e: Event) {
		if (!photo) return;
		await api.setNudity(photo.id, (e.target as HTMLSelectElement).value);
		load();
	}

	async function copyFiche() {
		if (!photo) return;
		const f = await api.fiche(photo.id);
		fiche = `${f.title}\n\n${f.description}\n\nCategorie : ${f.category ?? ''}\nTags : ${f.tags.join(', ')}`;
		try {
			await navigator.clipboard.writeText(fiche);
		} catch {}
	}

	$effect(() => {
		id;
		load();
	});
</script>

{#if error}<p style="color:#f66">{error}</p>{/if}

{#if photo}
	<div class="layout">
		<div>
			<a href={media(photo.media.original)} target="_blank">
				<img class="main" src={media(photo.media.web)} alt={photo.filename} />
			</a>
			<p class="muted">
				{photo.filename} · {photo.width}×{photo.height} · {photo.format} · {Math.round(photo.bytes / 1024)} ko
			</p>
			<p class="muted">
				Nudite :
				<select value={photo.nudity_level ?? ''} onchange={setNudity}>
					<option value="" disabled>non analyse</option>
					{#each NUDITY_LEVELS as l}<option value={l}>{l}</option>{/each}
				</select>
				<span>source : {photo.nudity_source ?? 'aucune'}</span>
			</p>
			{#if photo.faces.length}
				<h3>Visages ({photo.faces.length})</h3>
				<div class="faces">
					{#each photo.faces as f}
						<a href={f.cluster_id != null && f.cluster_id >= 0 ? `/faces/${f.cluster_id}` : undefined} class="face">
							<img src={media(f.crop)} alt="visage" />
							<div class="muted">
								{f.person_name ?? (f.cluster_id != null && f.cluster_id >= 0 ? `groupe ${f.cluster_id}` : 'isole')}
								{#if f.age != null}· ~{f.age} ans{/if}
							</div>
						</a>
					{/each}
				</div>
			{/if}
		</div>

		<div>
			<h2>Legende</h2>
			<input bind:value={title} placeholder="Titre" style="width:100%" />
			<textarea bind:value={description} rows="7" style="width:100%; margin-top:0.5rem"></textarea>
			<div style="display:flex; gap:0.5rem; align-items:center; margin-top:0.5rem">
				<button onclick={save}>Enregistrer (humain)</button>
				<button onclick={copyFiche}>Copier la fiche Delcampe</button>
				<span class="muted">{saved}</span>
			</div>
			{#if fiche}<pre class="fiche">{fiche}</pre>{/if}

			{#each photo.captions as c}
				<details open={c.source !== 'human'}>
					<summary>{c.source} <span class="muted">{c.updated_at?.slice(0, 16)}</span></summary>
					{#if c.source !== 'human'}
						<p><b>{c.title}</b></p>
						<p>{c.description}</p>
					{/if}
					{#if c.data}
						{@const d = c.data}
						<div class="kv">
							{#if d.type_objet}<div><span>objet</span>{d.type_objet} · {d.face}{#if d.nombre_objets > 1} · lot de {d.nombre_objets}{/if}</div>{/if}
							{#if d.support}<div><span>support</span>{d.support} · {d.couleur} · {d.scene}</div>{/if}
							{#if d.piece}
								<div><span>pièce</span>{[d.piece.pays, d.piece.valeur_faciale, d.piece.type_ou_graveur, d.piece.annee, d.piece.metal, d.piece.atelier].filter(Boolean).join(' · ')}
									{#if d.piece.etat_estime}· état {d.piece.etat_estime}{/if} · {d.piece.face_visible}{#if d.piece.nombre > 1} · {d.piece.nombre} pièces{/if}</div>
								{#if d.piece.annotations?.length}<div><span>étui</span>{d.piece.annotations.join(' / ')}</div>{/if}
							{/if}
							{#if d.carte_postale}
								<div><span>carte</span>{[d.carte_postale.editeur, d.carte_postale.numero ? `n° ${d.carte_postale.numero}` : null, d.carte_postale.carte_photo ? 'carte-photo' : 'imprimée', d.carte_postale.voyagee === true ? 'voyagée' : d.carte_postale.voyagee === false ? 'non voyagée' : null, d.carte_postale.cachet_lieu, d.carte_postale.cachet_date].filter(Boolean).join(' · ')}</div>
								{#if d.carte_postale.legende_imprimee}<div><span>légende</span>{d.carte_postale.legende_imprimee}</div>{/if}
								{#if d.carte_postale.correspondance}<div><span>courrier</span>{d.carte_postale.correspondance}</div>{/if}
							{/if}
							{#if d.lot?.length}<div><span>lot</span><ul style="margin:0">{#each d.lot as o}<li>{o.type_objet} : {o.resume}</li>{/each}</ul></div>{/if}
							{#if d.epoque}<div><span>epoque</span>{d.epoque.decennie} ({d.epoque.confiance}) — {(d.epoque.indices ?? []).join('; ')}</div>{/if}
							{#if d.lieu}
								<div>
									<span>lieu</span>
									{[d.lieu.lieu_precis, d.lieu.ville, d.lieu.region, d.lieu.pays].filter(Boolean).join(', ') || 'inconnu'}
									({d.lieu.confiance}) — {(d.lieu.indices ?? []).join('; ')}
								</div>
							{/if}
							{#if d.personnes != null}<div><span>personnes</span>{d.personnes}</div>{/if}
							{#if d.objets?.length}<div><span>objets</span>{d.objets.join(', ')}</div>{/if}
							{#if d.texte_visible?.length}<div><span>texte</span>{d.texte_visible.join(' | ')}</div>{/if}
							{#if d.etat?.length}<div><span>etat</span>{d.etat.join(', ')}</div>{/if}
							{#if d.interet_vente}<div><span>interet</span>{d.interet_vente} · {d.categorie_delcampe}</div>{/if}
							{#if d.incertitudes?.length}<div><span>a verifier</span>{d.incertitudes.join('; ')}</div>{/if}
							{#if d.nudite && d.nudite.niveau !== 'aucune'}<div><span>nudite</span>{d.nudite.niveau} · {d.nudite.contexte} — {d.nudite.explication}</div>{/if}
							{#if d.tags?.length}<div><span>tags</span>{#each d.tags as t}<span class="tag">{t}</span>{/each}</div>{/if}
						</div>
					{/if}
				</details>
			{/each}

			<details>
				<summary class="muted">Jobs et extractions brutes</summary>
				<ul>
					{#each photo.jobs as j}<li>{j.extractor}: {j.status} {j.error ? `— ${j.error}` : ''}</li>{/each}
				</ul>
				<pre style="font-size:0.75rem; white-space:pre-wrap">{JSON.stringify(photo.extractions, null, 1)}</pre>
			</details>
		</div>
	</div>

	{#if photo.series}
		<h3>
			<a href={`/series/${photo.series.id}`}>{photo.series.name ?? `Série ${photo.series.id}`}</a>
			<span class="muted">{photo.series.size} photos, même pellicule ou même séance</span>
		</h3>
		<div class="grid">
			{#each photo.series.members as p}
				<a class="card" href={`/photo/${p.id}`}>
					<img src={media(p.media.thumb)} alt="" class:blur={isExplicit(p.nudity_level)} />
					<div class="t">{p.title ?? p.filename} <span class="muted">{p.decade ?? ''}</span></div>
				</a>
			{/each}
		</div>
	{/if}

	{#if photo.duplicates.length}
		<h3>Doublons probables (pHash)</h3>
		<div class="grid">
			{#each photo.duplicates as p}
				<a class="card" href={`/photo/${p.id}`}>
					<img src={media(p.media.thumb)} alt="" />
					<div class="t">{p.title ?? p.filename} <span class="muted">d={p.distance}</span></div>
				</a>
			{/each}
		</div>
	{/if}

	{#if photo.similar.length}
		<h3>Photos proches (embeddings)</h3>
		<div class="grid">
			{#each photo.similar as p}
				<a class="card" href={`/photo/${p.id}`}>
					<img src={media(p.media.thumb)} alt="" />
					<div class="t">{p.title ?? p.filename} <span class="muted">{p.score?.toFixed(2)}</span></div>
				</a>
			{/each}
		</div>
	{/if}
{:else if !error}
	<p class="muted">chargement…</p>
{/if}

<style>
	.blur {
		filter: blur(14px);
	}
	.layout {
		display: grid;
		grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr);
		gap: 1.5rem;
	}
	@media (max-width: 900px) {
		.layout {
			grid-template-columns: 1fr;
		}
	}
	.main {
		width: 100%;
		max-height: 80vh;
		object-fit: contain;
		background: #000;
	}
	.faces {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
	}
	.face {
		width: 96px;
		text-align: center;
		font-size: 0.75rem;
		text-decoration: none;
	}
	.face img {
		width: 96px;
		height: 96px;
		object-fit: cover;
		border-radius: 4px;
	}
	.kv div {
		margin: 0.25rem 0;
		font-size: 0.9rem;
	}
	.kv span {
		display: inline-block;
		min-width: 6rem;
		color: #999;
	}
	.fiche {
		white-space: pre-wrap;
		background: #1c1c1c;
		padding: 0.6rem;
		font-size: 0.85rem;
	}
	details {
		margin-top: 1rem;
		border-top: 1px solid #333;
		padding-top: 0.5rem;
	}
</style>
