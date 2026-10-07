// Selection de photos pour l'export (page Photos -> page Export), memorisee dans le navigateur : elle survit aux
// changements de filtre, de page et aux rechargements. L'ordre d'ajout est conserve.
import { browser } from '$app/environment';
import { SvelteSet } from 'svelte/reactivity';

const KEY = 'photoflow.selection';

function load(): string[] {
	try {
		const v = JSON.parse(localStorage.getItem(KEY) ?? '[]');
		return Array.isArray(v) ? v.filter((x) => typeof x === 'string') : [];
	} catch {
		return [];
	}
}

export const selection = new SvelteSet<string>(browser ? load() : []);

function save() {
	if (browser) localStorage.setItem(KEY, JSON.stringify([...selection]));
}

export function setSelected(ids: string[], on: boolean) {
	for (const id of ids) on ? selection.add(id) : selection.delete(id);
	save();
}

export function toggleSelected(id: string) {
	setSelected([id], !selection.has(id));
}

export function clearSelection() {
	selection.clear();
	save();
}

// Un autre onglet a change la selection.
if (browser) {
	addEventListener('storage', (e) => {
		if (e.key !== KEY) return;
		selection.clear();
		for (const id of load()) selection.add(id);
	});
}
