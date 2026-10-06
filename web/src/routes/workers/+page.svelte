<script lang="ts">
	import { onMount } from 'svelte';
	import { api, type PendingWorker, type WorkerInfo } from '$lib/api';

	let workers = $state<WorkerInfo[]>([]);
	let pendingList = $state<PendingWorker[]>([]);
	let names = $state<Record<string, string>>({});
	let error = $state('');
	let busy = $state('');
	// Choix par worker : nombre de photos et extracteurs coches (par defaut tous ceux qu'il declare).
	let n = $state<Record<string, number>>({});
	let picked = $state<Record<string, Record<string, boolean>>>({});

	async function load() {
		try {
			const r = await api.workers();
			workers = r.workers;
			pendingList = r.pending;
			for (const p of pendingList) names[p.code] ??= p.hostname ?? '';
			for (const w of workers) {
				n[w.id] ??= 20;
				picked[w.id] ??= {};
				for (const ex of w.extractors) picked[w.id][ex] ??= true;
			}
			error = '';
		} catch (e: any) {
			error = e.message;
		}
	}

	async function launch(w: WorkerInfo) {
		const exs = w.extractors.filter((ex) => picked[w.id]?.[ex]);
		if (!exs.length) return;
		busy = w.id;
		try {
			const r = await api.assign(w.id, n[w.id], exs);
			busy = Object.entries(r.assigned).map(([k, v]) => `${k} : ${v}`).join(', ') || 'rien a faire';
			await load();
		} catch (e: any) {
			error = e.message;
			busy = '';
		}
	}

	async function approve(p: PendingWorker) {
		try {
			await api.approveWorker(p.code, names[p.code] || p.hostname || 'worker');
			await load();
		} catch (e: any) {
			error = e.message;
		}
	}
	async function reject(p: PendingWorker) {
		try {
			await api.rejectWorker(p.code);
			await load();
		} catch (e: any) {
			error = e.message;
		}
	}
	async function forget(w: WorkerInfo) {
		if (!confirm(`Oublier ${w.name} ? Il devra etre approuve a nouveau.`)) return;
		try {
			await api.revokeWorker(w.id);
			await load();
		} catch (e: any) {
			error = e.message;
		}
	}

	async function giveBack(w: WorkerInfo) {
		try {
			await api.unassign(w.id);
			await load();
		} catch (e: any) {
			error = e.message;
		}
	}

	function pending(w: WorkerInfo) {
		return Object.values(w.jobs).reduce((a, s) => a + (s.pending ?? 0), 0);
	}
	function running(w: WorkerInfo) {
		return Object.values(w.jobs).reduce((a, s) => a + (s.running ?? 0), 0);
	}
	function ago(iso: string | null) {
		if (!iso) return 'jamais';
		const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
		return s < 90 ? `il y a ${s} s` : s < 5400 ? `il y a ${Math.round(s / 60)} min` : `il y a ${Math.round(s / 3600)} h`;
	}

	onMount(() => {
		load();
		const t = setInterval(load, 5000);
		return () => clearInterval(t);
	});
</script>

<h2>Workers</h2>
<p class="muted">
	Un worker est une machine perso qui tourne <code>docker compose -f docker-compose.worker.yml up -d</code>. Elle apparait
	ici en attente ; une fois approuvee, elle vient chercher les photos qu'on lui reserve, calcule, et renvoie les
	resultats. Elle n'ouvre aucun port. Pour le VLM, elle recoit d'abord les photos jamais analysees, puis celles
	analysees par un modele moins fort que le sien.
