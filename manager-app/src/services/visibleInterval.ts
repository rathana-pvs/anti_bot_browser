/**
 * Like setInterval, but ticks are skipped while the page is hidden (a background tab,
 * a minimised window, a locked phone), so polling costs nothing nobody can see.
 * Coming back runs one tick at once if any were skipped. Returns a function that stops it.
 */
export function setVisibleInterval(tick: () => void, ms: number,
  doc: Pick<Document, 'hidden' | 'addEventListener' | 'removeEventListener'> = document): () => void {
  let missed = false;
  const timer = setInterval(() => {
    if (doc.hidden) missed = true;
    else tick();
  }, ms);
  const onVisibility = () => {
    if (!doc.hidden && missed) {
      missed = false;
      tick();
    }
  };
  doc.addEventListener('visibilitychange', onVisibility);
  return () => {
    clearInterval(timer);
    doc.removeEventListener('visibilitychange', onVisibility);
  };
}
