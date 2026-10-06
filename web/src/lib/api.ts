import { env } from '$env/dynamic/public';

/** Base de l'API, accessible depuis le navigateur. */
export const API = (env.PUBLIC_API_BASE ?? 'http://localhost:8000').replace(/\/$/, '');

export function media(path: string): string {
	return path.startsWith('http') ? path : API + path;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
	const r = await fetch(API + path, { ...init, headers: { 'content-type': 'application/json', ...(init?.headers ?? {}) } });
	if (!r.ok) throw new Error(`${r.status} ${r.statusText} sur ${path}`);
	return (await r.json()) as T;
}

export interface Media {
	thumb: string;
	web: string;
	original: string;
}

export interface PhotoSummary {
	id: string;
	filename: string;
	width?: number;
	height?: number;
	title: string | null;
	score?: number | null;
	distance?: number;
	nudity_level?: string | null;
	media: Media;
}

export const NUDITY_LEVELS = ['aucune', 'suggestive', 'partielle', 'integrale'] as const;
export function isExplicit(level?: string | null): boolean {
	return level === 'partielle' || level === 'integrale';
}

export interface Caption {
	id: string;
	source: string;
	title: string | null;
	description: string | null;
	data: Record<string, any> | null;
	updated_at: string | null;
}

export interface FaceOut {
	id: string;
	bbox: number[];
	det_score: number;
	age: number | null;
	gender: string | null;
	cluster_id: number | null;
	person_id: string | null;
	person_name: string | null;
	crop: string;
}

export interface PhotoDetail extends PhotoSummary {
	rel_path: string;
	nudity_source: string | null;
	format: string | null;
	bytes: number;
	ingested_at: string;
	captions: Caption[];
	extractions: { extractor: string; version: number; model: string | null; data: any; created_at: string }[];
	faces: FaceOut[];
	similar: PhotoSummary[];
	duplicates: PhotoSummary[];
	jobs: { extractor: string; status: string; error: string | null }[];
	series: { id: number; name: string | null; size: number; members: SeriesPhoto[] } | null;
}

export interface SeriesPhoto extends PhotoSummary {
	decade?: string | null;
}

export interface SeriesSummary {
	id: number;
	name: string | null;
	size: number;
	samples: SeriesPhoto[];
	decade: string | null;
	scene: string | null;
	persons: string[];
	nudity_max: string | null;
}

export interface SeriesDetail {
	id: number;
	name: string | null;
	notes: string | null;
	size: number;
	summary: {
		decades: { value: string; count: number }[];
		scenes: { value: string; count: number }[];
		persons: { name: string; photos: number }[];
		unnamed_clusters: { cluster_id: number; photos: number }[];
		nudity_max: string | null;
	};
	photos: SeriesPhoto[];
	edges: { a: string; b: string; score: number; reasons: Record<string, any> }[];
}

export interface Cluster {
	cluster_id: number;
	faces: number;
	photos: number;
	person_name: string | null;
	samples: string[];
}

export const api = {
	stats: () => call<any>('/api/stats'),
	facets: () => call<{ types: { value: string; count: number }[]; scenes: { value: string; count: number }[]; decades: { value: string; count: number }[] }>('/api/facets'),
	photos: (params: Record<string, string | number | undefined>) => {
		const q = new URLSearchParams();
		for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== '') q.set(k, String(v));
		return call<{ items: PhotoSummary[]; page: number; page_size: number; total: number | null }>('/api/photos?' + q);
	},
	photo: (id: string) => call<PhotoDetail>(`/api/photos/${id}`),
	saveCaption: (id: string, body: { title?: string; description?: string; data?: any }) =>
		call<Caption>(`/api/photos/${id}/caption`, { method: 'PUT', body: JSON.stringify(body) }),
	setNudity: (id: string, level: string) =>
		call<any>(`/api/photos/${id}/nudity`, { method: 'PUT', body: JSON.stringify({ level }) }),
	fiche: (id: string) => call<{ title: string; description: string; category: string | null; tags: string[] }>(`/api/photos/${id}/fiche`),
	series: (page = 1) => call<{ items: SeriesSummary[]; total: number }>(`/api/series?page=${page}&page_size=40`),
	serie: (id: number) => call<SeriesDetail>(`/api/series/${id}`),
	renameSeries: (id: number, name: string) =>
		call<any>(`/api/series/${id}`, { method: 'PUT', body: JSON.stringify({ name }) }),
	propagate: (id: number, data: Record<string, any>, only_if_unknown: boolean) =>
		call<{ photos: number; updated: number }>(`/api/series/${id}/propagate`, {
			method: 'POST',
			body: JSON.stringify({ data, only_if_unknown })
		}),
	clusters: () => call<Cluster[]>('/api/faces/clusters'),
	cluster: (id: number) => call<{ cluster_id: number; faces: any[] }>(`/api/faces/clusters/${id}`),
	nameCluster: (id: number, name: string) =>
		call<any>(`/api/faces/clusters/${id}/person`, { method: 'PUT', body: JSON.stringify({ name }) })
};
