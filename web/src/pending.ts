/** Copy for the card while the server is still computing an outlook. */
export function formatElapsed(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const m = Math.floor(s / 60);
  return m ? `${m} min ${s % 60} s` : `${s} s`;
}

export function pendingMessage(elapsedSeconds: number, method?: string): string {
  const what = method ? `the ${method} outlook` : "the outlook";
  return `Computing ${what}… ${formatElapsed(elapsedSeconds)} elapsed. A first run for a region fetches observations and models and can take up to half an hour; the card fills in by itself, and the result is kept for next time.`;
}
