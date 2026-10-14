/**
 * Heure affichée par Atlas.
 *
 * Le décalage du fuseau (ex. Africa/Casablanca) est calculé par le serveur Windows avec une
 * base de fuseaux à jour, puis appliqué ici. On ne se fie pas aux règles du navigateur :
 * celles d'un ancien Mac (High Sierra) peuvent être périmées (le Maroc est revenu à GMT le
 * 20 septembre 2026, ce qu'un vieux navigateur ignore et affiche avec une heure d'avance).
 */

let offsetMin: number | null = null;
let fallbackTz: string | undefined;

export function setClockOffset(minutes: number | null, tz?: string): void {
  offsetMin = typeof minutes === "number" && isFinite(minutes) ? minutes : null;
  fallbackTz = tz;
}

export function clockOffsetKnown(): boolean {
  return offsetMin !== null;
}

/** Formate un instant (millisecondes UTC) à l'heure du fuseau d'Atlas. */
export function fmt(ms: number, opts: Intl.DateTimeFormatOptions): string {
  if (offsetMin !== null) {
    try {
      return new Intl.DateTimeFormat("fr-FR", { ...opts, timeZone: "UTC" }).format(new Date(ms + offsetMin * 60000));
    } catch {
      /* repli ci-dessous */
    }
  }
  try {
    return new Intl.DateTimeFormat("fr-FR", { ...opts, timeZone: fallbackTz }).format(new Date(ms));
  } catch {
    return new Intl.DateTimeFormat("fr-FR", opts).format(new Date(ms));
  }
}

/** Clé « AAAA-MM-JJ » du jour courant dans le fuseau d'Atlas. */
export function dayKey(ms: number): string {
  return fmt(ms, { year: "numeric", month: "2-digit", day: "2-digit" });
}
