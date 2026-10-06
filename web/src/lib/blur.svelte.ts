// Interrupteur « flouter » commun a toutes les pages (barre du haut), memorise dans le navigateur.
import { browser } from '$app/environment';

const KEY = 'photoflow.blur';

export const blur = $state({ on: browser ? localStorage.getItem(KEY) !== '0' : true });

export function setBlur(on: boolean) {
	blur.on = on;
	if (browser) localStorage.setItem(KEY, on ? '1' : '0');
}
