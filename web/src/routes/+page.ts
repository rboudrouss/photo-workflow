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
		page: Math.max(1, Number(get('page')) || 1)
	};
	try {
		const r = await api.photos({ ...params, page_size: PAGE_SIZE });
		return { params, items: r.items, total: r.total, error: '' };
	} catch (e: any) {
		return { params, items: [], total: null, error: e.message as string };
	}
};