</p>
{#if error}<p style="color:#f66">{error}</p>{/if}

{#if pendingList.length}
	<h3>En attente d'approbation</h3>
	<div class="list">
		{#each pendingList as p (p.code)}
			<div class="worker pending">
				<div class="head">
					<span class="dot wait"></span>
					<b>{p.hostname ?? 'machine inconnue'}</b>
					<span class="muted">{p.extractors.join(', ') || 'extracteurs inconnus'} · depuis {ago(p.since)}</span>
				</div>
				<div class="actions">
					<label>Nom <input bind:value={names[p.code]} placeholder={p.hostname ?? 'worker'} /></label>
					<button onclick={() => approve(p)}>Approuver</button>
					<button onclick={() => reject(p)}>Refuser</button>
				</div>
			</div>
		{/each}
	</div>
{/if}

{#if !workers.length && !pendingList.length}
	<p class="muted">Aucun worker. Lance le compose sur une machine : elle apparaitra ici en quelques secondes.</p>
{/if}

<div class="list">
	{#each workers as w (w.id)}
		<div class="worker" class:off={!w.online}>
			<div class="head">
				<span class="dot" class:on={w.online}></span>
				<b>{w.name}</b>
				<span class="muted">{w.online ? 'en ligne' : 'hors ligne'} · vu {ago(w.last_seen)}{w.hosts.length ? ` · ${w.hosts.join(', ')}` : ''}</span>
			</div>

			{#if w.online}
				<table class="ex">
					<thead><tr><th>extracteur</th><th>modele</th><th>reste a faire</th><th>reserve</th><th>en cours</th><th>fait</th><th>echec</th></tr></thead>
					<tbody>
						{#each w.extractors as ex}
							<tr>
								<td><label><input type="checkbox" bind:checked={picked[w.id][ex]} /> {ex}</label></td>
								<td class="muted">{w.models[ex] ?? '-'}{ex === 'vlm' && w.vlm_rank != null ? ` (rang ${w.vlm_rank})` : ''}</td>
								<td>{w.backlog[ex] ?? '-'}</td>
								<td>{w.jobs[ex]?.pending ?? 0}</td>
								<td>{w.jobs[ex]?.running ?? 0}</td>
								<td>{w.jobs[ex]?.done ?? 0}</td>
								<td>{w.jobs[ex]?.failed ?? 0}</td>
							</tr>
						{/each}
					</tbody>
				</table>
				<div class="actions">
					<label>Analyser <input type="number" min="1" max="10000" bind:value={n[w.id]} style="width:6rem" /> photos</label>
					<button onclick={() => launch(w)} disabled={busy === w.id}>Lancer l'analyse</button>
					{#if pending(w)}
						<button onclick={() => giveBack(w)}>Rendre les {pending(w)} photos non commencees</button>
					{/if}
					<span class="muted">{running(w)} en cours · {w.done_24h} faites ces 24 h{busy && busy !== w.id ? ` · reserve : ${busy}` : ''}</span>
					<button class="link" onclick={() => forget(w)}>Oublier</button>
				</div>
			{:else}
				<p class="muted">
					Lance le worker sur la machine pour le voir apparaitre.
					{#if pending(w)}Il a encore {pending(w)} photo(s) reservee(s) ; elles retournent a la file commune apres 6 h d'absence.{/if}
					<button class="link" onclick={() => forget(w)}>Oublier</button>
				</p>
			{/if}
		</div>
	{/each}
</div>

<style>
	.list { display: flex; flex-direction: column; gap: 0.8rem; max-width: 1000px; }
	.worker { background: #1c1c1c; border-radius: 6px; padding: 0.8rem 1rem; }
	.worker.off { opacity: 0.7; }
	.head { display: flex; gap: 0.6rem; align-items: center; margin-bottom: 0.5rem; }
	.dot { width: 10px; height: 10px; border-radius: 50%; background: #555; }
	.dot.on { background: #4c4; }
	.dot.wait { background: #ec3; }
	.worker.pending { border: 1px solid #665; }
	h3 { margin: 1rem 0 0.4rem; }
	button.link { background: none; border: none; color: #9cf; padding: 0; text-decoration: underline; }
	table.ex { border-collapse: collapse; margin: 0.4rem 0 0.6rem; }
	.ex td, .ex th { padding: 0.25rem 0.8rem 0.25rem 0; border-bottom: 1px solid #333; text-align: left; font-size: 0.9rem; }
	.actions { display: flex; gap: 0.8rem; align-items: center; flex-wrap: wrap; }
	code { background: #222; padding: 0 0.3rem; border-radius: 3px; }
</style>
