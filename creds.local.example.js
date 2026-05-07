/**
 * Retired local credential template.
 *
 * The current production path does not build local Frontier/ScrapeGW/VPS chain
 * nodes and does not require Sparkle-local credentials. Residential supply
 * URLs live only in the VPS Sub-Store runtime.
 *
 * Keep this file as a placeholder so older local scripts fail closed instead
 * of encouraging new credential files.
 */

(function noLocalCredsRequired() {
  if (typeof globalThis === "undefined") return;
  globalThis.__creds = globalThis.__creds || {};
})();
