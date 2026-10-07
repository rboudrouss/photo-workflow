<script lang="ts">
	// Export Delcampe : la selection faite sur la page Photos devient un fichier Easy Uploader (Excel ou CSV).
	// Les images partent en URL publiques /pub/<jeton>.jpg, que Delcampe telecharge a l'import.
	import { onMount } from 'svelte';
	import { browser } from '$app/environment';
	import {
		api,
		media,
		publicBase,
		exportDelcampe,
		isExplicit,
		type DelcampeCategory,
		type ExportOptions,
		type ExportRow
	} from '$lib/api';
	import { blur } from '$lib/blur.svelte';
	import { selection, setSelected, clearSelection } from '$lib/selection.svelte';

	const OPTS_KEY = 'photoflow.delcampe.options';
	const OVERRIDES_KEY = 'photoflow.delcampe.overrides';
	const DEFAULTS: ExportOptions = {
		selling_type: 'fixed_price',
		price: 5,
		minimum_bid_step: 0.5,
		initial_quantity: 1,
		renew_duration: 14,
		renew_total_count: 99,
		sale_end_time: null,
		sale_end_day: null,
		shipping_model: null,
		weight: null
	};
	const DAYS = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche'];

	function stored<T>(key: string, fallback: T): T {
		if (!browser) return fallback;
		try {
			return { ...fallback, ...JSON.parse(localStorage.getItem(key) ?? '{}') };
		} catch {
			return fallback;
		}
	}

	let opts = $state<ExportOptions>(stored(OPTS_KEY, DEFAULTS));
	let overrides = $state<Record<string, { price?: number | null; category_id?: number | null }>>(stored(OVERRIDES_KEY, {}));
	let format = $state<'xlsx' | 'csv'>('xlsx');
	$effect(() => localStorage.setItem(OPTS_KEY, JSON.stringify(opts)));
	$effect(() => localStorage.setItem(OVERRIDES_KEY, JSON.stringify(overrides)));

	let rows = $state<ExportRow[]>([]);
	let categories = $state<DelcampeCategory[]>([]);
	let loading = $state(true);
	let busy = $state(false);
	let error = $state('');
	let done = $state('');
	let links = $state<number | null>(null);
	let order = $state<'selection' | 'vente' | 'instagram'>('selection');
	let onlyIssues = $state(false);

	const label = (id: number | null | undefined) => categories.find((c) => c.id === id)?.label ?? (id ? `n° ${id}` : '');
	const categoryOf = (r: ExportRow) => overrides[r.id]?.category_id ?? r.category_id;

	let missing = $derived(rows.filter((r) => !categoryOf(r)));
	let withWarnings = $derived(rows.filter((r) => r.warnings.length || !categoryOf(r)));
	let shown = $derived.by(() => {
		let out = onlyIssues ? withWarnings : rows;
		const key = order;
		if (key !== 'selection') out = [...out].sort((a, b) => (b.potentiel?.[key] ?? -1) - (a.potentiel?.[key] ?? -1));
		return out;
	});

	async function load() {
		loading = true;
		error = '';
		try {
			const ids = [...selection];
			const [c, p, l] = await Promise.all([
				categories.length ? Promise.resolve(categories) : api.delcampeCategories(),
				ids.length ? api.exportPreview(ids, publicBase()) : Promise.resolve({ items: [] }),
				api.publicLinks()
			]);
			categories = c;
			rows = p.items;
			links = l.count;
		} catch (e: any) {
			error = e.message;
		} finally {
			loading = false;
		}
	}

	function setOverride(id: string, patch: { price?: number | null; category_id?: number | null }) {
		const o = { ...overrides[id], ...patch };
		if (o.price == null) delete o.price;
		if (o.category_id == null) delete o.category_id;
		const next = { ...overrides };
		if (Object.keys(o).length) next[id] = o;
		else delete next[id];
		overrides = next;
	}

	function chooseCategory(r: ExportRow, value: string) {
		if (value === 'autre') {
			const n = Number(prompt('Numero de categorie Delcampe (liste sur delcampe.net/en_GB/collectables/category-id)', ''));
			if (Number.isInteger(n) && n > 0) setOverride(r.id, { category_id: n });
			return;
		}
		const n = Number(value);
		setOverride(r.id, { category_id: n === r.category_id ? null : n });
	}

	function remove(id: string) {
		setSelected([id], false);
		rows = rows.filter((r) => r.id !== id);
	}

	async function download() {
		busy = true;
		error = '';
		done = '';
		try {
			const ids = rows.map((r) => r.id);
			const relevant = Object.fromEntries(Object.entries(overrides).filter(([id]) => ids.includes(id)));
			const blob = await exportDelcampe({ ids, base_url: publicBase(), format, options: $state.snapshot(opts), overrides: relevant });
			const a = document.createElement('a');
			a.href = URL.createObjectURL(blob);
			a.download = `delcampe-${new Date().toISOString().slice(0, 10)}-${ids.length}.${format}`;
			a.click();
			setTimeout(() => URL.revokeObjectURL(a.href), 10_000);
			done = `${ids.length} fiche(s) exportee(s).`;
			await load();
		} catch (e: any) {
			error = e.message;
		} finally {
			busy = false;
		}
	}

	async function revoke(all: boolean) {
		const what = all ? `tous les liens publics (${links})` : `les liens publics des ${rows.length} photos de la selection`;
		if (!confirm(`Desactiver ${what} ? Les images deja importees sur Delcampe ne sont pas touchees.`)) return;
		try {
			const r = await api.revokePublicLinks(all ? null : rows.map((x) => x.id));
			done = `${r.revoked} lien(s) desactive(s).`;
			await load();
		} catch (e: any) {
			error = e.message;
		}
	}

	onMount(load);
