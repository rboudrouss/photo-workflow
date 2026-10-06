import adapter from '@sveltejs/adapter-node';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	preprocess: vitePreprocess(),
	kit: {
		adapter: adapter(),
		// Le controle d'origine est fait dans hooks.server.ts (meme regle, mais il sait lire les en-tetes du
		// reverse proxy et laisser passer les workers, qui n'envoient que du JSON).
		csrf: { checkOrigin: false }
	}
};

export default config;
