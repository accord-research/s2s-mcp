/** Resolve with `p`, or reject with `message` once `ms` elapse first.
 *  Hosts cap how long a widget-initiated tool call may run and do not always
 *  reject when they give up (ChatGPT left the card on its skeleton for good),
 *  so the widget keeps its own clock. */
export function withTimeout<T>(p: Promise<T>, ms: number, message: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const t = setTimeout(() => reject(new Error(message)), ms);
    p.then((v) => { clearTimeout(t); resolve(v); }, (e) => { clearTimeout(t); reject(e); });
  });
}