</script>

<h1>Export Delcampe</h1>

{#if !selection.size && !loading}
	<p>Aucune photo selectionnee. Sur la page <a href="/">Photos</a>, cocher des photos (Maj+clic pour une plage), ou
		« Cocher les N resultats » apres avoir filtre ou trie par potentiel de vente.</p>
{:else}
	<section class="opts">
		<label>Vente
			<select bind:value={opts.selling_type}>
				<option value="fixed_price">prix fixe</option>
				<option value="bid">enchere</option>
			</select>
		</label>
		<label>{opts.selling_type === 'bid' ? 'Mise de depart' : 'Prix'} par defaut (€)
			<input type="number" min="0.01" step="0.01" bind:value={opts.price} />
		</label>
		{#if opts.selling_type === 'bid'}
			<label>Pas d'enchere (€)
				<input type="number" min="0.01" step="0.01" bind:value={opts.minimum_bid_step} />
			</label>
		{:else}
			<label>Quantite
				<input type="number" min="1" max="100" bind:value={opts.initial_quantity} />
			</label>
		{/if}
		<label>Duree
			<select bind:value={opts.renew_duration}>
				{#each [7, 10, 14, 21, 28] as d}<option value={d}>{d} jours</option>{/each}
			</select>
		</label>
		<label>Remises en vente
			<select bind:value={opts.renew_total_count}>
				{#each [0, 1, 2, 3, 4, 5, 10] as n}<option value={n}>{n}</option>{/each}
				<option value={99}>jusqu'a la vente</option>
			</select>
		</label>
		<label>Fin a
			<input type="time" value={opts.sale_end_time ?? ''} onchange={(e) => (opts.sale_end_time = e.currentTarget.value || null)} />
		</label>
		<label>Jour de fin
			<select value={opts.sale_end_day ?? ''} onchange={(e) => (opts.sale_end_day = e.currentTarget.value ? Number(e.currentTarget.value) : null)}>
				<option value="">7 jours apres</option>
				{#each DAYS as d, i}<option value={i + 1}>{d}</option>{/each}
			</select>
		</label>
		<label title="Nom exact d'un modele de frais de port cree sur Delcampe, ou Free">Modele de frais de port
			<input value={opts.shipping_model ?? ''} placeholder="anciens frais" oninput={(e) => (opts.shipping_model = e.currentTarget.value || null)} />
		</label>
		<label title="Seulement avec un modele de frais au poids">Poids (g)
			<input type="number" min="0" value={opts.weight ?? ''} oninput={(e) => (opts.weight = e.currentTarget.value ? Number(e.currentTarget.value) : null)} />
		</label>
		<label>Fichier
			<select bind:value={format}>
				<option value="xlsx">Excel (.xlsx)</option>
				<option value="csv">CSV</option>
			</select>
		</label>
	</section>

	<div class="actions">
		<button class="primary" onclick={download} disabled={busy || loading || !rows.length || missing.length > 0}>
			Telecharger le fichier ({rows.length} fiche{rows.length > 1 ? 's' : ''}){busy ? ' …' : ''}
		</button>
		{#if missing.length}<span class="warn">{missing.length} photo(s) sans categorie : la choisir dans le tableau.</span>{/if}
		{#if done}<span class="ok">{done}</span>{/if}
		{#if error}<span class="warn">{error}</span>{/if}
	</div>
	<p class="muted">
		Les images sont envoyees en liens publics impossibles a deviner, crees a l'export et sans mot de passe :
		{links ?? '…'} lien(s) actif(s).
		<button class="link" onclick={() => revoke(false)} disabled={!rows.length}>desactiver ceux de la selection</button>
		· <button class="link" onclick={() => revoke(true)} disabled={!links}>tous les desactiver</button>.
		Delcampe garde sa copie des images : on peut desactiver une fois l'import confirme par e-mail.
	</p>

	<div class="tools">
		<label>Ordre
			<select bind:value={order}>
				<option value="selection">ordre de selection</option>
				<option value="vente">potentiel de vente</option>
				<option value="instagram">potentiel Instagram</option>
			</select>
		</label>
		<label><input type="checkbox" bind:checked={onlyIssues} /> seulement les {withWarnings.length} a verifier</label>
		<button onclick={() => confirm(`Vider la selection (${selection.size}) ?`) && (clearSelection(), (rows = []))}>Vider la selection</button>
	</div>

	{#if loading}
		<p class="muted">chargement…</p>
	{:else}
		<table>
			<thead>
				<tr><th></th><th>Fiche</th><th>Categorie</th><th>Prix</th><th>Potentiel</th><th></th></tr>
			</thead>
			<tbody>
				{#each shown as r (r.id)}
					{@const cat = categoryOf(r)}
					<tr class:bad={!cat}>
						<td><a href={`/photo/${r.id}`}><img src={media(r.media.thumb)} alt="" loading="lazy" class:blur={blur.on && isExplicit(r.nudity_level)} /></a></td>
						<td class="fiche">
							<a href={`/photo/${r.id}`}>{r.title}</a>
							<div class="muted">{r.reference} · {r.filename}{#if r.public_url} · <a href={r.public_url} target="_blank" rel="noreferrer">lien public</a>{/if}</div>
							{#each r.warnings as w}<span class="w">{w}</span>{/each}
							<details><summary class="muted">description</summary><p>{r.description}</p></details>
						</td>
						<td>
							<select value={cat ?? ''} onchange={(e) => chooseCategory(r, e.currentTarget.value)} class:auto={!overrides[r.id]?.category_id}>
								{#if !cat}<option value="" disabled>— choisir —</option>{/if}
								{#if cat && !categories.some((c) => c.id === cat)}<option value={cat}>n° {cat}</option>{/if}
								{#each categories as c}<option value={c.id}>{c.label}</option>{/each}
								<option value="autre">autre numero…</option>
							</select>
							<div class="muted">{cat ? `n° ${cat}` : ''}{!overrides[r.id]?.category_id && r.category_id ? ` · ${r.category_source}` : ''}</div>
						</td>
						<td>
							<input
								type="number"
								min="0.01"
								step="0.01"
								class="price"
								placeholder={String(opts.price)}
								value={overrides[r.id]?.price ?? ''}
								onchange={(e) => setOverride(r.id, { price: e.currentTarget.value ? Number(e.currentTarget.value) : null })}
							/>
						</td>
						<td class="pot">
							{#if r.potentiel}
								<div title={r.potentiel.vente_raison}>vente <b>{r.potentiel.vente}</b>/10</div>
								<div title={r.potentiel.instagram_raison}>insta <b>{r.potentiel.instagram}</b>/10</div>
							{:else}<span class="muted">—</span>{/if}
						</td>
						<td><button class="link" title="Retirer de la selection" onclick={() => remove(r.id)}>×</button></td>
					</tr>
				{/each}
			</tbody>
		</table>
	{/if}
{/if}

<style>
	h1 {
		margin-top: 0;
	}
	.opts {
		display: flex;
		flex-wrap: wrap;
		gap: 0.8rem;
		align-items: end;
	}
	.opts label,
	.tools label {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		font-size: 0.8rem;
		color: #aaa;
	}
	.opts input[type='number'] {
		width: 6rem;
	}
	.actions {
		display: flex;
		gap: 1rem;
		align-items: center;
		margin: 1rem 0 0.3rem;
	}
	.primary {
		background: #2d5f8a;
		border-color: #39f;
		font-weight: 600;
	}
	.primary:disabled {
		opacity: 0.5;
	}
	.warn {
		color: #f96;
	}
	.ok {
		color: #7d7;
	}
	.link {
		background: none;
		border: none;
		padding: 0;
		color: #9cf;
		text-decoration: underline;
	}
	.link:disabled {
		color: #666;
	}
	.tools {
		display: flex;
		gap: 1rem;
		align-items: end;
		margin: 1rem 0 0.5rem;
	}
	.tools label:has(input[type='checkbox']) {
		flex-direction: row;
		align-items: center;
	}
	table {
		width: 100%;
		border-collapse: collapse;
	}
	th {
		text-align: left;
		font-weight: 500;
		color: #999;
		font-size: 0.8rem;
	}
	td {
		border-top: 1px solid #2a2a2a;
		padding: 0.4rem;
		vertical-align: top;
	}
	tr.bad td {
		background: #3a1d1d;
	}
	td img {
		width: 80px;
		height: 60px;
		object-fit: cover;
		background: #000;
		display: block;
	}
	.blur {
		filter: blur(10px);
	}
	.fiche a {
		color: #eee;
		text-decoration: none;
	}
	.fiche p {
		font-size: 0.85rem;
		white-space: pre-wrap;
		margin: 0.3rem 0;
	}
	.w {
		display: inline-block;
		background: #4a3a1a;
		color: #fc9;
		border-radius: 3px;
		padding: 0 0.35rem;
		margin: 0.2rem 0.25rem 0 0;
		font-size: 0.75rem;
	}
	td select {
		max-width: 17rem;
	}
	select.auto {
		color: #bbb;
	}
	.price {
		width: 5.5rem;
	}
	.pot {
		font-size: 0.85rem;
		white-space: nowrap;
	}
	details {
		margin-top: 0.2rem;
	}
</style>
