<script lang="ts">
	import { upload, type UploadItem } from '$lib/api';

	const BATCH = 8; // fichiers par requete : limite la taille de chaque envoi, et affiche l'avancement au fil de l'eau

	let queue = $state<File[]>([]);
	let results = $state<UploadItem[]>([]);
	let sending = $state(false);
	let done = $state(0);
	let error = $state('');
	let dragging = $state(false);

	function pick(e: Event) {
		add(Array.from((e.target as HTMLInputElement).files ?? []));
		(e.target as HTMLInputElement).value = '';
	}
	function drop(e: DragEvent) {
		e.preventDefault();
		dragging = false;
		add(Array.from(e.dataTransfer?.files ?? []));
	}
	function add(files: File[]) {
		const known = new Set(queue.map((f) => f.name + f.size));
		queue = [...queue, ...files.filter((f) => !known.has(f.name + f.size))];
	}

	async function send() {
		if (!queue.length || sending) return;
		sending = true;
		error = '';
		results = [];
		done = 0;
		const files = queue;
		// Un lot qui echoue (serveur qui redemarre, coupure reseau) est retente 3 fois avec attente croissante,
		// puis ses fichiers sont marques en erreur et on passe au lot suivant : un envoi de 1000 photos ne
		// s'arrete pas sur un incident. Les fichiers en erreur restent dans la file pour un second essai.
		const failed: File[] = [];
		try {
			for (let i = 0; i < files.length; i += BATCH) {
				const batch = files.slice(i, i + BATCH);
				let items: UploadItem[] | null = null;
				let lastError = '';
				for (let attempt = 0; attempt < 4 && !items; attempt++) {
					if (attempt) await new Promise((r) => setTimeout(r, 3000 * attempt));
					try {
						items = await upload(batch);
					} catch (e: any) {
						lastError = e.message;
					}
				}
				if (!items) {
					failed.push(...batch);
					items = batch.map((f) => ({ filename: f.name, status: 'error' as const, error: lastError }));
				}
				results = [...results, ...items];
				done = Math.min(i + BATCH, files.length);
			}
		} finally {
			queue = failed;
			if (failed.length) error = `${failed.length} fichier(s) non envoyes apres plusieurs essais : ils restent dans la liste, clique a nouveau sur Envoyer.`;
			sending = false;
		}
	}

	let counts = $derived({
		new: results.filter((r) => r.status === 'new').length,
		duplicate: results.filter((r) => r.status === 'duplicate').length,
		error: results.filter((r) => r.status === 'error').length
	});
	let totalBytes = $derived(queue.reduce((a, f) => a + f.size, 0));
</script>

<h2>Ajouter des photos</h2>
<p class="muted">
	Le nom du fichier est conserve tel quel : c'est lui qui sert a retrouver la photo (recherche, fiche, export). Un
	fichier deja present (meme contenu) est signale et n'est pas ajoute deux fois. Les photos ajoutees partent
	automatiquement dans la file d'analyse.
</p>

<div
	class="drop"
	class:dragging
	role="button"
	tabindex="0"
	ondragover={(e) => { e.preventDefault(); dragging = true; }}
	ondragleave={() => (dragging = false)}
	ondrop={drop}
>
	<p>Glisser des fichiers ici, ou</p>
	<label class="btn">choisir des fichiers <input type="file" multiple accept="image/*,.tif,.tiff" onchange={pick} hidden /></label>
</div>

{#if queue.length}
	<div class="actions">
		<button onclick={send} disabled={sending}>
			{sending ? `Envoi ${done} / ${queue.length}...` : `Envoyer ${queue.length} fichier(s) (${(totalBytes / 1048576).toFixed(0)} Mo)`}
		</button>
		{#if !sending}<button onclick={() => (queue = [])}>Vider</button>{/if}
	</div>
	{#if !sending}
		<ul class="files">
			{#each queue.slice(0, 50) as f}<li>{f.name} <span class="muted">{(f.size / 1024).toFixed(0)} ko</span></li>{/each}
			{#if queue.length > 50}<li class="muted">et {queue.length - 50} autres</li>{/if}
		</ul>
	{/if}
{/if}

{#if error}<p style="color:#f66">{error}</p>{/if}

{#if results.length}
	<h3>Resultat : {counts.new} ajoutee(s), {counts.duplicate} deja presente(s), {counts.error} erreur(s)</h3>
	<ul class="files">
		{#each results as r}
			<li>
				{#if r.status === 'new'}
					<a href={`/photo/${r.id}`}>{r.filename}</a> <span class="ok">ajoutee</span>
				{:else if r.status === 'duplicate'}
					{r.filename} <span class="muted">deja presente :</span> <a href={`/photo/${r.existing_id}`}>voir</a>
				{:else}
					{r.filename} <span class="err">{r.error}</span>
				{/if}
			</li>
		{/each}
	</ul>
{/if}

<style>
	.drop { border: 2px dashed #444; border-radius: 8px; padding: 2rem; text-align: center; max-width: 700px; }
	.drop.dragging { border-color: #9cf; background: #1a2230; }
	.btn { display: inline-block; background: #222; border: 1px solid #444; border-radius: 4px; padding: 0.4rem 0.8rem; cursor: pointer; }
	.actions { display: flex; gap: 0.6rem; margin: 0.8rem 0; }
	.files { list-style: none; padding: 0; max-width: 700px; }
	.files li { padding: 0.2rem 0; border-bottom: 1px solid #222; font-size: 0.9rem; }
	.ok { color: #6c6; }
	.err { color: #f66; }
</style>
