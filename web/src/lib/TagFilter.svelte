<script lang="ts">
	// Filtre par tags : pastilles des tags choisis + saisie avec autocompletion. Les propositions viennent des
	// photos qui passent deja les autres filtres, avec leur nombre : on n'affiche jamais un tag qui donnerait 0.
	import { api, type Facet } from '$lib/api';

	let {
		selected,
		filters,
		onchange
	}: {
		selected: string[];
		filters: Record<string, string | string[]>;
		onchange: (tags: string[]) => void;
	} = $props();

	let text = $state('');
	let open = $state(false);
	let active = $state(0);
	let options = $state<Facet[]>([]);
	let input: HTMLInputElement;
	let timer: ReturnType<typeof setTimeout> | undefined;
	let seq = 0;

	function fetchOptions() {
		clearTimeout(timer);
		timer = setTimeout(async () => {
			const mine = ++seq;
			try {
				const r = await api.tags({ ...filters, tag: selected, prefix: text, limit: 12 });
				if (mine === seq) {
					options = r.items;
					active = 0;
				}
			} catch {
				options = [];
			}
		}, 120);
	}

	function add(tag: string) {
		const t = tag.trim().toLowerCase();
		if (t && !selected.includes(t)) onchange([...selected, t]);
		text = '';
		open = false;
	}

	function remove(tag: string) {
		onchange(selected.filter((t) => t !== tag));
	}

	function onkeydown(e: KeyboardEvent) {
		if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
			e.preventDefault();
			if (!open) {
				open = true;
				fetchOptions();
				return;
			}
			const d = e.key === 'ArrowDown' ? 1 : -1;
			active = (active + d + options.length) % Math.max(options.length, 1);
		} else if (e.key === 'Enter') {
			// Entree choisit la proposition en surbrillance ; sans proposition, ne fait rien (pas de tag a 0 resultat).
			if (open && options[active]) {
				e.preventDefault();
				add(options[active].value);
			} else if (text) {
				e.preventDefault();
			}
		} else if (e.key === 'Escape') {
			open = false;
		} else if (e.key === 'Backspace' && !text && selected.length) {
			remove(selected[selected.length - 1]);
		}
	}
</script>

<div class="tagfilter" class:focus={open}>
	{#each selected as t (t)}
		<span class="chip">{t}<button type="button" aria-label={`retirer ${t}`} onclick={() => remove(t)}>×</button></span>
	{/each}
	<input
		bind:this={input}
		bind:value={text}
		placeholder={selected.length ? 'autre tag…' : 'tags…'}
		size="12"
		role="combobox"
		aria-expanded={open}
		aria-controls="tag-options"
		aria-autocomplete="list"
		oninput={() => {
			open = true;
			fetchOptions();
		}}
		onfocus={() => {
			open = true;
			fetchOptions();
		}}
		onblur={() => setTimeout(() => (open = false), 150)}
		{onkeydown}
	/>
	{#if open && options.length}
		<ul id="tag-options" role="listbox">
			{#each options as o, i (o.value)}
				<li role="option" aria-selected={i === active} class:active={i === active}>
					<button type="button" onmousedown={(e) => e.preventDefault()} onclick={() => { add(o.value); input.focus(); }}>
						{o.value}<span class="n">{o.count}</span>
					</button>
				</li>
			{/each}
		</ul>
	{/if}
</div>

<style>
	.tagfilter {
		position: relative;
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.25rem;
		background: #222;
		border: 1px solid #444;
		border-radius: 4px;
		padding: 0.15rem 0.3rem;
		min-width: 12rem;
	}
	.tagfilter.focus {
		border-color: #9cf;
	}
	.tagfilter input {
		border: none;
		background: transparent;
		padding: 0.25rem 0.2rem;
		flex: 1;
		min-width: 6rem;
		outline: none;
	}
	.chip {
		display: inline-flex;
		align-items: center;
		gap: 0.2rem;
		background: #2d4a63;
		border-radius: 999px;
		padding: 0.1rem 0.25rem 0.1rem 0.6rem;
		font-size: 0.85rem;
	}
	.chip button {
		border: none;
		background: transparent;
		padding: 0 0.3rem;
		color: #cde;
		line-height: 1;
	}
	ul {
		position: absolute;
		top: 100%;
		left: 0;
		z-index: 10;
		margin: 0.2rem 0 0;
		padding: 0.2rem 0;
		list-style: none;
		background: #1c1c1c;
		border: 1px solid #444;
		border-radius: 4px;
		min-width: 100%;
		max-height: 20rem;
		overflow-y: auto;
		box-shadow: 0 4px 16px #0008;
	}
	li button {
		display: flex;
		justify-content: space-between;
		gap: 1rem;
		width: 100%;
		border: none;
		border-radius: 0;
		background: transparent;
		text-align: left;
		white-space: nowrap;
	}
	li.active button,
	li button:hover {
		background: #2a2a2a;
	}
	.n {
		color: #999;
		font-size: 0.8rem;
	}
</style>
