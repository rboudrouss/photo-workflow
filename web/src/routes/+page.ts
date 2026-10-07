// La recherche vit dans l'URL : partageable, et le retour arriere retrouve la meme page (resultats charges avant
// l'affichage, donc SvelteKit peut restaurer la position de defilement).
import { api, PAGE_SIZE } from '$lib/api';
import type { PageLoad } from './$types';

export const ssr = false;

export const load: PageLoad = async ({ url }) => {
	const get = (k: string) => url.searchParams.get(k) ?? '';
	const params = {
		q: get('q'),
		mode: (get('mode') || 'text') as 'text' | 'vector',
		scene: get('scene'),
		decade: get('decade'),
		nudity: (get('nudity') || 'all') as 'all' | 'exclude' | 'only',
		type_objet: get('type_objet'),
		vlm: get('vlm'),
		statut: get('statut'),
		tag: url.searchParams.getAll('tag'),
		sort: (get('sort') || 'recent') as 'recent' | 'vente' | 'instagram',
		page: Math.max(1, Number(get('page')) || 1)
	};
	// Tags pour affiner : les plus frequents dans les resultats courants (facultatif, ne bloque pas la grille).
	const { page, sort, ...filters } = params;
	const refine = api.tags({ ...filters, limit: 15 }).then((r) => r.items, () => []);
	try {
		const r = await api.photos({ ...params, page_size: PAGE_SIZE });
		return { params, filters, items: r.items, total: r.total, refine: await refine, error: '' };
	} catch (e: any) {
		return { params, filters, items: [], total: null, refine: [], error: e.message as string };
	}
};
