/**
 * Deux roles cote serveur Node (adapter-node), pour n'exposer qu'une seule origine publique :
 *  1. mot de passe (Basic) sur tout le site si UI_USER/UI_PASSWORD sont definis, SAUF /api/worker/* qui est
 *     reserve aux workers distants (jeton verifie par l'API) ;
 *  2. relais de /api/* et /media/* vers l'API interne (API_INTERNAL, ex. http://api:8000), en flux, pour
 *     que le navigateur ne parle qu'au site (pas de CORS, pas de second domaine).
 * En dev (vite), API_INTERNAL n'est pas defini et le navigateur appelle l'API directement (PUBLIC_API_BASE).
 */
import type { Handle } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { timingSafeEqual } from 'node:crypto';

const PROXIED = ['/api/', '/media/'];
const WORKER_PREFIX = '/api/worker/';
const SAFE = new Set(['GET', 'HEAD', 'OPTIONS']);

/** Origine publique du site : ORIGIN si defini, sinon deduite des en-tetes (Traefik : x-forwarded-*). */
function expectedOrigin(request: Request): string {
	if (env.ORIGIN) return env.ORIGIN.replace(/\/$/, '');
	const h = request.headers;
	const proto = h.get('x-forwarded-proto')?.split(',')[0].trim() || 'http';
	const host = h.get('x-forwarded-host')?.split(',')[0].trim() || h.get('host') || '';
	return `${proto}://${host}`;
}

/** Meme regle que SvelteKit : un envoi de formulaire (multipart, urlencoded, text/plain) qui modifie l'etat doit
 * venir de notre origine, sinon un site tiers pourrait poster avec le mot de passe memorise par le navigateur.
 * Le JSON n'est pas concerne : un formulaire HTML ne peut pas en envoyer sans requete preflight. */
function crossSiteForm(request: Request): boolean {
	if (SAFE.has(request.method)) return false;
	const ct = (request.headers.get('content-type') ?? '').split(';')[0].trim().toLowerCase();
	if (!['multipart/form-data', 'application/x-www-form-urlencoded', 'text/plain'].includes(ct)) return false;
	const site = request.headers.get('sec-fetch-site');
	if (site === 'same-origin') return false;
	return (request.headers.get('origin') ?? '') !== expectedOrigin(request);
}

function same(a: string, b: string): boolean {
	const x = Buffer.from(a), y = Buffer.from(b);
	return x.length === y.length && timingSafeEqual(x, y);
}

function authorized(request: Request): boolean {
	const user = env.UI_USER, pass = env.UI_PASSWORD;
	if (!user || !pass) return true; // pas de mot de passe configure (dev)
	const h = request.headers.get('authorization') ?? '';
	if (!h.startsWith('Basic ')) return false;
	const [u, ...rest] = Buffer.from(h.slice(6), 'base64').toString().split(':');
	return same(u, user) && same(rest.join(':'), pass);
}

export const handle: Handle = async ({ event, resolve }) => {
	const { pathname, search } = event.url;
	const isWorker = pathname.startsWith(WORKER_PREFIX);

	if (crossSiteForm(event.request)) {
		return new Response('Cross-site POST form submissions are forbidden', { status: 403 });
	}

	if (!isWorker && !authorized(event.request)) {
		return new Response('Authentification requise', {
			status: 401,
			headers: { 'WWW-Authenticate': 'Basic realm="photoflow", charset="UTF-8"' }
		});
	}

	const target = env.API_INTERNAL?.replace(/\/$/, '');
	if (target && PROXIED.some((p) => pathname.startsWith(p))) {
		const headers = new Headers(event.request.headers);
		headers.delete('host');
		headers.delete('authorization'); // le mot de passe du site ne va pas plus loin ; les workers ne passent pas ici
		if (isWorker) headers.set('authorization', event.request.headers.get('authorization') ?? '');
		const init: RequestInit & { duplex?: 'half' } = {
			method: event.request.method,
			headers,
			body: event.request.method === 'GET' || event.request.method === 'HEAD' ? undefined : event.request.body,
			duplex: 'half',
			redirect: 'manual'
		};
		const upstream = await fetch(target + pathname + search, init);
		const out = new Headers(upstream.headers);
		out.delete('content-encoding');
		out.delete('content-length');
		return new Response(upstream.body, { status: upstream.status, headers: out });
	}

	return resolve(event);
};
